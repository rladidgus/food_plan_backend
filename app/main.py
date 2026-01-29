import os
import time
import logging
from datetime import date, datetime, timezone
import gradio as gr
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db, engine, Base
from app.models import Record, InBodyRecord, User, UserProfile, UserGoal, DailyActivity
from typing import List, Optional
from app.inbody_ocr import extract_key_values, format_key_values, upstage_ocr_from_bytes, update_user_inbody, build_demo
from app.inbody import InbodyInput, BodyTypeResult, classify_body_type

# 환경 변수에서 DB 정보 가져오기
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")

# FastAPI 앱 생성
app = FastAPI(title="식단 계획 AI API")
logger = logging.getLogger("app.sync")

# Gradio OCR 데모 마운트 (카메라 기능 제공)
ocr_demo = build_demo()
app = gr.mount_gradio_app(app, ocr_demo, path="/ocr-web")

# CORS 설정 (Next.js 프론트엔드와 통신)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 및 테스트를 위해 모든 출처 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic 모델 (요청/응답 스키마)
class DietRecordRequest(BaseModel):
    """식단 기록 요청 모델"""
    goal_calories: int

class DietRecordResponse(BaseModel):
    """식단 기록 응답 모델"""
    food_name: str
    calories: int
    message: str

class MyPageResponse(BaseModel):
    """마이페이지 식단 기록 응답 모델"""
    id: int
    goal_calories: int
    food_name: str
    calories: int
    created_at: str
    height: Optional[float] = None
    weight: Optional[float] = None
    skeletal_muscle_mass: Optional[float] = None
    body_fat_pct: Optional[float] = None

    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    """사용자 기본 정보 응답"""
    user_number: int
    id: str
    username: str
    email: Optional[str] = None
    role: Optional[str] = None

    class Config:
        from_attributes = True


class UserGoalResponse(BaseModel):
    """사용자 목표 응답"""
    goal_id: int
    goal_type: str
    target_calory: Optional[float] = None
    target_protein: Optional[float] = None
    target_carb: Optional[float] = None
    target_fat: Optional[float] = None
    target_macros: Optional[str] = None
    target_pace: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class InBodyHistoryResponse(BaseModel):
    """인바디 히스토리 응답"""
    inbody_id: int
    measurement_date: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    body_fat_pct: Optional[float] = None
    skeletal_muscle_mass: Optional[float] = None
    predicted_cluster: Optional[int] = None
    cluster_name: Optional[str] = None
    values: Optional[dict] = None
    created_at: str
    
    class Config:
        from_attributes = True


class InBodyOcrResponse(BaseModel):
    """인바디 OCR 응답"""
    raw_text: str
    text: str
    values: dict
    updated: bool


class DailyActivityIn(BaseModel):
    user_number: int
    activity_date: date
    activity_type: str
    steps: Optional[int] = None
    active_kcal: Optional[float] = None
    total_kcal: Optional[float] = None
    workout_minutes: Optional[int] = None
    distance_meters: Optional[float] = None
    activity_source: Optional[str] = None
    activity_source_device: Optional[str] = None
    activity_source_app: Optional[str] = None
    activity_source_record_id: Optional[str] = None
    activity_created_at: Optional[datetime] = None
    activity_updated_at: Optional[datetime] = None


class DailyActivityUpsertResult(BaseModel):
    activity_id: int
    created: bool

    class Config:
        from_attributes = True


def _normalize_activity(item: DailyActivityIn) -> DailyActivityIn:
    data = item.model_dump()

    if not data["activity_type"] or not data["activity_type"].strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="activity_type은 비어 있을 수 없습니다.",
        )

    data["activity_type"] = data["activity_type"].strip().lower()

    if data.get("activity_source"):
        allowed_sources = {"healthkit", "health_connect", "manual"}
        if data["activity_source"] not in allowed_sources:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="activity_source는 healthkit/health_connect/manual 중 하나여야 합니다.",
            )

    for field in ("steps", "active_kcal", "total_kcal", "workout_minutes", "distance_meters"):
        value = data.get(field)
        if value is not None and value < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field}는 음수가 될 수 없습니다.",
            )

    if data.get("activity_created_at") and data["activity_created_at"].tzinfo is None:
        data["activity_created_at"] = data["activity_created_at"].replace(tzinfo=timezone.utc)
    if data.get("activity_updated_at") and data["activity_updated_at"].tzinfo is None:
        data["activity_updated_at"] = data["activity_updated_at"].replace(tzinfo=timezone.utc)

    return DailyActivityIn(**data)


class BodyTypeFromUserRequest(BaseModel):
    user_number: int


# DB 테이블 생성
@app.on_event("startup")
def startup_event():
    """애플리케이션 시작 시 DB 테이블 생성"""
    print("🚀 FastAPI 서버 시작 중...")
    time.sleep(3)  # DB가 준비될 때까지 대기
    Base.metadata.create_all(bind=engine)
    print("✅ 데이터베이스 초기화 완료")

@app.get("/")
def root():
    """메인 페이지"""
    return {
        "status": "success",
        "message": "식단 계획 AI API 서버가 정상 작동 중입니다!",
        "version": "1.0.0"
    }


class UserCreate(BaseModel):
    id: str
    username: str
    password: str

class UserLogin(BaseModel):
    id: str
    password: str

class AuthResponse(BaseModel):
    user_number: int
    id: str
    username: str
    message: str

@app.post("/api/register", response_model=AuthResponse)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """회원가입"""
    # 아이디 중복 확인
    existing_user = db.query(User).filter(User.id == user_data.id).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 존재하는 아이디입니다."
        )
    
    new_user = User(
        id=user_data.id,
        username=user_data.username,
        password=user_data.password
        # 나머지 필드(height, weight 등)는 nullable=True이므로 생략 가능
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {
        "user_number": new_user.user_number,
        "id": new_user.id,
        "username": new_user.username,
        "message": "회원가입이 완료되었습니다."
    }

@app.post("/api/login", response_model=AuthResponse)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    """로그인"""
    # 로그인 아이디(id)로 사용자 검색
    user = db.query(User).filter(User.id == user_data.id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    
    # 비밀번호 검증 (평문 비교)
    if user_data.password != user.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    
    return {
        "user_number": user.user_number,
        "id": user.id,
        "username": user.username,
        "message": "로그인 성공"
    }


@app.get("/api/user", response_model=UserResponse)
def get_user(user_number: int = 1, db: Session = Depends(get_db)):
    """사용자 기본 정보 조회"""
    user = db.query(User).filter(User.user_number == user_number).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    return {
        "user_number": user.user_number,
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
    }


@app.get("/api/user/goal", response_model=UserGoalResponse)
def get_user_goal(user_number: int = 1, db: Session = Depends(get_db)):
    """사용자 목표 조회 (최신 1건)"""
    goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="목표를 찾을 수 없습니다.")
    return {
        "goal_id": goal.goal_id,
        "goal_type": goal.goal_type,
        "target_calory": goal.target_calory,
        "target_protein": goal.target_protein,
        "target_carb": goal.target_carb,
        "target_fat": goal.target_fat,
        "target_macros": goal.target_macros,
        "target_pace": goal.target_pace,
        "start_date": goal.start_date.isoformat() if goal.start_date else None,
        "end_date": goal.end_date.isoformat() if goal.end_date else None,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
    }


@app.get("/api/inbody", response_model=Optional[InBodyHistoryResponse])
def get_latest_inbody(user_number: int = 1, db: Session = Depends(get_db)):
    """사용자 최신 인바디 기록 조회"""
    record = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )
    if not record:
        return None
    return {
        "inbody_id": record.inbody_id,
        "measurement_date": record.measurement_date.isoformat() if record.measurement_date else None,
        "height": record.height,
        "weight": record.weight,
        "body_fat_pct": record.body_fat_pct,
        "skeletal_muscle_mass": record.skeletal_muscle_mass,
        "predicted_cluster": record.predicted_cluster,
        "cluster_name": record.cluster_name,
        "values": {
            k: v for k, v in {
                "height": record.height,
                "weight": record.weight,
                "body_fat_mass": record.body_fat_mass,
                "body_fat_pct": record.body_fat_pct,
                "skeletal_muscle_mass": record.skeletal_muscle_mass,
                "bmr": record.bmr,
                "inbody_score": record.inbody_score,
            }.items() if v is not None
        },
        "created_at": record.created_at.isoformat()
    }


@app.get("/api/mypage", response_model=List[MyPageResponse])
def get_mypage_records(user_number: int = 1, limit: int = 10, db: Session = Depends(get_db)):
    """마이페이지 식단 기록 조회"""
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == user_number)
        .first()
    )

    height = latest_inbody.height if latest_inbody and latest_inbody.height is not None else (profile.height if profile else None)
    weight = latest_inbody.weight if latest_inbody and latest_inbody.weight is not None else (profile.weight if profile else None)
    skeletal_muscle_mass = (
        latest_inbody.skeletal_muscle_mass if latest_inbody and latest_inbody.skeletal_muscle_mass is not None
        else (profile.skeletal_muscle_mass if profile else None)
    )
    body_fat_pct = (
        latest_inbody.body_fat_pct if latest_inbody and latest_inbody.body_fat_pct is not None
        else (profile.body_fat_percent if profile else None)
    )

    records = (
        db.query(Record)
        .filter(Record.user_number == user_number)
        .order_by(Record.record_created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": record.record_id,
            "goal_calories": int(record.goal_calories) if record.goal_calories is not None else 0,
            "food_name": record.food_name,
            "calories": int(record.food_calories),
            "created_at": record.record_created_at.isoformat() if record.record_created_at else "",
            "height": height,
            "weight": weight,
            "skeletal_muscle_mass": skeletal_muscle_mass,
            "body_fat_pct": body_fat_pct,
        }
        for record in records
    ]


@app.post("/api/daily-activities/sync", response_model=List[DailyActivityUpsertResult])
def sync_daily_activities(
    activities: List[DailyActivityIn],
    db: Session = Depends(get_db),
):
    """
    일일 활동 데이터 업서트 (source_record_id 있으면 그 기준, 없으면 날짜+타입 기준).
    """
    results: List[DailyActivityUpsertResult] = []
    synced_at = datetime.now(timezone.utc)
    created_count = 0
    updated_count = 0
    logger.info("daily_activities_sync_start count=%s", len(activities))

    for item in activities:
        item = _normalize_activity(item)
        if item.activity_source_record_id and not item.activity_source:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="activity_source_record_id가 있으면 activity_source도 필요합니다.",
            )

        if item.activity_source_record_id:
            existing = db.query(DailyActivity).filter(
                DailyActivity.user_number == item.user_number,
                DailyActivity.activity_source == item.activity_source,
                DailyActivity.activity_source_record_id == item.activity_source_record_id,
            ).first()
        else:
            existing = db.query(DailyActivity).filter(
                DailyActivity.user_number == item.user_number,
                DailyActivity.activity_date == item.activity_date,
                DailyActivity.activity_type == item.activity_type,
                DailyActivity.activity_source_record_id.is_(None),
            ).first()

        if existing:
            for field in (
                "activity_date",
                "activity_type",
                "steps",
                "active_kcal",
                "total_kcal",
                "workout_minutes",
                "distance_meters",
                "activity_source",
                "activity_source_device",
                "activity_source_app",
                "activity_source_record_id",
                "activity_created_at",
                "activity_updated_at",
            ):
                setattr(existing, field, getattr(item, field))
            existing.activity_synced_at = synced_at
            results.append(DailyActivityUpsertResult(activity_id=existing.activity_id, created=False))
            updated_count += 1
        else:
            new_activity = DailyActivity(
                user_number=item.user_number,
                activity_date=item.activity_date,
                activity_type=item.activity_type,
                steps=item.steps,
                active_kcal=item.active_kcal,
                total_kcal=item.total_kcal,
                workout_minutes=item.workout_minutes,
                distance_meters=item.distance_meters,
                activity_source=item.activity_source,
                activity_source_device=item.activity_source_device,
                activity_source_app=item.activity_source_app,
                activity_source_record_id=item.activity_source_record_id,
                activity_created_at=item.activity_created_at,
                activity_updated_at=item.activity_updated_at,
                activity_synced_at=synced_at,
            )
            db.add(new_activity)
            db.flush()
            results.append(DailyActivityUpsertResult(activity_id=new_activity.activity_id, created=True))
            created_count += 1

    db.commit()
    logger.info(
        "daily_activities_sync_done count=%s created=%s updated=%s",
        len(activities),
        created_count,
        updated_count,
    )
    return results


@app.post("/api/health-connect/sync", response_model=List[DailyActivityUpsertResult])
def sync_health_connect_activities(
    activities: List[DailyActivityIn],
    db: Session = Depends(get_db),
):
    """
    Health Connect 동기화 전용 엔드포인트.
    activity_source를 'health_connect'로 강제한다.
    """
    normalized: List[DailyActivityIn] = []
    for item in activities:
        data = item.model_dump()
        data["activity_source"] = "health_connect"
        normalized.append(DailyActivityIn(**data))
    return sync_daily_activities(normalized, db)


@app.get("/api/inbody-history", response_model=List[InBodyHistoryResponse])
def get_inbody_history(user_number: int = 1, limit: int = 10, db: Session = Depends(get_db)):
    """
    사용자의 인바디 측정 히스토리 조회
    """
    records = db.query(InBodyRecord).filter(
        InBodyRecord.user_number == user_number
    ).order_by(InBodyRecord.created_at.desc()).limit(limit).all()
    
    return [
        {
            "inbody_id": record.inbody_id,
            "measurement_date": record.measurement_date.isoformat() if record.measurement_date else None,
            "height": record.height,
            "weight": record.weight,
            "body_fat_pct": record.body_fat_pct,
            "skeletal_muscle_mass": record.skeletal_muscle_mass,
            "predicted_cluster": record.predicted_cluster,
            "cluster_name": record.cluster_name,
            "values": {
                k: v for k, v in {
                    "height": record.height,
                    "weight": record.weight,
                    "body_fat_mass": record.body_fat_mass,
                    "body_fat_pct": record.body_fat_pct,
                    "skeletal_muscle_mass": record.skeletal_muscle_mass,
                    "bmr": record.bmr,
                    "inbody_score": record.inbody_score,
                }.items() if v is not None
            },
            "created_at": record.created_at.isoformat()
        }
        for record in records
    ]


@app.post("/api/inbody-ocr", response_model=InBodyOcrResponse)
async def inbody_ocr(
    user_number: int = Form(1),  # 기본값 1 (테스트용)
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    인바디 사진 OCR -> 핵심 항목 추출 -> users 테이블 최신값 업데이트
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 업로드 가능합니다.")

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="이미지 파일이 비어 있습니다.")

    text = upstage_ocr_from_bytes(
        content,
        filename=image.filename or "inbody.jpg",
        mime=image.content_type or "image/jpeg",
    )
    values = extract_key_values(text)
    if not values:
        return {"raw_text": text, "text": "", "values": {}, "updated": False}

    try:
        update_user_inbody(user_number, values)
    except RuntimeError as e:
        # 사용자를 찾을 수 없는 경우 등
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 업데이트 실패: {e}")

    return {"raw_text": text, "text": format_key_values(values), "values": values, "updated": True}


@app.post("/api/vision/food")
async def vision_food(image: UploadFile = File(...)):
    import tempfile
    import traceback
    from app.food_lens import decide_food_gpt_only  # 함수 내부 또는 상단에서 임포트

    print(f"▶ [API Start] /api/vision/food requested with file: {image.filename}")
    
    try:
        # 파일 임시 저장
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            content = await image.read()
            print(f"   - File size: {len(content)} bytes")
            tmp.write(content)
            tmp_path = tmp.name

        print(f"   - Temp file created at: {tmp_path}")

        # 비전 분석 로직 실행
        result = decide_food_gpt_only(tmp_path)
        print("   - Recognition successful")
        return result

    except Exception as e:
        # 에러 발생 시 콘솔에 상세 출력
        print("\n" + "="*60)
        print(f"🚨 [Error] /api/vision/food failed!")
        print(f"   - Error Message: {e}")
        print("-" * 60)
        print(traceback.format_exc())  # 에러 스택 트레이스 출력
        print("="*60 + "\n")
        
        # 클라이언트에게도 500 에러 전달
        raise HTTPException(status_code=500, detail=f"Vision API Error: {str(e)}")

    finally:
        # 임시 파일 삭제
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
            print("   - Temp file deleted.")
            
            
@app.post("/api/classify/bodytype", response_model=BodyTypeResult)
def classify_endpoint(payload: InbodyInput):
    return classify_body_type(payload)


@app.post("/api/classify/bodytype/by-user", response_model=BodyTypeResult)
def classify_by_user(payload: BodyTypeFromUserRequest, db: Session = Depends(get_db)):
    record = db.query(InBodyRecord).filter(
        InBodyRecord.user_number == payload.user_number
    ).order_by(InBodyRecord.created_at.desc()).first()

    if not record:
        raise HTTPException(status_code=404, detail="인바디 기록이 없습니다.")

    profile = db.query(UserProfile).filter(
        UserProfile.user_number == payload.user_number
    ).one_or_none()

    if not profile or not profile.gender:
        raise HTTPException(status_code=400, detail="프로필 성별 정보가 없습니다.")

    gender_raw = str(profile.gender).strip().lower()
    if gender_raw in ("m", "male", "남", "남성"):
        sex = "M"
    elif gender_raw in ("f", "female", "여", "여성"):
        sex = "F"
    else:
        raise HTTPException(status_code=400, detail="프로필 성별 정보가 올바르지 않습니다.")

    required_fields = {
        "height": record.height,
        "weight": record.weight,
        "body_fat_mass": record.body_fat_mass,
        "body_fat_pct": record.body_fat_pct,
        "skeletal_muscle_mass": record.skeletal_muscle_mass,
    }
    missing = [k for k, v in required_fields.items() if v is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"인바디 기록 값이 부족합니다: {', '.join(missing)}")

    inbody_input = InbodyInput(
        sex=sex,
        height_cm=record.height,
        weight_kg=record.weight,
        body_fat_kg=record.body_fat_mass,
        body_fat_pct=record.body_fat_pct,
        skeletal_muscle_kg=record.skeletal_muscle_mass,
        bmr_kcal=record.bmr,
    )
    result = classify_body_type(inbody_input)
    record.predicted_cluster = None
    record.cluster_name = result.stage2
    db.commit()
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
