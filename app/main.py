import os
import traceback
import time
import logging
import time as time_module
from datetime import date, datetime, timezone, time, timedelta as dt_time
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta
from sqlalchemy import and_
from fastapi.responses import RedirectResponse
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.food_lens import decide_food_gpt_only
from app.database import get_db, engine, Base
from app.inbody import InbodyInput, BodyTypeResult, classify_body_type
from app.models import Record, InBodyRecord, User, UserProfile, FoodAnalysisResult
from typing import List, Optional
from app import models
from app.inbody_ocr import extract_key_values, format_key_values, upstage_ocr_from_bytes, update_user_inbody
from app.models import Record, InBodyRecord, User, UserProfile, UserGoal, DailyActivity
from app.goal_rules import estimate_target_calorie, normalize_activity_level, ACTIVITY_FACTORS
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client

# Supabase 클라이언트 초기화
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 환경변수가 필요합니다.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
security = HTTPBearer()

UPLOAD_DIR = Path("uploads/foods")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 환경 변수에서 DB 정보 가져오기
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")

# FastAPI 앱 생성
app = FastAPI(title="식단 계획 AI API")
logger = logging.getLogger("app.sync")

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:3000"
    ).split(",")
    if origin.strip()
]
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret")

# CORS 설정 (Next.js 프론트엔드와 통신)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션 기반 로그인 (쿠키)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=False,
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
    target_calorie: Optional[float] = None
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


class MyPageResponse(BaseModel):
    """마이페이지 식단 기록 응답 모델"""
    id: int
    goal_calories: int
    food_name: str
    calories: int
    record_created_at: str
    height: Optional[float] = None
    weight: Optional[float] = None
    skeletal_muscle_mass: Optional[float] = None
    body_fat_percent: Optional[float] = None

    class Config:
        from_attributes = True


class MyPageEnvelopeResponse(BaseModel):
    """마이페이지 응답 모델 (목표 + 식단 기록)"""
    user: Optional[UserResponse] = None
    goal: Optional[UserGoalResponse] = None
    body: Optional[dict] = None
    records: List[MyPageResponse]


class UserGoalUpdateRequest(BaseModel):
    """사용자 목표 변경 요청"""
    user_number: int
    goal_type: str
    target_calorie: Optional[float] = None



class ActivityLevelUpdateRequest(BaseModel):
    user_number: int
    activity_level: str


class InBodyHistoryResponse(BaseModel):
    """인바디 히스토리 응답"""
    inbody_id: int
    measurement_date: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    body_fat_pct: Optional[float] = None
    skeletal_muscle_mass: Optional[float] = None
    predicted_classify: Optional[int] = None
    classify_name: Optional[str] = None
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
    activity_level: Optional[str] = None
    activity_level_options: Optional[dict] = None


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
    time_module.sleep(3)  # DB가 준비될 때까지 대기
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


class LogoutResponse(BaseModel):
    message: str


class RecordDeleteResponse(BaseModel):
    """식단 기록 삭제 응답"""
    record_id: int
    message: str


def _resolve_user_from_session_or_params(
    request: Request,
    db: Session,
    id: Optional[str],
    user_number: Optional[int],
) -> User:
    session_user_id = request.session.get("user_id")
    if session_user_id:
        user = db.query(User).filter(User.id == session_user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 유효하지 않습니다.")
        return user

    if id:
        user = db.query(User).filter(User.id == id).first()
    elif user_number is not None:
        user = db.query(User).filter(User.user_number == user_number).first()
    else:
        user = None

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return user

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
def login(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
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

    request.session["user_id"] = user.id
    request.session["user_number"] = user.user_number

    return {
        "user_number": user.user_number,
        "id": user.id,
        "username": user.username,
        "message": "로그인 성공"
    }

async def get_current_user_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Supabase 토큰을 검증하고 해당하는 로컬 DB 사용자를 반환
    (없으면 자동 회원가입)
    """
    token = credentials.credentials
    
    try:
        # 1. Supabase에 토큰 검증 요청
        user_response = supabase.auth.get_user(token)
        user_data = user_response.user
        
        if not user_data:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰입니다.")
        
        # 2. 로컬 DB에서 사용자 조회
        # social_id (uid)로 조회
        provider_user_id = user_data.id
        email = user_data.email
        
        existing_user = db.query(User).filter(User.provider_user_id == provider_user_id).first()
        
        if existing_user:
            return existing_user
            
        # 3. 없으면 자동 회원가입 진행
        # ID는 이메일이나 난수로 생성, 비밀번호는 사용 안 함(Dummy)
        new_username = user_data.user_metadata.get("full_name") or user_data.user_metadata.get("name") or email.split("@")[0]
        
        new_user = User(
            id=email, # 소셜 로그인은 이메일을 ID로 사용하거나 UUID 사용
            username=new_username,
            password=uuid4().hex, # 비밀번호는 랜덤으로 설정 (로그인에 사용 안 함)
            provider_user_id=provider_user_id,
            email=email,
            role="user"
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        # 프로필도 함께 생성
        new_profile = UserProfile(
            user_number=new_user.user_number
        )
        db.add(new_profile)
        db.commit()
        
        return new_user
        
    except Exception as e:
        print(f"Token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증에 실패했습니다."
        )

# --- Social Login & Registration ---

class SocialCheckRequest(BaseModel):
    access_token: str

class SocialRegisterRequest(BaseModel):
    access_token: str
    username: str
    height: Optional[float] = None
    weight: Optional[float] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    activity_level: Optional[str] = None
    goal_type: Optional[str] = "maintain"

@app.get("/api/auth/oauth/url")
def get_oauth_url(provider: str = "google"):
    """
    Supabase OAuth 로그인 URL 생성 후 바로 리다이렉트
    (공식 supabase-py 패턴: sign_in_with_oauth 사용)
    """
    try:
        redirect_to = "http://localhost:3000/login/callback"  # ✅ 너 프론트에서 쓰는 콜백으로 맞추기

        resp = supabase.auth.sign_in_with_oauth({
            "provider": provider,
            "options": {
                "redirect_to": redirect_to
            }
        })

        # ✅ 브라우저가 이 엔드포인트로 오면 구글 로그인 페이지로 바로 이동
        return RedirectResponse(url=resp.url, status_code=302)

    except Exception as e:
        print(f"OAuth URL generation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OAuth URL 생성 중 오류가 발생했습니다: {str(e)}"
        )

@app.post("/api/auth/social-check")
def check_social_user(payload: SocialCheckRequest, db: Session = Depends(get_db)):
    """
    소셜 로그인 후 가입 여부 확인
    - 가입되어 있으면: 로그인 처리 결과 반환 (토큰은 이미 프론트가 가지고 있음)
    - 가입 안되어 있으면: registered=False 반환
    """
    try:
        # 1. 토큰 검증
        user_response = supabase.auth.get_user(payload.access_token)
        user_data = user_response.user
        
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid token")

        # 2. DB 조회
        existing_user = db.query(User).filter(User.provider_user_id == user_data.id).first()
        
        if existing_user:
             return {
                "registered": True,
                "user_number": existing_user.user_number,
                "id": existing_user.id,
                "username": existing_user.username,
                "message": "로그인 성공"
            }
        else:
            return {
                "registered": False,
                "email": user_data.email,
                "provider_user_id": user_data.id
            }

    except Exception as e:
        print(f"Social check failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/social-register")
def register_social_user(payload: SocialRegisterRequest, db: Session = Depends(get_db)):
    """
    소셜 로그인 후 추가 정보를 입력받아 회원가입 완료
    """
    try:
        # 1. 토큰 검증
        user_response = supabase.auth.get_user(payload.access_token)
        user_data = user_response.user
        
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid token")
            
        provider_user_id = user_data.id
        email = user_data.email
        
        # 2. 중복 확인 (혹시나)
        if db.query(User).filter(User.provider_user_id == provider_user_id).first():
             raise HTTPException(status_code=400, detail="이미 가입된 사용자입니다.")

        # 3. User 생성
        new_user = User(
            id=email, 
            username=payload.username,
            password=uuid4().hex, # 비밀번호 미사용
            provider_user_id=provider_user_id,
            email=email,
            role="user"
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        # 4. Profile 생성
        new_profile = UserProfile(
            user_number=new_user.user_number,
            height=payload.height,
            weight=payload.weight,
            age=payload.age,
            gender=payload.gender,
            activity_level=normalize_activity_level(payload.activity_level) if payload.activity_level else "sedentary",
            goal_type=payload.goal_type
        )
        db.add(new_profile)
        
        # 5. Goal 생성 (기본값)
        if payload.goal_type:
             bmr = None # 계산 필요하면 여기서 계산 로직 추가
             # 간단히 maintain으로 초기화
             goal = UserGoal(
                user_number=new_user.user_number,
                id=new_user.id,
                goal_type=payload.goal_type,
                start_date=datetime.now(timezone.utc)
             )
             db.add(goal)

        db.commit()
        
        return {
            "registered": True,
            "user_number": new_user.user_number,
            "id": new_user.id,
            "username": new_user.username,
            "message": "회원가입 완료"
        }

    except Exception as e:
        db.rollback()
        print(f"Social registration failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/me", response_model=AuthResponse)
def read_users_me(current_user: User = Depends(get_current_user_from_token)):
    """
    현재 로그인된(토큰) 사용자 정보 조회
    """
    return {
        "user_number": current_user.user_number,
        "id": current_user.id,
        "username": current_user.username,
        "message": "사용자 정보를 성공적으로 불러왔습니다."
    }


@app.post("/api/logout", response_model=LogoutResponse)
def logout(request: Request):
    """로그아웃 (서버 상태 없음)"""
    request.session.clear()
    return {"message": "로그아웃 성공"}


@app.get("/api/user", response_model=UserResponse)
def get_user(request: Request, id: Optional[str] = None, user_number: int = 1, db: Session = Depends(get_db)):
    """사용자 기본 정보 조회"""
    user = _resolve_user_from_session_or_params(request, db, id, user_number)
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
def get_user_goal(request: Request, user_number: int = 1, db: Session = Depends(get_db)):
    """사용자 목표 조회"""
    user = _resolve_user_from_session_or_params(request, db, None, user_number)
    goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == user.user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="목표를 찾을 수 없습니다.")
    return {
        "goal_id": goal.goal_id,
        "goal_type": goal.goal_type,
        "target_calorie": goal.target_calorie,
        "target_protein": goal.target_protein,
        "target_carb": goal.target_carb,
        "target_fat": goal.target_fat,
        "target_macros": goal.target_macros,
        "target_pace": goal.target_pace,
        "start_date": goal.start_date.isoformat() if goal.start_date else None,
        "end_date": goal.end_date.isoformat() if goal.end_date else None,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
    }


@app.post("/api/user/goal", response_model=UserGoalResponse)
def upsert_user_goal(payload: UserGoalUpdateRequest, db: Session = Depends(get_db)):
    """사용자 목표 변경(없으면 생성)"""
    raw_goal_type = payload.goal_type.strip().lower()
    goal_type_map = {
        "diet": "diet",
        "다이어트": "diet",
        "감량": "diet",
        "maintain": "maintain",
        "유지": "maintain",
        "표준": "maintain",
        "bulk": "bulk",
        "벌크": "bulk",
        "벌크업": "bulk",
        "증량": "bulk",
    }
    goal_type = goal_type_map.get(raw_goal_type, raw_goal_type)
    if goal_type not in {"diet", "maintain", "bulk"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="goal_type은 diet/maintain/bulk 중 하나여야 합니다.",
        )

    user = db.query(User).filter(User.user_number == payload.user_number).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    profile = db.query(UserProfile).filter(UserProfile.user_number == payload.user_number).one_or_none()
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == payload.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )

    target_calorie = payload.target_calorie
    if target_calorie is None:
        bmr = latest_inbody.bmr if latest_inbody and latest_inbody.bmr is not None else (profile.bmr if profile else None)
        weight = latest_inbody.weight if latest_inbody and latest_inbody.weight is not None else (profile.weight if profile else None)
        target_calorie = estimate_target_calorie(
            goal_type,
            bmr,
            weight,
            normalize_activity_level(profile.activity_level) if profile else None,
        )

    latest_goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == payload.user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )
    if latest_goal:
        latest_goal.goal_type = goal_type
        latest_goal.target_calorie = target_calorie
        latest_goal.start_date = datetime.now(timezone.utc)
        goal = latest_goal
    else:
        goal = UserGoal(
            user_number=payload.user_number,
            id=user.id,
            goal_type=goal_type,
            target_calorie=target_calorie,
            start_date=datetime.now(timezone.utc),
        )
        db.add(goal)

    if profile:
        profile.goal_type = goal_type
    db.commit()
    return {
        "goal_id": goal.goal_id,
        "goal_type": goal.goal_type,
        "target_calorie": goal.target_calorie,
        "target_protein": goal.target_protein,
        "target_carb": goal.target_carb,
        "target_fat": goal.target_fat,
        "target_macros": goal.target_macros,
        "target_pace": goal.target_pace,
        "start_date": goal.start_date.isoformat() if goal.start_date else None,
        "end_date": goal.end_date.isoformat() if goal.end_date else None,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
    }


@app.post("/api/user/activity-level")
def update_activity_level(payload: ActivityLevelUpdateRequest, db: Session = Depends(get_db)):
    """사용자 활동 수준 업데이트"""
    level = normalize_activity_level(payload.activity_level)
    if level not in ACTIVITY_FACTORS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="activity_level은 sedentary/light/moderate/active 중 하나여야 합니다.",
        )

    profile = db.query(UserProfile).filter(UserProfile.user_number == payload.user_number).one_or_none()
    if not profile:
        profile = UserProfile(user_number=payload.user_number)
        db.add(profile)

    profile.activity_level = level
    db.commit()
    return {"user_number": payload.user_number, "activity_level": level, "factor": ACTIVITY_FACTORS[level]}


@app.get("/api/inbody", response_model=Optional[InBodyHistoryResponse])
def get_latest_inbody(request: Request, user_number: int = 1, db: Session = Depends(get_db)):
    """사용자 최신 인바디 기록 조회"""
    user = _resolve_user_from_session_or_params(request, db, None, user_number)
    record = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user.user_number)
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
        "predicted_classify": record.predicted_classify,
        "classify_name": record.classify_name,
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


@app.get("/api/mypage", response_model=MyPageEnvelopeResponse)
def get_mypage_records(
    request: Request,
    id: Optional[str] = None,
    user_number: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """마이페이지 식단 기록 조회"""
    user = _resolve_user_from_session_or_params(request, db, id, user_number)
    user_number = user.user_number
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
    goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )

    height = latest_inbody.height if latest_inbody and latest_inbody.height is not None else (profile.height if profile else None)
    weight = latest_inbody.weight if latest_inbody and latest_inbody.weight is not None else (profile.weight if profile else None)
    skeletal_muscle_mass = (
        latest_inbody.skeletal_muscle_mass if latest_inbody and latest_inbody.skeletal_muscle_mass is not None
        else (profile.skeletal_muscle_mass if profile else None)
    )
    body_fat_percent = (
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
    return {
        "user": (
            {
                "user_number": user.user_number,
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role,
            }
            if user
            else None
        ),
        "body": {
            "height": height,
            "weight": weight,
            "skeletal_muscle_mass": skeletal_muscle_mass,
            "body_fat_percent": body_fat_percent,
        },
        "goal": (
            {
                "goal_id": goal.goal_id,
                "goal_type": goal.goal_type,
                "target_calorie": goal.target_calorie,
                "target_protein": goal.target_protein,
                "target_carb": goal.target_carb,
                "target_fat": goal.target_fat,
                "target_macros": goal.target_macros,
                "target_pace": goal.target_pace,
                "start_date": goal.start_date.isoformat() if goal.start_date else None,
                "end_date": goal.end_date.isoformat() if goal.end_date else None,
                "created_at": goal.created_at.isoformat() if goal.created_at else None,
            }
            if goal
            else None
        ),
        "records": [
            {
                "id": record.record_id,
                "goal_calories": int(record.goal_calories) if record.goal_calories is not None else 0,
                "food_name": record.food_name,
                "calories": int(record.food_calories),
                "record_created_at": record.record_created_at.isoformat() if record.record_created_at else "",
                "height": height,
                "weight": weight,
                "skeletal_muscle_mass": skeletal_muscle_mass,
                "body_fat_percent": body_fat_percent,
            }
            for record in records
        ],
    }


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
def get_inbody_history(request: Request, user_number: int = 1, limit: int = 10, db: Session = Depends(get_db)):
    """
    사용자의 인바디 측정 히스토리 조회
    """
    user = _resolve_user_from_session_or_params(request, db, None, user_number)
    records = db.query(InBodyRecord).filter(
        InBodyRecord.user_number == user.user_number
    ).order_by(InBodyRecord.created_at.desc()).limit(limit).all()
    
    return [
        {
            "inbody_id": record.inbody_id,
            "measurement_date": record.measurement_date.isoformat() if record.measurement_date else None,
            "height": record.height,
            "weight": record.weight,
            "body_fat_pct": record.body_fat_pct,
            "skeletal_muscle_mass": record.skeletal_muscle_mass,
            "predicted_classify": record.predicted_classify,
            "classify_name": record.classify_name,
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
    request: Request,
    id: Optional[str] = Form(None),
    user_number: Optional[int] = Form(None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    인바디 사진 OCR -> 핵심 항목 추출 -> users 테이블 최신값 업데이트
    """
    user = _resolve_user_from_session_or_params(request, db, id, user_number)
    user_number = user.user_number

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
        profile = (
            db.query(UserProfile)
            .filter(UserProfile.user_number == user_number)
            .first()
        )
        return {
            "raw_text": text,
            "text": "",
            "values": {},
            "updated": False,
            "activity_level": profile.activity_level if profile else None,
            "activity_level_options": {
                "sedentary": 1.2,
                "light": 1.375,
                "moderate": 1.55,
                "active": 1.725,
            },
        }

    try:
        update_user_inbody(user_number, values)
    except RuntimeError as e:
        # 사용자를 찾을 수 없는 경우 등
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 업데이트 실패: {e}")

    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == user_number)
        .first()
    )
    return {
        "raw_text": text,
        "text": format_key_values(values),
        "values": values,
        "updated": True,
        "activity_level": profile.activity_level if profile else None,
        "activity_level_options": {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
        },
    }


@app.post("/api/inbody-ocr/upload", response_model=InBodyOcrResponse)
async def inbody_ocr_upload(
    request: Request,
    id: Optional[str] = Form(None),
    user_number: Optional[int] = Form(None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    인바디 사진 OCR -> 핵심 항목 추출 -> users 테이블 최신값 업데이트
    """
    user = _resolve_user_from_session_or_params(request, db, id, user_number)
    user_number = user.user_number

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
        profile = (
            db.query(UserProfile)
            .filter(UserProfile.user_number == user_number)
            .first()
        )
        return {
            "raw_text": text,
            "text": "",
            "values": {},
            "updated": False,
            "activity_level": profile.activity_level if profile else None,
            "activity_level_options": {
                "sedentary": 1.2,
                "light": 1.375,
                "moderate": 1.55,
                "active": 1.725,
            },
        }

    try:
        update_user_inbody(user_number, values)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 업데이트 실패: {e}")

    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == user_number)
        .first()
    )
    return {
        "raw_text": text,
        "text": format_key_values(values),
        "values": values,
        "updated": True,
        "activity_level": profile.activity_level if profile else None,
        "activity_level_options": {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
        },
    }


@app.get("/api/record")
def get_record(date: str, user_number: int = 3, db: Session = Depends(get_db)):
    """
    특정 날짜의 식단 기록 조회
    - date: "YYYY-MM-DD"
    - user_number: 테스트 기본값 3 (나중에 로그인 연동)
    """
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date는 YYYY-MM-DD 형식이어야 합니다.")

    start = day
    end = day + timedelta(days=1)

    rows = db.query(Record).filter(
        Record.user_number == user_number,
        Record.record_created_at >= start,
        Record.record_created_at < end,
    ).order_by(Record.record_created_at.desc()).all()

    return [
        {
            "record_id": r.record_id,
            "food_name": r.food_name,
            "food_calories": r.food_calories,
            "food_protein": r.food_protein,
            "food_carb": r.food_carb,
            "food_fat": r.food_fat,
            "meal_type": r.meal_type,
            "image_url": r.image_url,
            "record_created_at": r.record_created_at.isoformat(),
        }
        for r in rows
    ]


@app.delete("/api/record/{record_id}", response_model=RecordDeleteResponse)
def delete_record(
    record_id: int,
    request: Request,
    id: Optional[str] = None,
    user_number: int = 1,
    db: Session = Depends(get_db),
):
    """식단 기록 삭제"""
    user = _resolve_user_from_session_or_params(request, db, id, user_number)
    record = (
        db.query(Record)
        .filter(Record.record_id == record_id, Record.user_number == user.user_number)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="식단 기록을 찾을 수 없습니다.")

    db.delete(record)
    db.commit()

    return {"record_id": record_id, "message": "식단 기록이 삭제되었습니다."}
@app.post("/api/vision/food")
async def vision_food(
    user_number: int = Form(...),
    meal_type: str = Form(...),
    record_date: Optional[str] = Form(None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        ext = (Path(image.filename).suffix or ".jpg").lower()
        filename = f"{uuid4().hex}{ext}"
        save_path = UPLOAD_DIR / filename

        content = await image.read()
        save_path.write_bytes(content)

        image_url = f"/uploads/foods/{filename}"

        result = decide_food_gpt_only(str(save_path))
        decision = result["decision"]
        nutrition = decision["nutrition"]

        # 1) 분석 결과 저장
        far = FoodAnalysisResult(
            user_number=user_number,
            image_url=image_url,
            predicted_food_name=decision["chosen_food"],
            predicted_reason=decision["reason"],
            estimated_serving_g=None,
            estimated_calories_kcal=nutrition.get("calories_kcal"),
            estimated_carb_g=nutrition.get("carbs_g"),
            estimated_protein_g=nutrition.get("protein_g"),
            estimated_fat_g=nutrition.get("fat_g"),
            model="gpt-4.1-mini",
            status="PENDING",
        )
        db.add(far)
        db.commit()
        db.refresh(far)

        # 2) ✅ 화면 조회용 Record 저장 (GET /api/record가 이걸 가져감)
        record_created_at = datetime.utcnow()
        if record_date:
            try:
                day = datetime.strptime(record_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="record_date는 YYYY-MM-DD 형식이어야 합니다.")
            record_created_at = datetime.combine(day, dt_time(12, 0, 0))

        rec = Record(
            user_number=user_number,
            food_name=decision["chosen_food"],
            food_calories=nutrition.get("calories_kcal"),
            food_protein=nutrition.get("protein_g"),
            food_carb=nutrition.get("carbs_g"),
            food_fat=nutrition.get("fat_g"),
            meal_type=meal_type,
            image_url=image_url,
            record_created_at=record_created_at,
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)

        return {
            "far_id": far.far_id,
            "record_id": rec.record_id,
            "status": far.status,
            "image_url": image_url,
            "decision": decision,
        }

    except Exception as e:
        print("\n" + "="*60)
        print("🚨 [Error] /api/vision/food failed!")
        print(f"   - Error Message: {e}")
        print("-" * 60)
        print(traceback.format_exc())
        print("="*60 + "\n")
        raise HTTPException(status_code=500, detail=f"Vision API Error: {str(e)}")
            
            
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
        gender = "M"
    elif gender_raw in ("f", "female", "여", "여성"):
        gender = "F"
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
        gender=gender,
        height_cm=record.height,
        weight_kg=record.weight,
        body_fat_kg=record.body_fat_mass,
        body_fat_pct=record.body_fat_pct,
        skeletal_muscle_kg=record.skeletal_muscle_mass,
        bmr_kcal=record.bmr,
    )
    result = classify_body_type(inbody_input)
    record.predicted_classify = None
    record.classify_name = result.stage2
    db.commit()
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
