import os
import time
import gradio as gr
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db, engine, Base
from app.models import Record, InBodyRecord, User
from typing import List, Optional
from app.inbody_ocr import extract_key_values, format_key_values, upstage_ocr_from_bytes, update_user_inbody, build_demo
from ml.hybrid_classifier import HybridBodyTypeClassifier
from ml.diet_recommendation import recommend_diet_unified
from ml.inbody_scoring import get_comprehensive_evaluation

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
    allow_origins=[
        "http://localhost:3000",  # Next.js 개발 서버
        "http://localhost:3001",  # 대체 포트
    ],
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


class InBodyInput(BaseModel):
    """인바디 데이터 입력 스키마"""
    height: float
    weight: float
    body_fat_pct: float
    skeletal_muscle_mass: float
    bmr: float
    visceral_fat_level: int
    age: Optional[int] = None
    gender: str  # "M" or "F"
    birth_year: Optional[int] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "height": 175,
                "weight": 70,
                "body_fat_pct": 18,
                "skeletal_muscle_mass": 33,
                "bmr": 1600,
                "visceral_fat_level": 5,
                "gender": "M",
                "age": 30
            }
        }


class BodyTypeResponse(BaseModel):
    """체형 분석 결과 (하이브리드)"""
    primary_type: str  # "마른형", "표준형", "과체중형", "근육형"
    secondary_tags: List[str]  # ["건강", "근육질"] 등
    display_name: str  # "표준형 (건강)"
    classification_method: str  # "rule" or "ml"
    bmi: float
    muscle_ratio: float
    health_evaluation: dict
    recommended_diet: dict
    meal_plan_example: Optional[dict] = None


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


# 전역 변수: 하이브리드 분류기 (서버 시작 시 1회 로드)
classifier = None

# DB 테이블 생성
@app.on_event("startup")
def startup_event():
    """애플리케이션 시작 시 DB 테이블 생성 및 분류기 로드"""
    global classifier
    print("🚀 FastAPI 서버 시작 중...")
    time.sleep(3)  # DB가 준비될 때까지 대기
    Base.metadata.create_all(bind=engine)
    print("✅ 데이터베이스 초기화 완료")
    
    # 하이브리드 분류기 로드
    try:
        classifier = HybridBodyTypeClassifier(
            male_model_path="models/inbody_male_k4_model.joblib",
            female_model_path="models/inbody_female_k4_model.joblib"
        )
        print("✅ 하이브리드 체형 분류기 로드 완료")
        print("   - 남성 모델: models/inbody_male_k4_model.joblib")
        print("   - 여성 모델: models/inbody_female_k4_model.joblib")
    except FileNotFoundError as e:
        print(f"⚠️  모델 파일을 찾을 수 없습니다: {e}")
        print("   K=4 모델이 없으면 규칙 기반만 사용됩니다.")

@app.get("/")
def root():
    """메인 페이지"""
    return {
        "status": "success",
        "message": "식단 계획 AI API 서버가 정상 작동 중입니다!",
        "version": "1.0.0"
    }


@app.post("/api/record", response_model=DietRecordResponse)
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


@app.get("/api/mypage", response_model=List[MyPageResponse])
def get_mypage(limit: int = 10, db: Session = Depends(get_db)):
    """
    마이페이지 - 사용자의 식단 기록 조회
    """
    records = db.query(Record).order_by(Record.record_created_at.desc()).limit(limit).all()
    
    return [
        {
            "id": record.record_id,
            "goal_calories": record.goal_calories or 0,  # 기존 데이터 호환
            "food_name": record.food_name,
            "calories": record.food_calories,  # food_calories로 수정
            "created_at": record.record_created_at.isoformat()
        }
        for record in records
    ]


@app.post("/api/analyze-inbody", response_model=BodyTypeResponse)
def analyze_inbody(data: InBodyInput, db: Session = Depends(get_db)):
    """
    하이브리드 인바디 분석 (규칙 기반 + ML)
    
    사용자의 인바디 측정 데이터를 입력받아:
    1. 하이브리드 체형 분류 (1차: 4가지 체형, 2차: 세부 태그)
    2. 건강 상태 종합 평가
    3. 맞춤형 식단 추천 (4가지 체형 기반)
    4. DB에 기록 저장
    """
    if classifier is None:
        # 분류기 없어도 작동 (규칙 기반만 사용)
        print("⚠️ 분류기 미로드, 규칙 기반만 사용")
    
    # 1. 하이브리드 체형 분류
    classification = classifier.classify(
        gender=data.gender,
        height=data.height,
        weight=data.weight,
        body_fat_pct=data.body_fat_pct,
        skeletal_muscle_mass=data.skeletal_muscle_mass,
        bmr=data.bmr,
        visceral_fat_level=data.visceral_fat_level,
        age=data.age,
        birth_year=data.birth_year
    ) if classifier else {
        "primary_type": "표준형",
        "secondary_tags": [],
        "display_name": "표준형",
        "classification_method": "rule",
        "bmi": 0,
        "muscle_ratio": 0
    }
    
    # 2. 건강 상태 종합 평가
    health_eval = get_comprehensive_evaluation(
        height=data.height,
        weight=data.weight,
        body_fat_pct=data.body_fat_pct,
        skeletal_muscle_mass=data.skeletal_muscle_mass,
        visceral_fat_level=data.visceral_fat_level,
        bmr=data.bmr,
        gender=data.gender,
        age=data.age
    )
    
    # 3. 통일된 식단 추천 (4가지 체형)
    diet = recommend_diet_unified(
        primary_type=classification["primary_type"],
        gender=data.gender,
        bmr=data.bmr,
        activity_level="moderate",
        secondary_tags=classification["secondary_tags"]
    )
    
    # 4. DB에 저장
    inbody_record = InBodyRecord(
        user_id=1,  # TODO: 실제 사용자 ID
        height=data.height,
        weight=data.weight,
        body_fat_pct=data.body_fat_pct,
        skeletal_muscle_mass=data.skeletal_muscle_mass,
        bmr=data.bmr,
        visceral_fat_level=data.visceral_fat_level,
        inbody_score=health_eval.get("estimated_score"),
        predicted_cluster=None,  # 하이브리드는 cluster_id 없음
        cluster_name=classification["display_name"]
    )
    db.add(inbody_record)
    db.commit()
    db.refresh(inbody_record)
    
    return {
        "primary_type": classification["primary_type"],
        "secondary_tags": classification["secondary_tags"],
        "display_name": classification["display_name"],
        "classification_method": classification["classification_method"],
        "bmi": classification["bmi"],
        "muscle_ratio": classification["muscle_ratio"],
        "health_evaluation": health_eval,
        "recommended_diet": diet,
        "meal_plan_example": None  # TODO: 추가 가능
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
            "predicted_cluster": record.predicted_cluster,
            "cluster_name": record.cluster_name,
            "created_at": record.created_at.isoformat()
        }
        for record in records
    ]


@app.post("/api/inbody-ocr", response_model=InBodyOcrResponse)
async def inbody_ocr(
    user_id: int = Form(...),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="loaclhost", port=8000)
