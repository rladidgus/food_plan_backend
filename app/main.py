import os
import json
import traceback
import time
import logging
import time as time_module
import re
from datetime import date, datetime, timezone, time, timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import and_, func
from fastapi.responses import RedirectResponse
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.food_lens import decide_food_gpt_only
from app.database import get_db, engine, Base
from app.inbody import InbodyInput, BodyTypeResult, classify_body_type
from app.models import Record, InBodyRecord, User, UserProfile, FoodAnalysisResult
from typing import List, Optional
from app import models
from app.inbody_ocr import extract_key_values, format_key_values, upstage_ocr_from_bytes, update_user_inbody
from app.models import Record, InBodyRecord, User, UserProfile, UserGoal, DailyActivity, UserDietPlan
from app.goal_rules import estimate_target_calorie, normalize_activity_level, ACTIVITY_FACTORS, infer_goal_type
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client
import requests
from app.meal_plan_ai import MealPlanItem, DayMealPlan, OneDayMealPlan, generate_one_day_plan
from app.agent_graph import build_graph
from app.schemas import (
    FetchNutritionRequest,
    FetchNutritionResponse,
    NodeRunRequest,
    NodeRunResponse,
)

# Supabase 클라이언트 초기화
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 환경변수가 필요합니다.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
security = HTTPBearer()

UPLOAD_DIR = Path("uploads/foods")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# PEXELS_API_KEY 환경변수에 발급받은 키를 설정하세요.
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PEXELS_API_URL = "https://api.pexels.com/v1/search"
_pexels_cache: dict[str, Optional[str]] = {}

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_LOCAL_CATEGORY_API_URL = "https://dapi.kakao.com/v2/local/search/category.json"
KAKAO_LOCAL_KEYWORD_API_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

# 환경 변수에서 DB 정보 가져오기
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")

# FastAPI 앱 생성
app = FastAPI(title="식단 계획 AI API")
logger = logging.getLogger("app.sync")
if not logging.getLogger().handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:3000"
    ).split(",")
    if origin.strip()
]

# CORS 설정 (Next.js 프론트엔드와 통신)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
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

class UserResponse(BaseModel):
    """사용자 기본 정보 응답"""
    user_number: int
    id: str
    username: str
    email: Optional[str] = None
    role: Optional[str] = None

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    user_number: int
    id: str
    username: str
    message: str
    has_inbody: Optional[bool] = None


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
    user_number: Optional[int] = None
    goal_type: str
    target_calorie: Optional[float] = None


class DietPlan3DaysRequest(BaseModel):
    """3일 식단 추천 요청"""
    user_number: Optional[int] = None
    id: Optional[str] = None
    goal_type: Optional[str] = None
    target_calorie: Optional[float] = None


class DietPlanContextRequest(BaseModel):
    """상황 기반 식단 추천 요청"""
    context: str
    target_calorie: Optional[float] = None


class PlanMealRecordIn(BaseModel):
    meal_type: str
    name: str
    calories_kcal: float
    carbs_g: float
    protein_g: float
    fat_g: float


class PlanRecordCreateRequest(BaseModel):
    user_number: Optional[int] = None
    id: Optional[str] = None
    record_date: Optional[str] = None  # YYYY-MM-DD, default: today
    meals: List[PlanMealRecordIn]


class PlanRecordCreateResult(BaseModel):
    record_ids: List[int]


class TodayIntakeResponse(BaseModel):
    goal_type: str
    target_calorie: Optional[float] = None
    total_calories_kcal: int
    total_carbs_g: float
    total_protein_g: float
    total_fat_g: float
    plan_date: Optional[str] = None


class UserGoalWithPlanResponse(UserGoalResponse):
    """사용자 목표 변경 응답 (목표 + 최신 식단)"""
    plan: Optional[OneDayMealPlan] = None
    today_intake: Optional[TodayIntakeResponse] = None


class DietPlanWithIntakeResponse(BaseModel):
    plan: OneDayMealPlan
    today_intake: TodayIntakeResponse


class CalendarMarkedDatesResponse(BaseModel):
    year: int
    month: int
    dates: List[str]


class DietPlanPlacesRequest(BaseModel):
    food_name: Optional[str] = None
    lat: float
    lng: float
    radius_m: Optional[int] = 2000


class DietPlanPlaceItem(BaseModel):
    id: str
    name: str
    category_group_code: Optional[str] = None
    category_group_name: Optional[str] = None
    category_name: Optional[str] = None
    address_name: Optional[str] = None
    road_address_name: Optional[str] = None
    phone: Optional[str] = None
    place_url: Optional[str] = None
    distance_m: Optional[int] = None
    x: float
    y: float


class DietPlanPlacesResponse(BaseModel):
    places: List[DietPlanPlaceItem]



class ActivityLevelUpdateRequest(BaseModel):
    user_number: Optional[int] = None
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
    user_number: Optional[int] = None
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
    user_number: Optional[int] = None



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


class LogoutResponse(BaseModel):
    message: str


class RecordDeleteResponse(BaseModel):
    """식단 기록 삭제 응답"""
    record_id: int
    message: str


def _normalize_goal_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().lower()
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
    return goal_type_map.get(raw, raw)


def _fetch_pexels_image(query: str) -> Optional[str]:
    if not query:
        return None
    if not PEXELS_API_KEY:
        return None
    key = query.strip().lower()
    if key in _pexels_cache:
        return _pexels_cache[key]

    try:
        resp = requests.get(
            PEXELS_API_URL,
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": query,
                "per_page": 1,
                "orientation": "landscape",
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        photos = data.get("photos") or []
        if not photos:
            _pexels_cache[key] = None
            return None

        src = photos[0].get("src") or {}
        image_url = src.get("large") or src.get("medium") or src.get("original")
        _pexels_cache[key] = image_url
        return image_url
    except Exception:
        _pexels_cache[key] = None
        return None


def _kakao_local_search_category(lat: float, lng: float, radius_m: int, category_code: str) -> List[dict]:
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=500, detail="KAKAO_REST_API_KEY 환경변수가 필요합니다.")

    radius = max(10, min(int(radius_m), 20000))
    try:
        resp = requests.get(
            KAKAO_LOCAL_CATEGORY_API_URL,
            headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
            params={
                "category_group_code": category_code,
                "x": lng,
                "y": lat,
                "radius": radius,
                "sort": "distance",
                "size": 10,
            },
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(
                "Kakao category search failed: status=%s, body=%s",
                resp.status_code,
                (resp.text or "")[:500],
            )
            raise HTTPException(status_code=502, detail="Kakao Local API 오류(카테고리 검색)")
        data = resp.json()
        docs = data.get("documents") or []
        if not docs:
            meta = data.get("meta") or {}
            logger.info(
                "Kakao category empty: code=%s, radius=%s, lat=%s, lng=%s, total=%s",
                category_code,
                radius,
                lat,
                lng,
                meta.get("total_count"),
            )
        return docs
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Kakao category search exception: %s", e)
        raise HTTPException(status_code=502, detail="Kakao Local API 요청 실패(카테고리 검색)")


def _kakao_local_search_keyword(lat: float, lng: float, radius_m: int, keyword: str) -> List[dict]:
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=500, detail="KAKAO_REST_API_KEY 환경변수가 필요합니다.")

    if not keyword or not keyword.strip():
        return []

    radius = max(10, min(int(radius_m), 20000))
    try:
        resp = requests.get(
            KAKAO_LOCAL_KEYWORD_API_URL,
            headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
            params={
                "query": keyword.strip(),
                "x": lng,
                "y": lat,
                "radius": radius,
                "sort": "distance",
                "size": 10,
            },
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(
                "Kakao keyword search failed: status=%s, body=%s",
                resp.status_code,
                (resp.text or "")[:500],
            )
            raise HTTPException(status_code=502, detail="Kakao Local API 오류(키워드 검색)")
        data = resp.json()
        docs = data.get("documents") or []
        if not docs:
            meta = data.get("meta") or {}
            logger.info(
                "Kakao keyword empty: keyword=%s, radius=%s, lat=%s, lng=%s, total=%s",
                keyword.strip(),
                radius,
                lat,
                lng,
                meta.get("total_count"),
            )
        return docs
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Kakao keyword search exception: %s", e)
        raise HTTPException(status_code=502, detail="Kakao Local API 요청 실패(키워드 검색)")


def _extract_food_tokens(food_name: str) -> List[str]:
    tokens = [t for t in re.split(r"\s+", (food_name or "").strip()) if len(t) >= 2]
    return tokens


def _build_place_queries(food_name: str) -> List[str]:
    base = (food_name or "").strip()
    if not base:
        return []
    tokens = _extract_food_tokens(base)
    queries = [
        base,
        f"{base} 전문점",
        f"{base} 맛집",
        f"{base} 카페",
    ]
    if tokens:
        last = tokens[-1]
        queries.extend([
            f"{last} 전문점",
            f"{last} 맛집",
        ])
    # dedup while preserving order
    seen = set()
    result: List[str] = []
    for q in queries:
        qn = q.strip()
        if not qn or qn in seen:
            continue
        seen.add(qn)
        result.append(qn)
    return result[:8]


def _place_score(item: dict, tokens: List[str], queries: List[str]) -> int:
    name = (item.get("place_name") or "").lower()
    cat = (item.get("category_name") or "").lower()
    score = 0
    for t in tokens:
        tl = t.lower()
        if tl in name:
            score += 6
        if tl in cat:
            score += 3
    for q in queries:
        ql = q.lower()
        if ql and ql in name:
            score += 2
    return score


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
        new_username = user_data.user_metadata.get("full_name") or user_data.user_metadata.get("name") or (email.split("@")[0] if email else provider_user_id[:8])
        
        new_user = User(
            id=_fallback_user_id(provider_user_id, email),
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

class SocialCheckResponse(BaseModel):
    registered: bool
    user_number: Optional[int] = None
    id: Optional[str] = None
    username: Optional[str] = None
    message: Optional[str] = None
    email: Optional[str] = None
    provider_user_id: Optional[str] = None
    suggested_username: Optional[str] = None
    has_inbody: Optional[bool] = None
    next_path: Optional[str] = None

class SocialRegisterRequest(BaseModel):
    access_token: str
    username: str
    height: Optional[float] = None
    weight: Optional[float] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    activity_level: Optional[str] = None
    goal_type: Optional[str] = "maintain"


def _fallback_user_id(provider_user_id: Optional[str], email: Optional[str]) -> str:
    if email:
        return email
    if provider_user_id:
        return f"supabase:{provider_user_id}"
    return uuid4().hex


def _log_access_token(token: str) -> None:
    if os.getenv("LOG_FULL_TOKEN") == "1":
        logger.info("access_token=%s", token)
        return
    if not token:
        logger.info("access_token=EMPTY")
        return
    prefix = token[:8]
    logger.info("access_token_prefix=%s...", prefix)

@app.get("/api/auth/oauth/url")
def get_oauth_url(provider: str = "google"):
    """
    Supabase OAuth 로그인 URL 생성 후 바로 리다이렉트
    (공식 supabase-py 패턴: sign_in_with_oauth 사용)
    """
    try:
        redirect_to = "http://localhost:3000/login/callback"  # 프론트에서 쓰는 콜백으로 맞추기

        resp = supabase.auth.sign_in_with_oauth({
            "provider": provider,
            "options": {
                "redirect_to": redirect_to,
                "scopes": "email profile",
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

@app.post("/api/auth/social-check", response_model=SocialCheckResponse)
def check_social_user(payload: SocialCheckRequest, db: Session = Depends(get_db)):
    """
    소셜 로그인 후 가입 여부 확인
    - 가입되어 있으면: 로그인 처리 결과 반환
    - 가입 안되어 있으면: registered=False 반환
    """
    try:
        _log_access_token(payload.access_token)
        # 1. 토큰 검증
        user_response = supabase.auth.get_user(payload.access_token)
        user_data = user_response.user

        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid token")

    except Exception as e:
        print(f"Social check failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    email = user_data.email
    suggested_username = (
        user_data.user_metadata.get("full_name")
        or user_data.user_metadata.get("name")
        or (email.split("@")[0] if email else None)
    )

    # 2. DB 조회
    existing_user = db.query(User).filter(User.provider_user_id == user_data.id).first()
    if not existing_user and email:
        existing_user = (
            db.query(User)
            .filter((User.email == email) | (User.id == email))
            .first()
        )
        # 기존 계정에 소셜 아이디가 비어 있으면 연결
        if existing_user and not existing_user.provider_user_id:
            existing_user.provider_user_id = user_data.id
            db.commit()
            db.refresh(existing_user)

    if existing_user:
        latest_inbody = (
            db.query(InBodyRecord)
            .filter(InBodyRecord.user_number == existing_user.user_number)
            .order_by(InBodyRecord.created_at.desc())
            .first()
        )
        has_inbody = bool(latest_inbody)
        return {
            "registered": True,
            "user_number": existing_user.user_number,
            "id": existing_user.id,
            "username": existing_user.username,
            "message": "로그인 성공",
            "suggested_username": suggested_username,
            "has_inbody": has_inbody,
            "next_path": "/" if has_inbody else "/inbody",
        }

    # 3. 가입 안되어 있으면 프론트에서 social-register 호출
    return {
        "registered": False,
        "email": email,
        "provider_user_id": user_data.id,
        "suggested_username": suggested_username,
    }


@app.post("/api/auth/social-register")
def register_social_user(payload: SocialRegisterRequest, db: Session = Depends(get_db)):
    """
    소셜 로그인 후 추가 정보를 입력받아 회원가입 완료
    """
    try:
        _log_access_token(payload.access_token)
        # 1) 토큰 검증 (supabase-py 공식 사용 패턴 유지)
        user_response = supabase.auth.get_user(payload.access_token)
        user_data = user_response.user
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid token")

        provider_user_id = user_data.id
        email = user_data.email

        # 2) 중복 확인 (provider_user_id, email/id)
        exists = db.query(User).filter(User.provider_user_id == provider_user_id).first()
        if not exists and email:
            exists = (
                db.query(User)
                .filter((User.email == email) | (User.id == email))
                .first()
            )
        if exists:
            raise HTTPException(status_code=400, detail="이미 가입된 사용자입니다.")

        # 3) User 생성
        new_user = User(
            id=_fallback_user_id(provider_user_id, email),
            username=payload.username,
            password=uuid4().hex,  # 소셜은 비밀번호 미사용이므로 더미
            provider_user_id=provider_user_id,
            email=email,
            role="user",
        )
        db.add(new_user)
        db.flush()  # ✅ user_number(PK) 생성되도록 (commit 전에 PK 필요)

        # 4) Profile 생성
        new_profile = UserProfile(
            user_number=new_user.user_number,
            height=payload.height,
            weight=payload.weight,
            age=payload.age,
            gender=payload.gender,
            activity_level=normalize_activity_level(payload.activity_level) if payload.activity_level else "sedentary",
            goal_type=payload.goal_type,
        )
        db.add(new_profile)

        # 5) Goal 생성(기본값)
        if payload.goal_type:
            goal = UserGoal(
                user_number=new_user.user_number,
                id=new_user.id,
                goal_type=payload.goal_type,
                start_date=datetime.now(timezone.utc),
            )
            db.add(goal)

        # 6) 마지막에 한 번만 커밋 (표준적인 트랜잭션 패턴)
        db.commit()
        db.refresh(new_user)

        return {
            "registered": True,
            "user_number": new_user.user_number,
            "id": new_user.id,
            "username": new_user.username,
            "message": "회원가입 완료",
            "has_inbody": False,
            "next_path": "/inbody",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/me", response_model=AuthResponse)
def read_users_me(
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """
    현재 로그인된(토큰) 사용자 정보 조회
    """
    has_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == current_user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
        is not None
    )
    return {
        "user_number": current_user.user_number,
        "id": current_user.id,
        "username": current_user.username,
        "message": "사용자 정보를 성공적으로 불러왔습니다.",
        "has_inbody": has_inbody,
    }


@app.post("/api/logout", response_model=LogoutResponse)
def logout():
    """로그아웃 (JWT 환경에서는 클라이언트에서 토큰 폐기)"""
    return {"message": "로그아웃 성공"}


@app.get("/api/user", response_model=UserResponse)
def get_user(current_user: User = Depends(get_current_user_from_token)):
    """사용자 기본 정보 조회"""
    return {
        "user_number": current_user.user_number,
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
    }


@app.get("/api/user/goal", response_model=UserGoalResponse)
def get_user_goal(
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 목표 조회"""
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == current_user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )
    if not latest_inbody:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="인바디 기록이 없습니다.")

    goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == current_user.user_number)
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


@app.post("/api/user/goal", response_model=UserGoalWithPlanResponse)
def upsert_user_goal(
    payload: UserGoalUpdateRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 목표 변경(없으면 생성)"""
    goal_type = _normalize_goal_type(payload.goal_type)
    if goal_type not in {"diet", "maintain", "bulk"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="goal_type은 diet/maintain/bulk 중 하나여야 합니다.",
        )

    user = current_user

    profile = db.query(UserProfile).filter(UserProfile.user_number == user.user_number).one_or_none()
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user.user_number)
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
        .filter(UserGoal.user_number == user.user_number)
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
            user_number=user.user_number,
            id=user.id,
            goal_type=goal_type,
            target_calorie=target_calorie,
            start_date=datetime.now(timezone.utc),
        )
        db.add(goal)

    if profile:
        profile.goal_type = goal_type

    preferred_meals = []

    prompt = {
        "goal_type": goal_type,
        "target_calorie": round(float(target_calorie)) if target_calorie else None,
        "body_type_stage1": None,
        "body_type_stage2": None,
        "latest_inbody": {
            "height_cm": latest_inbody.height if latest_inbody else None,
            "weight_kg": latest_inbody.weight if latest_inbody else None,
            "body_fat_pct": latest_inbody.body_fat_pct if latest_inbody else None,
            "skeletal_muscle_kg": latest_inbody.skeletal_muscle_mass if latest_inbody else None,
            "bmr_kcal": latest_inbody.bmr if latest_inbody else None,
        },
        "activity_level": normalize_activity_level(profile.activity_level) if profile else None,
        "notes": [
            "한국어로만 작성한다.",
            "1일치(1일) 식단을 제공한다.",
            "각 일자는 아침/점심/저녁으로 구성한다.",
            "일일 총칼로리는 목표 칼로리 ±5% 범위를 지향한다.",
            "식단 이름은 한국어로 자연스럽고 구체적으로 작성한다.",
            "영양값은 추정치이며 현실적인 범위로 작성한다.",
            "요즘 한국에서 많이 먹는 대중적이고 익숙한 메뉴 위주로 구성한다.",
            "지나치게 방대한 메뉴 구성을 피하고 현실적으로 준비 가능한 수준으로 제안한다.",
        ],
    }

    try:
        plan = generate_one_day_plan(prompt, preferred_meals=preferred_meals)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"식단 생성 결과가 올바르지 않습니다. error={type(exc).__name__}: {exc}",
        )

    # 음식 이미지 URL 붙이기 (Pexels)
    for day in plan.days:
        for meal in (day.breakfast, day.lunch, day.dinner):
            if not meal.image_url:
                meal.image_url = _fetch_pexels_image(meal.name)

    db.add(
        UserDietPlan(
            user_number=user.user_number,
            goal_type=goal_type,
            target_calorie=target_calorie,
            plan_json=json.dumps(plan.model_dump(), ensure_ascii=False),
        )
    )

    db.commit()

    day0 = plan.days[0]
    today_intake = TodayIntakeResponse(
        goal_type=plan.goal_type,
        target_calorie=plan.target_calorie,
        total_calories_kcal=int(day0.total_calories_kcal),
        total_carbs_g=float(day0.total_carbs_g),
        total_protein_g=float(day0.total_protein_g),
        total_fat_g=float(day0.total_fat_g),
        plan_date=day0.date,
    )
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
        "plan": plan,
        "today_intake": today_intake,
    }


@app.post("/api/diet-plan", response_model=DietPlanWithIntakeResponse)
def generate_1day_diet_plan(
    payload: DietPlan3DaysRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """인바디 기반 1일 식단 추천 (OpenAI)"""
    user = current_user

    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == user.user_number)
        .first()
    )
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )
    if not latest_inbody:
        raise HTTPException(status_code=404, detail="인바디 기록이 없습니다.")

    body_stage1 = None
    body_stage2 = None
    gender_raw = (profile.gender if profile else None)
    gender = None
    if gender_raw:
        value = str(gender_raw).strip().lower()
        if value in ("m", "male", "남", "남성"):
            gender = "M"
        elif value in ("f", "female", "여", "여성"):
            gender = "F"

    required_fields = {
        "height": latest_inbody.height,
        "weight": latest_inbody.weight,
        "body_fat_mass": latest_inbody.body_fat_mass,
        "body_fat_pct": latest_inbody.body_fat_pct,
        "skeletal_muscle_mass": latest_inbody.skeletal_muscle_mass,
    }
    if gender and all(value is not None for value in required_fields.values()):
        inbody_input = InbodyInput(
            gender=gender,
            height_cm=latest_inbody.height,
            weight_kg=latest_inbody.weight,
            body_fat_kg=latest_inbody.body_fat_mass,
            body_fat_pct=latest_inbody.body_fat_pct,
            skeletal_muscle_kg=latest_inbody.skeletal_muscle_mass,
            bmr_kcal=latest_inbody.bmr,
        )
        body_result = classify_body_type(inbody_input)
        body_stage1 = body_result.stage1
        body_stage2 = body_result.stage2

    goal_type = _normalize_goal_type(payload.goal_type)
    if goal_type and goal_type not in {"diet", "maintain", "bulk"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="goal_type은 diet/maintain/bulk 중 하나여야 합니다.",
        )

    latest_goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == user.user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )
    if goal_type is None:
        if body_stage1 or body_stage2:
            goal_type = infer_goal_type(body_stage1 or "", body_stage2 or "")
        elif latest_goal and latest_goal.goal_type:
            goal_type = _normalize_goal_type(latest_goal.goal_type)
        elif profile and profile.goal_type:
            goal_type = _normalize_goal_type(profile.goal_type)
        else:
            goal_type = "maintain"

    target_calorie = payload.target_calorie
    if target_calorie is None:
        if latest_goal and latest_goal.target_calorie is not None:
            target_calorie = latest_goal.target_calorie
        else:
            target_calorie = estimate_target_calorie(
                goal_type,
                latest_inbody.bmr,
                latest_inbody.weight,
                normalize_activity_level(profile.activity_level) if profile else None,
            )

    preferred_meals = []

    prompt = {
        "goal_type": goal_type,
        "target_calorie": round(float(target_calorie)) if target_calorie else None,
        "body_type_stage1": body_stage1,
        "body_type_stage2": body_stage2,
        "latest_inbody": {
            "height_cm": latest_inbody.height,
            "weight_kg": latest_inbody.weight,
            "body_fat_pct": latest_inbody.body_fat_pct,
            "skeletal_muscle_kg": latest_inbody.skeletal_muscle_mass,
            "bmr_kcal": latest_inbody.bmr,
        },
        "activity_level": normalize_activity_level(profile.activity_level) if profile else None,
        "notes": [
            "한국어로만 작성한다.",
            "1일치(1일) 식단을 제공한다.",
            "각 일자는 아침/점심/저녁으로 구성한다.",
            "일일 총칼로리는 목표 칼로리 ±5% 범위를 지향한다.",
            "식단 이름은 한국어로 자연스럽고 구체적으로 작성한다.",
            "영양값은 추정치이며 현실적인 범위로 작성한다.",
            "요즘 한국에서 많이 먹는 대중적이고 익숙한 메뉴 위주로 구성한다.",
            "지나치게 방대한 메뉴 구성을 피하고 현실적으로 준비 가능한 수준으로 제안한다.",
        ],
    }

    try:
        plan = generate_one_day_plan(prompt, preferred_meals=preferred_meals)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"식단 생성 결과가 올바르지 않습니다. error={type(exc).__name__}: {exc}",
        )

    # 음식 이미지 URL 붙이기 (Pexels)
    for day in plan.days:
        for meal in (day.breakfast, day.lunch, day.dinner):
            if not meal.image_url:
                meal.image_url = _fetch_pexels_image(meal.name)

    record = UserDietPlan(
        user_number=user.user_number,
        goal_type=goal_type,
        target_calorie=target_calorie,
        plan_json=json.dumps(plan.model_dump(), ensure_ascii=False),
    )
    db.add(record)
    db.commit()

    day0 = plan.days[0]
    today_intake = TodayIntakeResponse(
        goal_type=plan.goal_type,
        target_calorie=plan.target_calorie,
        total_calories_kcal=int(day0.total_calories_kcal),
        total_carbs_g=float(day0.total_carbs_g),
        total_protein_g=float(day0.total_protein_g),
        total_fat_g=float(day0.total_fat_g),
        plan_date=day0.date,
    )

    return {
        "plan": plan,
        "today_intake": today_intake,
    }


@app.post("/api/diet-plan/context", response_model=DietPlanWithIntakeResponse)
def generate_context_aware_diet_plan(
    payload: DietPlanContextRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """상황 기반 1일 식단 추천 (OpenAI + Vector)"""
    user = current_user
    context = payload.context

    if not context or not context.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="context는 비어 있을 수 없습니다.",
        )

    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == user.user_number)
        .first()
    )
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )
    if not latest_inbody:
        raise HTTPException(status_code=404, detail="인바디 기록이 없습니다.")

    body_stage1 = None
    body_stage2 = None
    gender_raw = (profile.gender if profile else None)
    gender = None
    if gender_raw:
        value = str(gender_raw).strip().lower()
        if value in ("m", "male", "남", "남성"):
            gender = "M"
        elif value in ("f", "female", "여", "여성"):
            gender = "F"

    required_fields = {
        "height": latest_inbody.height,
        "weight": latest_inbody.weight,
        "body_fat_mass": latest_inbody.body_fat_mass,
        "body_fat_pct": latest_inbody.body_fat_pct,
        "skeletal_muscle_mass": latest_inbody.skeletal_muscle_mass,
    }
    if gender and all(value is not None for value in required_fields.values()):
        inbody_input = InbodyInput(
            gender=gender,
            height_cm=latest_inbody.height,
            weight_kg=latest_inbody.weight,
            body_fat_kg=latest_inbody.body_fat_mass,
            body_fat_pct=latest_inbody.body_fat_pct,
            skeletal_muscle_kg=latest_inbody.skeletal_muscle_mass,
            bmr_kcal=latest_inbody.bmr,
        )
        body_result = classify_body_type(inbody_input)
        body_stage1 = body_result.stage1
        body_stage2 = body_result.stage2

    latest_goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == user.user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )

    goal_type = infer_goal_type(body_stage1 or "", body_stage2 or "")
    if latest_goal and latest_goal.goal_type:
        goal_type = _normalize_goal_type(latest_goal.goal_type)
    elif profile and profile.goal_type:
        goal_type = _normalize_goal_type(profile.goal_type)
    else:
        goal_type = "maintain"

    target_calorie = payload.target_calorie
    if target_calorie is None:
        if latest_goal and latest_goal.target_calorie is not None:
            target_calorie = latest_goal.target_calorie
        else:
            target_calorie = estimate_target_calorie(
                goal_type,
                latest_inbody.bmr,
                latest_inbody.weight,
                normalize_activity_level(profile.activity_level) if profile else None,
            )

    preferred_meals = []

    prompt = {
        "goal_type": goal_type,
        "target_calorie": round(float(target_calorie)) if target_calorie else None,
        "body_type_stage1": body_stage1,
        "body_type_stage2": body_stage2,
        "user_context": context.strip(),
        "latest_inbody": {
            "height_cm": latest_inbody.height,
            "weight_kg": latest_inbody.weight,
            "body_fat_pct": latest_inbody.body_fat_pct,
            "skeletal_muscle_kg": latest_inbody.skeletal_muscle_mass,
            "bmr_kcal": latest_inbody.bmr,
        },
        "activity_level": normalize_activity_level(profile.activity_level) if profile else None,
        "notes": [
            "한국어로만 작성한다.",
            "1일치(1일) 식단을 제공한다.",
            "사용자의 특별 요청(user_context)을 최우선으로 고려하여 식단을 구성한다.",
            "각 일자는 아침/점심/저녁으로 구성한다.",
            "일일 총칼로리는 목표 칼로리 ±10% 범위를 지향한다.",
            "식단 이름은 한국어로 자연스럽고 구체적으로 작성한다.",
            "영양값은 추정치이며 현실적인 범위로 작성한다.",
            "요즘 한국에서 많이 먹는 대중적이고 익숙한 메뉴 위주로 구성한다.",
            "지나치게 방대한 메뉴 구성을 피하고 현실적으로 준비 가능한 수준으로 제안한다.",
        ],
    }

    try:
        plan = generate_one_day_plan(prompt, preferred_meals=preferred_meals)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"식단 생성 결과가 올바르지 않습니다. error={type(exc).__name__}: {exc}",
        )

    # 음식 이미지 URL 붙이기 (Pexels)
    for day in plan.days:
        for meal in (day.breakfast, day.lunch, day.dinner):
            if not meal.image_url:
                meal.image_url = _fetch_pexels_image(meal.name)

    record = UserDietPlan(
        user_number=user.user_number,
        goal_type=goal_type,
        target_calorie=target_calorie,
        plan_json=json.dumps(plan.model_dump(), ensure_ascii=False),
    )
    db.add(record)
    db.commit()

    day0 = plan.days[0]
    today_intake = TodayIntakeResponse(
        goal_type=plan.goal_type,
        target_calorie=plan.target_calorie,
        total_calories_kcal=int(day0.total_calories_kcal),
        total_carbs_g=float(day0.total_carbs_g),
        total_protein_g=float(day0.total_protein_g),
        total_fat_g=float(day0.total_fat_g),
        plan_date=day0.date,
    )

    return {
        "plan": plan,
        "today_intake": today_intake,
    }


@app.post("/api/diet-plan/places", response_model=DietPlanPlacesResponse)
def get_diet_plan_places(
    payload: DietPlanPlacesRequest,
    current_user: User = Depends(get_current_user_from_token),
):
    """내 위치 기반 주변 편의점/음식점 10곳 반환"""
    lat = float(payload.lat)  # 위도 (latitude)
    lng = float(payload.lng)  # 경도 (longitude)
    base_radius_m = payload.radius_m or 2000
    food_name = (payload.food_name or "").strip()

    categories = ["CS2", "FD6"]  # 편의점, 음식점
    queries: List[str] = []
    tokens: List[str] = []
    if food_name:
        queries = _build_place_queries(food_name)
        tokens = _extract_food_tokens(food_name)

    def _search_with_radius(radius_m: int) -> List[dict]:
        results: List[dict] = []
        if queries:
            for q in queries:
                results.extend(_kakao_local_search_keyword(lat, lng, radius_m, q))
        # 결과가 부족하면 카테고리 검색으로 보강
        if len(results) < 8:
            for code in categories:
                results.extend(_kakao_local_search_category(lat, lng, radius_m, code))
        return results

    def _add_dedup(items: List[dict], dedup: dict[str, dict]) -> None:
        for item in items:
            place_id = str(item.get("id"))
            if not place_id or place_id in dedup:
                continue
            dedup[place_id] = item

    radius_steps = [base_radius_m]
    if base_radius_m < 10000:
        radius_steps.append(10000)
    if base_radius_m < 15000:
        radius_steps.append(15000)
    if base_radius_m < 20000:
        radius_steps.append(20000)

    dedup: dict[str, dict] = {}
    for radius_m in radius_steps:
        _add_dedup(_search_with_radius(radius_m), dedup)
        if len(dedup) >= 10:
            break

    def _distance_value(value: Optional[str]) -> int:
        try:
            return int(value or 10**9)
        except Exception:
            return 10**9

    if tokens:
        sorted_items = sorted(
            dedup.values(),
            key=lambda x: (-_place_score(x, tokens, queries), _distance_value(x.get("distance"))),
        )
    else:
        sorted_items = sorted(dedup.values(), key=lambda x: _distance_value(x.get("distance")))
    limited = sorted_items[:10]

    places: List[DietPlanPlaceItem] = []
    for item in limited:
        try:
            x = float(item.get("x"))
            y = float(item.get("y"))
        except Exception:
            continue
        places.append(
            DietPlanPlaceItem(
                id=str(item.get("id")),
                name=item.get("place_name") or "",
                category_group_code=item.get("category_group_code"),
                category_group_name=item.get("category_group_name"),
                category_name=item.get("category_name"),
                address_name=item.get("address_name"),
                road_address_name=item.get("road_address_name"),
                phone=item.get("phone"),
                place_url=item.get("place_url"),
                distance_m=_distance_value(item.get("distance")),
                x=x,
                y=y,
            )
        )

    return DietPlanPlacesResponse(places=places)


@app.post("/api/plan/record", response_model=PlanRecordCreateResult)
def create_records_from_plan(
    payload: PlanRecordCreateRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """체크된 식단 항목을 오늘 기록으로 저장"""
    user = current_user

    if not payload.meals:
        raise HTTPException(status_code=400, detail="meals가 비어 있습니다.")

    record_day = datetime.now().date()
    if payload.record_date:
        try:
            record_day = datetime.strptime(payload.record_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="record_date는 YYYY-MM-DD 형식이어야 합니다.")

    allowed_meal_types = {"breakfast", "lunch", "dinner", "snack", "아침", "점심", "저녁", "간식"}
    meal_type_alias = {
        "아침": "breakfast",
        "점심": "lunch",
        "저녁": "dinner",
        "간식": "snack",
    }

    record_ids: List[int] = []
    for meal in payload.meals:
        if not meal.meal_type:
            raise HTTPException(status_code=400, detail="meal_type은 비어 있을 수 없습니다.")
        raw_type = meal.meal_type.strip().lower()
        if raw_type not in allowed_meal_types:
            raise HTTPException(status_code=400, detail="meal_type은 아침/점심/저녁/간식 중 하나여야 합니다.")
        meal_type = meal_type_alias.get(raw_type, raw_type)

        if not meal.name or not meal.name.strip():
            raise HTTPException(status_code=400, detail="name은 비어 있을 수 없습니다.")

        rec = Record(
            user_number=user.user_number,
            food_name=meal.name.strip(),
            food_calories=float(meal.calories_kcal),
            food_protein=float(meal.protein_g),
            food_carb=float(meal.carbs_g),
            food_fat=float(meal.fat_g),
            meal_type=meal_type,
            record_created_at=datetime.combine(record_day, time(12, 0, 0)),
        )
        db.add(rec)
        db.flush()
        record_ids.append(rec.record_id)

    db.commit()
    return {"record_ids": record_ids}


@app.get("/api/intake/today", response_model=TodayIntakeResponse)
def get_today_intake_from_plan(
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """최신 1일 식단 계획에서 총 영양정보 반환"""
    user = current_user

    plan_record = (
        db.query(UserDietPlan)
        .filter(UserDietPlan.user_number == user.user_number)
        .order_by(UserDietPlan.created_at.desc())
        .first()
    )
    if not plan_record:
        raise HTTPException(status_code=404, detail="식단 계획이 없습니다.")

    try:
        plan = json.loads(plan_record.plan_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="식단 계획 데이터가 손상되었습니다.")

    days = plan.get("days") or []
    if not days:
        raise HTTPException(status_code=500, detail="식단 계획에 day 데이터가 없습니다.")

    day0 = days[0]
    goal_type = plan.get("goal_type") or plan_record.goal_type or "maintain"
    return {
        "goal_type": goal_type,
        "total_calories_kcal": int(day0.get("total_calories_kcal") or 0),
        "total_carbs_g": float(day0.get("total_carbs_g") or 0),
        "total_protein_g": float(day0.get("total_protein_g") or 0),
        "total_fat_g": float(day0.get("total_fat_g") or 0),
    }


@app.post("/api/user/activity-level")
def update_activity_level(
    payload: ActivityLevelUpdateRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 활동 수준 업데이트"""
    level = normalize_activity_level(payload.activity_level)
    if level not in ACTIVITY_FACTORS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="activity_level은 sedentary/light/moderate/active 중 하나여야 합니다.",
        )

    profile = db.query(UserProfile).filter(UserProfile.user_number == current_user.user_number).one_or_none()
    if not profile:
        profile = UserProfile(user_number=current_user.user_number)
        db.add(profile)

    profile.activity_level = level
    db.commit()
    return {"user_number": current_user.user_number, "activity_level": level, "factor": ACTIVITY_FACTORS[level]}


@app.get("/api/inbody", response_model=Optional[InBodyHistoryResponse])
def get_latest_inbody(
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 최신 인바디 기록 조회"""
    record = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == current_user.user_number)
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


@app.delete("/api/inbody-latest")
def delete_latest_inbody(
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 최신 인바디 기록 1건 삭제"""
    record = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == current_user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="삭제할 인바디 기록이 없습니다.")

    deleted_id = record.inbody_id
    db.delete(record)
    db.commit()

    latest = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == current_user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )

    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == current_user.user_number)
        .one_or_none()
    )
    if profile:
        if latest:
            profile.height = latest.height
            profile.weight = latest.weight
            profile.body_fat_percent = latest.body_fat_pct
            profile.skeletal_muscle_mass = latest.skeletal_muscle_mass
            profile.bmr = latest.bmr
        else:
            profile.height = None
            profile.weight = None
            profile.body_fat_percent = None
            profile.skeletal_muscle_mass = None
            profile.bmr = None
            profile.goal_type = None
        db.add(profile)

    # 인바디 삭제 시 목표/식단도 최신 1건만 삭제
    latest_goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == current_user.user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )
    if latest_goal:
        db.delete(latest_goal)

    latest_plan = (
        db.query(UserDietPlan)
        .filter(UserDietPlan.user_number == current_user.user_number)
        .order_by(UserDietPlan.created_at.desc())
        .first()
    )
    if latest_plan:
        db.delete(latest_plan)
    db.commit()

    latest_payload = None
    if latest:
        latest_payload = {
            "inbody_id": latest.inbody_id,
            "measurement_date": latest.measurement_date.isoformat() if latest.measurement_date else None,
            "height": latest.height,
            "weight": latest.weight,
            "body_fat_pct": latest.body_fat_pct,
            "skeletal_muscle_mass": latest.skeletal_muscle_mass,
            "predicted_classify": latest.predicted_classify,
            "classify_name": latest.classify_name,
            "values": {
                k: v for k, v in {
                    "height": latest.height,
                    "weight": latest.weight,
                    "body_fat_mass": latest.body_fat_mass,
                    "body_fat_pct": latest.body_fat_pct,
                    "skeletal_muscle_mass": latest.skeletal_muscle_mass,
                    "bmr": latest.bmr,
                    "inbody_score": latest.inbody_score,
                }.items() if v is not None
            },
            "created_at": latest.created_at.isoformat()
        }

    return {"deleted": True, "inbody_id": deleted_id, "latest_inbody": latest_payload}


@app.get("/api/mypage", response_model=MyPageEnvelopeResponse)
def get_mypage_records(
    current_user: User = Depends(get_current_user_from_token),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """마이페이지 식단 기록 조회"""
    user = current_user
    user_number = current_user.user_number
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


def _sync_daily_activities(
    activities: List[DailyActivityIn],
    db: Session,
    user_number: int,
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
                DailyActivity.user_number == user_number,
                DailyActivity.activity_source == item.activity_source,
                DailyActivity.activity_source_record_id == item.activity_source_record_id,
            ).first()
        else:
            existing = db.query(DailyActivity).filter(
                DailyActivity.user_number == user_number,
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
                user_number=user_number,
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


@app.post("/api/daily-activities/sync", response_model=List[DailyActivityUpsertResult])
def sync_daily_activities(
    activities: List[DailyActivityIn],
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    return _sync_daily_activities(activities, db, current_user.user_number)


@app.post("/api/health-connect/sync", response_model=List[DailyActivityUpsertResult])
def sync_health_connect_activities(
    activities: List[DailyActivityIn],
    current_user: User = Depends(get_current_user_from_token),
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
    return _sync_daily_activities(normalized, db, current_user.user_number)


@app.get("/api/inbody-history", response_model=List[InBodyHistoryResponse])
def get_inbody_history(
    current_user: User = Depends(get_current_user_from_token),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """
    사용자의 인바디 측정 히스토리 조회
    """
    records = db.query(InBodyRecord).filter(
        InBodyRecord.user_number == current_user.user_number
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
    current_user: User = Depends(get_current_user_from_token),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    인바디 사진 OCR -> 핵심 항목 추출 -> users 테이블 최신값 업데이트
    """
    user_number = current_user.user_number

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
    current_user: User = Depends(get_current_user_from_token),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    인바디 사진 OCR -> 핵심 항목 추출 -> users 테이블 최신값 업데이트
    """
    user_number = current_user.user_number

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
def get_record(
    date: str,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """
    특정 날짜의 식단 기록 조회
    - date: "YYYY-MM-DD"
    """
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date는 YYYY-MM-DD 형식이어야 합니다.")

    start = day
    end = day + timedelta(days=1)

    rows = db.query(Record).filter(
        Record.user_number == current_user.user_number,
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

@app.get("/api/calendar", response_model=CalendarMarkedDatesResponse)
def get_calendar_marked_dates(
    year: int,
    month: int,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """
    사용자가 기록한 날짜 목록 반환 (캘린더 표시용)
    - year: 4자리 연도
    - month: 1~12
    """
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month는 1~12 범위여야 합니다.")

    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)

    dates = (
        db.query(func.date(Record.record_created_at))
        .filter(
            Record.user_number == current_user.user_number,
            Record.record_created_at >= start_date,
            Record.record_created_at < end_date,
        )
        .distinct()
        .order_by(func.date(Record.record_created_at))
        .all()
    )

    return {
        "year": year,
        "month": month,
        "dates": [d[0].isoformat() for d in dates if d and d[0]],
    }
    
@app.delete("/api/record", status_code=204)
def delete_day_records(
    date: str = Query(..., description="YYYY-MM-DD"),
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """
    특정 날짜의 식단 기록 전체 삭제
    - date: "YYYY-MM-DD"
    """
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date는 YYYY-MM-DD 형식이어야 합니다.")

    start = day
    end = day + timedelta(days=1)

    db.query(Record).filter(
        Record.user_number == current_user.user_number,
        Record.record_created_at >= start,
        Record.record_created_at < end,
    ).delete(synchronize_session=False)

    db.commit()

    return
    


@app.delete("/api/record/{record_id}", response_model=RecordDeleteResponse)
def delete_record(
    record_id: int,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """식단 기록 삭제"""
    record = (
        db.query(Record)
        .filter(Record.record_id == record_id, Record.user_number == current_user.user_number)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="식단 기록을 찾을 수 없습니다.")

    db.delete(record)
    db.commit()

    return {"record_id": record_id, "message": "식단 기록이 삭제되었습니다."}

@app.post("/api/vision/food")
async def vision_food(
    current_user: User = Depends(get_current_user_from_token),
    meal_type: str = Form(...),
    record_date: Optional[str] = Form(None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        user_number = current_user.user_number
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
            record_created_at = datetime.combine(day, time(12, 0, 0))

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
def classify_by_user(
    payload: BodyTypeFromUserRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    record = db.query(InBodyRecord).filter(
        InBodyRecord.user_number == current_user.user_number
    ).order_by(InBodyRecord.created_at.desc()).first()

    if not record:
        raise HTTPException(status_code=404, detail="인바디 기록이 없습니다.")

    profile = db.query(UserProfile).filter(
        UserProfile.user_number == current_user.user_number
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

@app.post("/api/node/run", response_model=NodeRunResponse)
def run_node_pipeline(
    payload: NodeRunRequest,
    current_user: User = Depends(get_current_user_from_token),
):
    if (payload.lat is None or payload.lng is None) and not (payload.address_text and payload.address_text.strip()):
        raise HTTPException(status_code=400, detail="lat/lng 또는 address_text 중 하나는 필수입니다.")

    graph = build_graph()
    state = {
        "user_number": current_user.user_number,
        "label": "current",
        "address_text": (payload.address_text or "").strip(),
        "lat": payload.lat,
        "lng": payload.lng,
        "radius_m": payload.radius_m or 500,
        "errors": [],
    }
    result = graph.invoke(state)
    return NodeRunResponse(
        user_number=current_user.user_number,
        label=(result.get("label") or "current"),
        location_profile_id=result.get("location_profile_id"),
        restaurant_ids=result.get("restaurant_ids", []) or [],
        menu_item_ids=result.get("menu_item_ids", []) or [],
        nutrition_ids=result.get("nutrition_ids", []) or [],
        errors=result.get("errors", []) or [],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
