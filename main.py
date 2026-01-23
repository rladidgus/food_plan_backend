import os
import time
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db, engine, Base
from models import Record
from typing import List

# 환경 변수에서 DB 정보 가져오기
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")

# FastAPI 앱 생성
app = FastAPI(title="식단 계획 AI API")

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


# DB 테이블 생성
@app.on_event("startup")
def startup_event():
    """애플리케이션 시작 시 DB 테이블 생성"""
    print("🚀 FastAPI 서버 시작 중...")
    time.sleep(3)  # DB가 준비될 때까지 대기
    Base.metadata.create_all(bind=engine)
    print("✅ 데이터베이스 초기화 완료")


# ===== API 엔드포인트 =====

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)