import os
import time
import gradio as gr
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from app.database import get_db, engine, Base
from app.models import Record, InBodyRecord, User
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

# Gradio OCR 데모 마운트 (카메라 기능 제공)
ocr_demo = build_demo()
app = gr.mount_gradio_app(app, ocr_demo, path="/ocr-web")

# CORS 설정 (Next.js 프론트엔드와 통신)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["localhost:3000"],  # 개발 및 테스트를 위해 모든 출처 허용
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
    created_at: str
    
    class Config:
        from_attributes = True


class InBodyOcrResponse(BaseModel):
    """인바디 OCR 응답"""
    raw_text: str
    text: str
    values: dict
    updated: bool


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


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserCreate(BaseModel):
    id: str
    username: str
    password: str

class UserLogin(BaseModel):
    id: str
    password: str

class AuthResponse(BaseModel):
    user_id: int
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
    
    # 비밀번호 해시
    hashed_password = pwd_context.hash(user_data.password)
    
    new_user = User(
        id=user_data.id,
        username=user_data.username,
        password=hashed_password
        # 나머지 필드(height, weight 등)는 nullable=True이므로 생략 가능
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {
        "user_id": new_user.user_id,
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
    
    # 비밀번호 검증
    if not pwd_context.verify(user_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    
    return {
        "user_id": user.user_id,
        "id": user.id,
        "username": user.username,
        "message": "로그인 성공"
    }
def create_diet_record(request: DietRecordRequest, db: Session = Depends(get_db)):
    """
    칼로리에 기반한 식단 기록 생성
    """
    goal = request.goal_calories
    
    # 간단한 추천 로직 (실제로는 AI 모델 사용)
    if goal < 1500:
        food_name = "닭가슴살 샐러드"
        calories = 400
    elif 1500 <= goal < 2000:
        food_name = "현미밥과 구운 연어"
        calories = 650
    elif 2000 <= goal < 2500:
        food_name = "불고기 덮밥"
        calories = 800
    else:
        food_name = "스테이크와 구운 야채"
        calories = 950
    
    # DB에 식단 기록 저장 (목표 칼로리 포함)
    record = Record(
        user_id=1,  # TODO: 실제 로그인한 사용자 ID 사용
        goal_calories=goal,
        food_name=food_name,
        food_calories=calories,
        food_protein=0.0,  # TODO: 실제 영양소 값
        food_carbs=0.0,
        food_fats=0.0
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    
    return {
        "food_name": food_name,
        "calories": calories,
        "message": f"목표 칼로리 {goal}kcal에 맞는 추천 메뉴가 기록되었습니다!"
    }


@app.get("/api/inbody-history", response_model=List[InBodyHistoryResponse])
def get_inbody_history(user_id: int = 1, limit: int = 10, db: Session = Depends(get_db)):
    """
    사용자의 인바디 측정 히스토리 조회
    """
    records = db.query(InBodyRecord).filter(
        InBodyRecord.user_id == user_id
    ).order_by(InBodyRecord.created_at.desc()).limit(limit).all()
    
    return [
        {
            "inbody_id": record.inbody_id,
            "measurement_date": record.measurement_date.isoformat() if record.measurement_date else None,
            "height": record.height,
            "weight": record.weight,
            "body_fat_pct": record.body_fat_pct,
            "skeletal_muscle_mass": record.skeletal_muscle_mass,
            "created_at": record.created_at.isoformat()
        }
        for record in records
    ]


@app.post("/api/inbody-ocr", response_model=InBodyOcrResponse)
async def inbody_ocr(
    user_id: int = Form(1),  # 기본값 1 (테스트용)
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
        update_user_inbody(user_id, values)
    except RuntimeError as e:
        # 사용자를 찾을 수 없는 경우 등
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 업데이트 실패: {e}")

    return {"raw_text": text, "text": format_key_values(values), "values": values, "updated": True}


@app.post("/api/vision/food")
async def vision_food(image: UploadFile = File(...), top_k: int = 5):
    import tempfile
    import traceback
    from app.food_lens import recognize_and_explain  # 함수 내부 또는 상단에서 임포트

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
        result = recognize_and_explain(tmp_path, top_k=top_k)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
