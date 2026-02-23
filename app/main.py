import os
import json
import traceback
import time
import asyncio
import logging
import time as time_module
import re
import math
from datetime import date, datetime, timezone, time, timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from fastapi.responses import RedirectResponse
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from app.food_lens import decide_food_gpt_only
from app.s3 import upload_image_to_s3
from app.database import get_db, engine, Base, SessionLocal
from app.inbody import InbodyInput, BodyTypeResult, classify_body_type
from app.models import Record, InBodyRecord, User, UserProfile, FoodAnalysisResult
from typing import List, Optional
from app import models
from app.inbody_ocr import extract_key_values, format_key_values, upstage_ocr_from_bytes, update_user_inbody
from app.models import (
    Record,
    InBodyRecord,
    User,
    UserProfile,
    UserGoal,
    DailyActivity,
    UserDietPlan,
    LocationProfile,
    Restaurant,
    RestaurantSnapshot,
    MenuItem,
    NutritionFacts,
)
from app.goal_rules import estimate_target_calorie, normalize_activity_level, ACTIVITY_FACTORS, infer_goal_type
from app.schemas import (
    ActivityLevelUpdateRequest,
    AuthResponse,
    BodyTypeFromUserRequest,
    CalendarMarkedDatesResponse,
    CollectorRunRequest,
    CollectorRunResponse,
    DailyActivityIn,
    DailyActivityUpsertResult,
    DietPlan3DaysRequest,
    DietPlanContextRequest,
    DietPlanPlaceItem,
    DietPlanPlacesRequest,
    DietPlanPlacesResponse,
    DietPlanWithIntakeResponse,
    DietRecordRequest,
    DietRecordResponse,
    InBodyHistoryResponse,
    InBodyOcrResponse,
    LogoutResponse,
    MyPageEnvelopeResponse,
    MyPageResponse,
    PlanMealRecordIn,
    PlanRecordCreateRequest,
    PlanRecordCreateResult,
    PersonalizedMenuItem,
    PersonalizedMenuRequest,
    PersonalizedMenuResponse,
    RecordDeleteResponse,
    RecommendMealRecordIn,
    RecommendRecordResultItem,
    RecommendRecordCreateRequest,
    SocialCheckRequest,
    SocialCheckResponse,
    SocialRegisterRequest,
    TodayIntakeResponse,
    UserGoalResponse,
    UserGoalUpdateRequest,
    UserGoalWithPlanResponse,
    UserResponse,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client
import requests
try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None
try:
    from app.meal_plan_ai import MealPlanItem, DayMealPlan, OneDayMealPlan, generate_one_day_plan
except ModuleNotFoundError:
    MealPlanItem = dict
    DayMealPlan = dict
    OneDayMealPlan = dict

    def generate_one_day_plan(*args, **kwargs):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="meal_plan_ai 모듈이 비활성화되어 식단 생성 기능을 사용할 수 없습니다.",
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
KAKAO_LOCAL_ADDRESS_API_URL = "https://dapi.kakao.com/v2/local/search/address.json"
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TIMEOUT = int(os.getenv("EXTERNAL_API_TIMEOUT", "300"))
openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=TIMEOUT) if OpenAI and OPENAI_API_KEY else None

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
        "http://localhost:3000,https://food-plan-frontend-deploy.vercel.app" # 하나의 문자열로 묶음
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


def _normalize_location_label(raw: Optional[str]) -> str:
    label = (raw or "").strip().lower()
    if label == "work":
        return "company"
    return label


def _validate_location_label(raw: Optional[str]) -> str:
    label = _normalize_location_label(raw)
    if label not in {"home", "company"}:
        raise HTTPException(status_code=400, detail="label은 home/company 중 하나여야 합니다.")
    return label


def _get_location_profile(
    db: Session,
    user_number: int,
    label: str,
) -> Optional[LocationProfile]:
    profile = (
        db.query(LocationProfile)
        .filter(LocationProfile.user_number == user_number, LocationProfile.label == label)
        .first()
    )
    if profile is None and label == "company":
        profile = (
            db.query(LocationProfile)
            .filter(LocationProfile.user_number == user_number, LocationProfile.label == "work")
            .first()
        )
    return profile


def _migrate_work_to_company(db: Session, user_number: Optional[int] = None) -> int:
    query = db.query(LocationProfile).filter(LocationProfile.label == "work")
    if user_number is not None:
        query = query.filter(LocationProfile.user_number == user_number)
    updated = 0
    for profile in query.all():
        profile.label = "company"
        updated += 1
    if updated:
        db.flush()
    return updated


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


def _resolve_goal_type_for_user(db: Session, user_number: int) -> str:
    latest_goal = (
        db.query(UserGoal)
        .filter(UserGoal.user_number == user_number)
        .order_by(UserGoal.created_at.desc())
        .first()
    )
    if latest_goal and latest_goal.goal_type:
        normalized = _normalize_goal_type(latest_goal.goal_type)
        if normalized in {"diet", "maintain", "bulk"}:
            return normalized

    profile = db.query(UserProfile).filter(UserProfile.user_number == user_number).first()
    if profile and profile.goal_type:
        normalized = _normalize_goal_type(profile.goal_type)
        if normalized in {"diet", "maintain", "bulk"}:
            return normalized

    return "maintain"


def _resolve_tdee_kcal_for_user(db: Session, user: User) -> int:
    profile = db.query(UserProfile).filter(UserProfile.user_number == user.user_number).first()
    latest_inbody = (
        db.query(InBodyRecord)
        .filter(InBodyRecord.user_number == user.user_number)
        .order_by(InBodyRecord.created_at.desc())
        .first()
    )

    bmr = latest_inbody.bmr if latest_inbody and latest_inbody.bmr else None
    weight = latest_inbody.weight if latest_inbody and latest_inbody.weight else None
    if weight is None and profile and profile.weight:
        weight = profile.weight

    if bmr is None:
        # Fallback rule in PRD: 인바디 미입력 시 표준값 사용
        fallback_weight = float(weight) if weight is not None else 70.0
        bmr = 24.0 * fallback_weight

    activity_level = normalize_activity_level(profile.activity_level) if profile else "moderate"
    factor = ACTIVITY_FACTORS.get(activity_level or "moderate", ACTIVITY_FACTORS["moderate"])
    return int(round(float(bmr) * float(factor)))


def _goal_daily_target_kcal(tdee_kcal: int, goal_type: str) -> int:
    if goal_type == "diet":
        return max(1200, tdee_kcal - 400)
    if goal_type == "bulk":
        return tdee_kcal + 400
    return tdee_kcal


def _meal_targets_from_daily(daily_kcal: int) -> dict[str, int]:
    breakfast = int(round(daily_kcal * 0.30))
    lunch = int(round(daily_kcal * 0.35))
    dinner = max(0, daily_kcal - breakfast - lunch)
    return {"breakfast": breakfast, "lunch": lunch, "dinner": dinner}


def _goal_macro_ratio(goal_type: str) -> tuple[float, float, float]:
    if goal_type == "diet":
        return (0.40, 0.30, 0.30)  # carb, protein, fat
    if goal_type == "bulk":
        return (0.50, 0.25, 0.25)
    return (0.45, 0.25, 0.30)


def _menu_score(candidate: dict, meal_target_kcal: int, goal_type: str) -> float:
    calories = float(candidate["calories_kcal"])
    carbs = float(candidate["carbs_g"])
    protein = float(candidate["protein_g"])
    fat = float(candidate["fat_g"])
    distance_m = float(candidate["distance_m"] or 9999.0)

    calorie_penalty = abs(calories - meal_target_kcal) / max(1.0, float(meal_target_kcal))
    ratio_c, ratio_p, ratio_f = _goal_macro_ratio(goal_type)
    macro_total = (carbs * 4.0) + (protein * 4.0) + (fat * 9.0)
    if macro_total <= 0.0:
        macro_penalty = 1.0
    else:
        macro_penalty = (
            abs((carbs * 4.0) / macro_total - ratio_c)
            + abs((protein * 4.0) / macro_total - ratio_p)
            + abs((fat * 9.0) / macro_total - ratio_f)
        )

    distance_penalty = min(distance_m / 1000.0, 1.5) * 0.15
    confidence_bonus = float(candidate["confidence"]) * 0.25
    return -calorie_penalty - macro_penalty - distance_penalty + confidence_bonus


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    base = (text or "").lower()
    return any(keyword in base for keyword in keywords)


def _is_likely_open_for_meal(candidate: dict, meal: str) -> bool:
    """
    실제 영업시간 API가 없어서, 업종/메뉴명 기반으로 시간대 부적합 후보를 제외한다.
    """
    restaurant_name = str(candidate.get("restaurant_name") or "")
    restaurant_category = str(candidate.get("restaurant_category") or "")
    menu_name = str(candidate.get("menu_name") or "")
    merged = f"{restaurant_name} {restaurant_category} {menu_name}".lower()

    # 시간대 무관: 술집/주점 + 빙과류 중심 후보는 추천에서 제외
    blocked_always = (
        "술집",
        "주점",
        "호프",
        "포차",
        "이자카야",
        "bar",
        "pub",
        "칵테일",
        "와인바",
        "맥주집",
        "요리주점",
        "선술집",
        "룸살롱",
        "클럽",
        "아이스크림",
        "빙수",
        "젤라또",
        "샤베트",
        "프로즌요거트",
    )
    if _contains_any(merged, blocked_always):
        return False

    if meal == "breakfast":
        blocked_breakfast = (
            "참치",
            "횟집",
            "회",
            "불고기",
            "삼계탕",
            "족발",
            "보쌈",
            "곱창",
            "막창",
            "곱창",
            "닭발",
            "양꼬치",
            "삼겹살",
            "고기집",
            "안주",
            "소주",
            "하이볼",
            "무한리필",
        )
        if _contains_any(merged, blocked_breakfast):
            return False

        # 아침에는 지나치게 헤비한 메뉴를 1차 제외
        calories = float(candidate.get("calories_kcal") or 0.0)
        if calories > 850:
            return False
    return True


def _is_goal_compatible(candidate: dict, goal_type: str, meal_target_kcal: int) -> bool:
    menu_name = str(candidate.get("menu_name") or "").lower()
    calories = float(candidate.get("calories_kcal") or 0.0)
    protein = float(candidate.get("protein_g") or 0.0)
    fat = float(candidate.get("fat_g") or 0.0)

    if goal_type == "diet":
        blocked_menu_keywords = (
            "버거",
            "빅맥",
            "튀김",
            "돈까스",
            "피자",
            "라면",
            "치킨",
            "족발",
            "삼겹",
            "햄버거",
        )
        if _contains_any(menu_name, blocked_menu_keywords):
            return False
        if calories > meal_target_kcal * 1.10:
            return False
        if fat > 20:
            return False
        if protein < 16:
            return False

    return True


def _query_verified_menu_candidates(
    db: Session,
    user_number: int,
    label: str,
    radius_m: int,
) -> list[dict]:
    norm_label = _validate_location_label(label)
    location = (
        db.query(LocationProfile)
        .filter(
            LocationProfile.user_number == user_number,
            LocationProfile.label == norm_label,
        )
        .first()
    )
    if location is None and norm_label == "company":
        location = (
            db.query(LocationProfile)
            .filter(
                LocationProfile.user_number == user_number,
                LocationProfile.label == "work",
            )
            .first()
        )
    if location is None:
        raise HTTPException(status_code=404, detail=f"{norm_label} 위치 프로필이 없습니다.")

    rows = (
        db.query(
            Restaurant.restaurant_id,
            Restaurant.name.label("restaurant_name"),
            Restaurant.category.label("restaurant_category"),
            Restaurant.place_url.label("place_url"),
            MenuItem.menu_id,
            MenuItem.name.label("menu_name"),
            MenuItem.price,
            RestaurantSnapshot.distance_m,
            NutritionFacts.calories_kcal,
            NutritionFacts.carbs_g,
            NutritionFacts.protein_g,
            NutritionFacts.fat_g,
            NutritionFacts.confidence,
            NutritionFacts.nutrition_id,
        )
        .join(RestaurantSnapshot, RestaurantSnapshot.restaurant_id == Restaurant.restaurant_id)
        .join(MenuItem, MenuItem.restaurant_id == Restaurant.restaurant_id)
        .join(NutritionFacts, NutritionFacts.menu_item_id == MenuItem.menu_id)
        .filter(RestaurantSnapshot.location_profile_id == location.location_id)
        .filter(RestaurantSnapshot.distance_m <= radius_m)
        .filter(MenuItem.name.isnot(None), MenuItem.price.isnot(None))
        .filter(
            NutritionFacts.calories_kcal.isnot(None),
            NutritionFacts.carbs_g.isnot(None),
            NutritionFacts.protein_g.isnot(None),
            NutritionFacts.fat_g.isnot(None),
        )
        .filter(NutritionFacts.confidence.isnot(None), NutritionFacts.confidence >= 0.6)
        .order_by(NutritionFacts.menu_item_id.asc(), NutritionFacts.nutrition_id.desc())
        .all()
    )

    latest_by_menu: dict[int, dict] = {}
    for row in rows:
        row_map = row._asdict()
        menu_id = int(row_map["menu_id"])
        if menu_id in latest_by_menu:
            continue
        latest_by_menu[menu_id] = {
            "restaurant_id": int(row_map["restaurant_id"]),
            "restaurant_name": str(row_map["restaurant_name"]),
            "restaurant_category": str(row_map.get("restaurant_category") or ""),
            "place_url": str(row_map.get("place_url") or ""),
            "menu_id": menu_id,
            "menu_name": str(row_map["menu_name"]),
            "price": float(row_map["price"]),
            "distance_m": float(row_map["distance_m"] or 0.0),
            "calories_kcal": float(row_map["calories_kcal"]),
            "carbs_g": float(row_map["carbs_g"]),
            "protein_g": float(row_map["protein_g"]),
            "fat_g": float(row_map["fat_g"]),
            "confidence": float(row_map["confidence"]),
        }
    return list(latest_by_menu.values())


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
            timeout=TIMEOUT,
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
            timeout=TIMEOUT,
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
            timeout=TIMEOUT,
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


def _kakao_geocode_address(address_text: str) -> tuple[float, float]:
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=500, detail="KAKAO_REST_API_KEY 환경변수가 필요합니다.")
    query = (address_text or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="address_text가 필요합니다.")

    try:
        resp = requests.get(
            KAKAO_LOCAL_ADDRESS_API_URL,
            headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
            params={"query": query, "size": 1},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            print("\n" + "!" * 40)
            print("❌ 카카오 API 호출 실패!")
            print(f"❌ 요청 주소: {query}")
            print(f"❌ 응답 상태 코드: {resp.status_code}")
            print(f"❌ 카카오 서버의 답변: {resp.text}")
            print("!" * 40 + "\n")
            raise HTTPException(status_code=502, detail="Kakao Geocoding API 오류")
        data = resp.json()
        docs = data.get("documents") or []
        if not docs:
            raise HTTPException(status_code=404, detail="주소를 좌표로 변환하지 못했습니다.")
        first = docs[0]
        x = float(first.get("x"))
        y = float(first.get("y"))
        return y, x
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Kakao geocode failed: %s", exc)
        raise HTTPException(status_code=502, detail="Kakao Geocoding API 요청 실패")


def _kakao_collect_restaurants(lat: float, lng: float, radius_m: int, max_count: int = 100) -> list[dict]:
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=500, detail="KAKAO_REST_API_KEY 환경변수가 필요합니다.")

    radius = max(50, min(int(radius_m), 20000))
    target_count = max(1, min(int(max_count), 100))
    merged: dict[str, dict] = {}

    max_pages = min(45, max(1, (target_count + 14) // 15 + 1))
    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(
                KAKAO_LOCAL_CATEGORY_API_URL,
                headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
                params={
                    "category_group_code": "FD6",
                    "x": lng,
                    "y": lat,
                    "radius": radius,
                    "sort": "distance",
                    "size": 15,
                    "page": page,
                },
                timeout=TIMEOUT,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            docs = data.get("documents") or []
            if not docs:
                break
            for doc in docs:
                pid = str(doc.get("id") or "").strip()
                if not pid or pid in merged:
                    continue
                merged[pid] = doc
            if len(merged) >= target_count:
                break
            meta = data.get("meta") or {}
            if meta.get("is_end"):
                break
        except Exception:
            break

    def _distance(item: dict) -> int:
        try:
            return int(item.get("distance") or 10**9)
        except Exception:
            return 10**9

    ordered = sorted(merged.values(), key=_distance)
    return ordered[:target_count]


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lng2) - float(lng1))
    a = (math.sin(dp / 2.0) ** 2) + (math.cos(p1) * math.cos(p2) * (math.sin(dl / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r * c


def _snapshot_cache_fresh_cutoff(now_utc: datetime) -> datetime:
    # 기본 정책: 당일 00:00 이후 스냅샷만 재사용
    use_daily = str(os.getenv("SNAPSHOT_CACHE_UNTIL_MIDNIGHT", "1")).strip().lower() in {"1", "true", "yes", "on"}
    if use_daily:
        kst = timezone(timedelta(hours=9))
        local_now = now_utc.astimezone(kst)
        day_start_local = datetime.combine(local_now.date(), time.min, tzinfo=kst)
        return day_start_local.astimezone(timezone.utc)

    ttl_minutes = max(10, min(int(os.getenv("SNAPSHOT_CACHE_TTL_MINUTES", "180")), 1440))
    return now_utc - timedelta(minutes=ttl_minutes)


def _load_restaurants_from_location_snapshots(
    db: Session,
    location_profile_id: int,
    max_count: int,
) -> list[Restaurant]:
    rows = (
        db.query(Restaurant)
        .join(RestaurantSnapshot, RestaurantSnapshot.restaurant_id == Restaurant.restaurant_id)
        .filter(RestaurantSnapshot.location_profile_id == location_profile_id)
        .order_by(func.coalesce(RestaurantSnapshot.distance_m, 1e9).asc(), RestaurantSnapshot.snapshot_id.asc())
        .limit(max_count)
        .all()
    )
    return rows


def _find_reusable_location_profile(
    db: Session,
    lat: float,
    lng: float,
    max_distance_m: int,
    fresh_cutoff: datetime,
    current_location_id: Optional[int] = None,
) -> Optional[LocationProfile]:
    profiles = (
        db.query(LocationProfile)
        .filter(LocationProfile.lat.isnot(None), LocationProfile.lng.isnot(None))
        .all()
    )
    if not profiles:
        return None

    candidates: list[tuple[float, LocationProfile]] = []
    for profile in profiles:
        if profile.lat is None or profile.lng is None:
            continue
        try:
            dist = _haversine_m(lat, lng, float(profile.lat), float(profile.lng))
        except Exception:
            continue
        if dist <= float(max_distance_m):
            candidates.append((dist, profile))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (0 if (current_location_id and x[1].location_id == current_location_id) else 1, x[0]))

    for _, profile in candidates:
        latest_collected = (
            db.query(func.max(RestaurantSnapshot.collected_at))
            .filter(RestaurantSnapshot.location_profile_id == profile.location_id)
            .scalar()
        )
        if latest_collected is None:
            continue
        if latest_collected.tzinfo is None:
            latest_collected = latest_collected.replace(tzinfo=timezone.utc)
        if latest_collected >= fresh_cutoff:
            return profile
    return None


def _copy_snapshots_to_location(
    db: Session,
    source_location_id: int,
    target_location: LocationProfile,
    max_count: int,
    collected_at: datetime,
) -> list[Restaurant]:
    rows = (
        db.query(RestaurantSnapshot, Restaurant)
        .join(Restaurant, Restaurant.restaurant_id == RestaurantSnapshot.restaurant_id)
        .filter(RestaurantSnapshot.location_profile_id == source_location_id)
        .order_by(func.coalesce(RestaurantSnapshot.distance_m, 1e9).asc(), RestaurantSnapshot.snapshot_id.asc())
        .limit(max_count)
        .all()
    )
    if not rows:
        return []

    restaurant_ids = [restaurant.restaurant_id for _, restaurant in rows]
    existing_rows = (
        db.query(RestaurantSnapshot)
        .filter(
            RestaurantSnapshot.location_profile_id == target_location.location_id,
            RestaurantSnapshot.restaurant_id.in_(restaurant_ids),
        )
        .all()
    )
    existing_map = {row.restaurant_id: row for row in existing_rows}

    out: list[Restaurant] = []
    for source_snapshot, restaurant in rows:
        distance_m = source_snapshot.distance_m
        if (
            target_location.lat is not None
            and target_location.lng is not None
            and restaurant.lat is not None
            and restaurant.lng is not None
        ):
            try:
                distance_m = _haversine_m(
                    float(target_location.lat),
                    float(target_location.lng),
                    float(restaurant.lat),
                    float(restaurant.lng),
                )
            except Exception:
                pass

        target_snapshot = existing_map.get(restaurant.restaurant_id)
        if target_snapshot is None:
            target_snapshot = RestaurantSnapshot(
                location_profile_id=target_location.location_id,
                restaurant_id=restaurant.restaurant_id,
                distance_m=distance_m,
                collected_at=collected_at,
            )
            db.add(target_snapshot)
        else:
            target_snapshot.distance_m = distance_m
            target_snapshot.collected_at = collected_at
        out.append(restaurant)
    return out


def _upsert_location_profile(
    db: Session,
    user_number: int,
    label: str,
    address_text: str,
    lat: float,
    lng: float,
) -> LocationProfile:
    norm_label = _validate_location_label(label)
    profile = (
        db.query(LocationProfile)
        .filter(LocationProfile.user_number == user_number, LocationProfile.label == norm_label)
        .one_or_none()
    )
    if profile is None and norm_label == "company":
        profile = (
            db.query(LocationProfile)
            .filter(LocationProfile.user_number == user_number, LocationProfile.label == "work")
            .one_or_none()
        )
        if profile is not None:
            profile.label = "company"
    if profile is None:
        profile = LocationProfile(
            user_number=user_number,
            label=norm_label,
            address_text=address_text,
            lat=lat,
            lng=lng,
        )
        db.add(profile)
        db.flush()
    else:
        profile.address_text = address_text
        profile.lat = lat
        profile.lng = lng
    return profile


def _upsert_restaurant_and_snapshot(
    db: Session,
    location_profile_id: int,
    kakao_doc: dict,
) -> Restaurant:
    source_place_id = str(kakao_doc.get("id") or "").strip()
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.source == "kakao", Restaurant.source_place_id == source_place_id)
        .one_or_none()
    )
    if restaurant is None:
        restaurant = Restaurant(
            source="kakao",
            source_place_id=source_place_id,
            name=(kakao_doc.get("place_name") or "").strip(),
            category=(kakao_doc.get("category_name") or kakao_doc.get("category_group_name")),
            address_text=(kakao_doc.get("road_address_name") or kakao_doc.get("address_name") or "").strip(),
            lat=float(kakao_doc.get("y")) if kakao_doc.get("y") else None,
            lng=float(kakao_doc.get("x")) if kakao_doc.get("x") else None,
            phone=(kakao_doc.get("phone") or "").strip() or None,
            place_url=(kakao_doc.get("place_url") or "").strip() or None,
        )
        db.add(restaurant)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            db.rollback()
            restaurant = (
                db.query(Restaurant)
                .filter(Restaurant.source == "kakao", Restaurant.source_place_id == source_place_id)
                .one_or_none()
            )
            if restaurant is None:
                raise
    else:
        restaurant.name = (kakao_doc.get("place_name") or restaurant.name or "").strip()
        restaurant.category = kakao_doc.get("category_name") or kakao_doc.get("category_group_name") or restaurant.category
        restaurant.address_text = (kakao_doc.get("road_address_name") or kakao_doc.get("address_name") or restaurant.address_text or "").strip()
        restaurant.lat = float(kakao_doc.get("y")) if kakao_doc.get("y") else restaurant.lat
        restaurant.lng = float(kakao_doc.get("x")) if kakao_doc.get("x") else restaurant.lng
        restaurant.phone = (kakao_doc.get("phone") or restaurant.phone or "").strip() or None
        restaurant.place_url = (kakao_doc.get("place_url") or restaurant.place_url or "").strip() or None

    snapshot = (
        db.query(RestaurantSnapshot)
        .filter(
            RestaurantSnapshot.location_profile_id == location_profile_id,
            RestaurantSnapshot.restaurant_id == restaurant.restaurant_id,
        )
        .one_or_none()
    )
    distance_m = None
    try:
        distance_m = float(kakao_doc.get("distance")) if kakao_doc.get("distance") else None
    except Exception:
        distance_m = None
    if snapshot is None:
        snapshot = RestaurantSnapshot(
            location_profile_id=location_profile_id,
            restaurant_id=restaurant.restaurant_id,
            distance_m=distance_m,
            collected_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
    else:
        snapshot.distance_m = distance_m
        snapshot.collected_at = datetime.now(timezone.utc)
    return restaurant


def _safe_float(text: str) -> Optional[float]:
    try:
        return float(str(text).strip())
    except Exception:
        return None


def _truncate_source_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:500]


def _safe_int_price(value: object) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = re.sub(r"[^0-9]", "", raw)
    if not raw:
        return None
    try:
        price = int(raw)
    except Exception:
        return None
    if price < 1000 or price > 200000:
        return None
    return price


def _extract_kakao_place_id(place_url: str) -> Optional[str]:
    url = (place_url or "").strip()
    if not url:
        return None
    m = re.search(r"/([0-9]{6,})/?(?:[?#].*)?$", url)
    if not m:
        return None
    return m.group(1)


def _extract_menu_candidates_from_kakao_payload(payload: object, restaurant_name: str) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    name_keys = ("menu", "menu_name", "menuname", "name", "title", "nm")
    price_keys = ("price", "menu_price", "menuprice", "cost", "amount", "fee")

    def pick_value(item: dict, keys: tuple[str, ...]) -> Optional[object]:
        for key, value in item.items():
            key_norm = str(key).strip().lower().replace(" ", "").replace("-", "_")
            if key_norm in keys:
                return value
        return None

    def add_candidate(name_raw: object, price_raw: object) -> None:
        if name_raw is None or price_raw is None:
            return
        name = _sanitize_menu_name(str(name_raw), restaurant_name)
        price = _safe_int_price(price_raw)
        if price is None or not _is_valid_menu_name(name, restaurant_name):
            return
        key = (_normalize_menu_name(name), price)
        if not key[0] or key in seen:
            return
        seen.add(key)
        out.append(
            {
                "name": name,
                "price": float(price),
                "description": "kakao-place-menu",
                "menu_confidence": 0.95,
            }
        )

    def walk(node: object) -> None:
        if isinstance(node, dict):
            name_value = pick_value(node, name_keys)
            price_value = pick_value(node, price_keys)
            add_candidate(name_value, price_value)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return out[:20]


def _fetch_kakao_place_menu_candidates(restaurant: Restaurant) -> list[dict]:
    place_url = (restaurant.place_url or "").strip()
    place_id = _extract_kakao_place_id(place_url)
    if not place_url or not place_id:
        return []

    headers = {"User-Agent": "Mozilla/5.0", "Referer": place_url}
    menus: list[dict] = []

    # Kakao place 내부 JSON 엔드포인트(메뉴 탭 데이터 포함 가능)
    try:
        resp = requests.get(
            f"https://place.map.kakao.com/main/v/{place_id}",
            headers=headers,
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            payload = resp.json()
            menus = _extract_menu_candidates_from_kakao_payload(payload, restaurant.name or "")
    except Exception as exc:
        logger.warning("kakao place menu json parse failed place_id=%s error=%s", place_id, exc)

    # JSON에서 못 뽑으면 place 페이지 본문에서 보조 추출
    if not menus:
        try:
            resp = requests.get(place_url, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 200:
                text = resp.text or ""
                menus = _extract_menu_candidates_from_text(text, restaurant.name or "")
        except Exception as exc:
            logger.warning("kakao place menu html parse failed place_url=%s error=%s", place_url, exc)

    for menu in menus:
        menu["source_url"] = f"{place_url}#menu"
    return menus[:10]


def _search_web(query: str, max_results: int = 8) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []

    try:
        if SERPER_API_KEY:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": q, "num": max_results},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                organic = data.get("organic") or []
                return [
                    {
                        "title": item.get("title") or "",
                        "snippet": item.get("snippet") or "",
                        "url": item.get("link") or "",
                    }
                    for item in organic
                ]

        if TAVILY_API_KEY:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": q,
                    "search_depth": "basic",
                    "max_results": max_results,
                },
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("results") or []
                return [
                    {
                        "title": row.get("title") or "",
                        "snippet": row.get("content") or "",
                        "url": row.get("url") or "",
                    }
                    for row in rows
                ]

        if SERPAPI_API_KEY:
            resp = requests.get(
                "https://serpapi.com/search.json",
                params={"q": q, "api_key": SERPAPI_API_KEY, "num": max_results, "hl": "ko"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                organic = data.get("organic_results") or []
                return [
                    {
                        "title": item.get("title") or "",
                        "snippet": item.get("snippet") or "",
                        "url": item.get("link") or "",
                    }
                    for item in organic
                ]
    except Exception as exc:
        logger.warning("web search failed for query=%s error=%s", q, exc)
    return []


def _sanitize_menu_name(name: str, restaurant_name: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    cleaned = re.sub(r"[|•]+", " ", cleaned)
    cleaned = re.sub(r"\s*[-–]\s*(instagram|blog|블로그).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[\s\.\-·•:;,]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:;,.\u00b7")

    # "가게명 메뉴명" 형태면 가게명 접두어 제거
    rname = re.sub(r"\s+", "", (restaurant_name or "").strip().lower())
    cname = re.sub(r"\s+", "", cleaned.lower())
    if rname and cname.startswith(rname):
        stripped = cleaned[len(restaurant_name):].strip(" -:;,.")
        if stripped:
            cleaned = stripped
    return cleaned


def _restaurant_name_tokens(restaurant_name: str) -> list[str]:
    raw = re.sub(r"[\(\)\[\],/]", " ", restaurant_name or "")
    tokens = []
    for tk in re.split(r"\s+", raw.strip()):
        tk = tk.strip()
        if len(tk) < 2:
            continue
        low = tk.lower()
        if low in {"점", "본점", "지점", "branch", "store", "the"}:
            continue
        tokens.append(low)
    return tokens


def _restaurant_row_relevance(row: dict, restaurant: Restaurant) -> float:
    title = str(row.get("title") or "")
    snippet = str(row.get("snippet") or "")
    url = str(row.get("url") or "")
    text = f"{title} {snippet}".lower()
    text_norm = re.sub(r"\s+", "", text)
    full_name = (restaurant.name or "").strip().lower()
    full_name_norm = re.sub(r"\s+", "", full_name)

    score = 0.0
    if full_name_norm and full_name_norm in text_norm:
        score += 0.65
    if restaurant.place_url and restaurant.place_url in url:
        score += 0.45

    token_hits = 0
    for tk in _restaurant_name_tokens(restaurant.name or ""):
        if tk in text:
            token_hits += 1
    if token_hits >= 2:
        score += 0.35
    elif token_hits == 1:
        score += 0.18

    addr_tokens = [t.lower() for t in re.split(r"\s+", (restaurant.address_text or "").strip()) if len(t) >= 2][:2]
    addr_hits = sum(1 for tk in addr_tokens if tk in text)
    if addr_hits:
        score += min(0.15 * addr_hits, 0.3)

    return max(0.0, min(score, 1.0))


def _is_relevant_row_for_restaurant(row: dict, restaurant: Restaurant) -> bool:
    relevance = _restaurant_row_relevance(row, restaurant)
    min_relevance = _safe_float(os.getenv("MENU_ROW_RELEVANCE_MIN", "0.58")) or 0.58
    return relevance >= min_relevance


def _is_valid_menu_name(name: str, restaurant_name: str = "") -> bool:
    value = _sanitize_menu_name(name, restaurant_name)
    if not value:
        return False
    if len(value) < 2 or len(value) > 28:
        return False
    if len(value.split()) > 4:
        return False
    if not re.search(r"[가-힣A-Za-z]", value):
        return False

    bad_keywords = [
        "강남역", "추천", "맛집", "혼밥", "후기", "리뷰", "방문", "다녀왔", "instagram",
        "인스타", "블로그", "주소", "전화", "영업시간", "예약", "주차", "원산지",
        "메뉴판", "무한리필", "셀프바", "출구점", "점심", "저녁", "브레이크타임",
        "이전 페이지", "완벽한 하루", "런치", "lunch", "dinner",
    ]
    lower = value.lower()
    if any(k in lower for k in bad_keywords):
        return False

    # 불필요한 기호로 시작하는 케이스 방지 (예: "· 잠봉 펜네 샐러드")
    if re.match(r"^[\.\-·•]", value):
        return False

    # 음식명으로 보기 어려운 일반 문구 차단
    generic_phrase_patterns = [
        r"추천\s*$",
        r"맛집\s*$",
        r"^\s*이전\s*페이지",
        r"^\s*완벽한\s*하루\s*$",
    ]
    if any(re.search(p, lower, flags=re.IGNORECASE) for p in generic_phrase_patterns):
        return False

    food_keyword_patterns = [
        r"(밥|국|탕|찌개|전골|국수|면|냉면|라면|우동|소바|덮밥|비빔밥|김밥|죽|포케|샐러드)",
        r"(볶음|구이|튀김|찜|수육|보쌈|족발|불고기|갈비|스테이크|돈까스|카츠|치킨|버거|샌드위치)",
        r"(피자|파스타|리조또|펜네|라자냐|타코|케밥|쌀국수|분짜|샤브|스키야키)",
        r"(커피|라떼|에이드|티|차|주스|스무디|디저트|케이크|빙수)",
        r"(salad|pasta|pizza|steak|burger|sandwich|ramen|udon|soup|noodle|rice|set)",
    ]
    has_food_hint = any(re.search(p, lower, flags=re.IGNORECASE) for p in food_keyword_patterns)
    if not has_food_hint and len(value.split()) >= 3:
        return False

    # 숫자/기호 비율이 너무 높으면 메뉴명이 아닐 가능성이 큼
    alpha_count = len(re.findall(r"[가-힣A-Za-z]", value))
    if alpha_count < max(2, len(value) // 3):
        return False

    rname = re.sub(r"\s+", "", (restaurant_name or "").strip().lower())
    vnorm = re.sub(r"\s+", "", lower)
    if rname and vnorm == rname:
        return False
    return True


def _extract_menu_candidates_from_text(raw_text: str, restaurant_name: str = "") -> list[dict]:
    text = (raw_text or "").replace("\u00a0", " ")
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()

    pattern_inline = re.compile(
        r"([가-힣A-Za-z0-9\s\(\)\[\]\-_/&\+\.,'·]{2,80})\s*(?:[:\-]|\s)\s*([0-9][0-9,]{2,7})\s*원"
    )
    pattern_price = re.compile(r"([0-9][0-9,]{2,7})\s*원")
    noise_patterns = [
        r"메뉴",
        r"가격",
        r"원산지",
        r"영업시간",
        r"리뷰",
        r"전화",
        r"주소",
        r"주문",
    ]

    for line in lines:
        for match in pattern_inline.finditer(line):
            name = _sanitize_menu_name(match.group(1), restaurant_name)
            price = int(match.group(2).replace(",", ""))
            if price < 1000 or not _is_valid_menu_name(name, restaurant_name):
                continue
            key = (name.lower(), price)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "price": float(price), "description": None})

    # 2-line format: "메뉴명" next line "12000원"
    for idx in range(len(lines) - 1):
        line = lines[idx]
        next_line = lines[idx + 1]
        p = pattern_price.search(next_line)
        if not p:
            continue
        name = _sanitize_menu_name(line, restaurant_name)
        if len(name) > 45 or any(re.search(np, name, flags=re.IGNORECASE) for np in noise_patterns):
            continue
        price = int(p.group(1).replace(",", ""))
        if price < 1000 or not _is_valid_menu_name(name, restaurant_name):
            continue
        key = (name.lower(), price)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "price": float(price), "description": None})

    return out[:20]


def _build_menu_seed_queries(restaurant: Restaurant) -> list[str]:
    place_name = re.sub(r"\s+", " ", (restaurant.name or "").strip())
    place_url = (restaurant.place_url or "").strip()
    address = re.sub(r"\s+", " ", (restaurant.address_text or "").strip())
    address_seed = " ".join(address.split()[:2]).strip()
    simple_name = re.sub(r"\([^)]*\)", "", place_name).strip()

    candidates = [
        f'"{place_name}" 메뉴 가격',
        f'"{place_name}" 메뉴판 가격',
    ]
    if address_seed:
        candidates.append(f'"{place_name}" "{address_seed}" 메뉴 가격')
    if place_url:
        candidates.append(f'"{place_name}" "{place_url}" 메뉴 가격')
        candidates.append(f"site:kakao.com {place_name} 메뉴 가격")
    if simple_name and simple_name != place_name:
        candidates.append(f'"{simple_name}" 메뉴 가격')
        if address_seed:
            candidates.append(f'"{simple_name}" "{address_seed}" 메뉴 가격')
        if place_url:
            candidates.append(f'"{simple_name}" "{place_url}" 메뉴 가격')

    out: list[str] = []
    seen: set[str] = set()
    for query in candidates:
        key = query.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(query)
    return out[:8]


def _score_menu_candidate(row: dict, restaurant: Restaurant, menu_name: str) -> float:
    title = str(row.get("title") or "")
    snippet = str(row.get("snippet") or "")
    url = str(row.get("url") or "")
    text = f"{title} {snippet}".lower()
    normalized_place = re.sub(r"\s+", "", (restaurant.name or "").lower())
    normalized_menu = re.sub(r"\s+", "", (menu_name or "").lower())
    normalized_text = re.sub(r"\s+", "", text)

    score = 0.35 + (_restaurant_row_relevance(row, restaurant) * 0.45)
    if normalized_place and normalized_place in normalized_text:
        score += 0.2
    if normalized_menu and normalized_menu in normalized_text:
        score += 0.2
    if restaurant.place_url and restaurant.place_url in url:
        score += 0.1
    if "메뉴" in text and "가격" in text:
        score += 0.05
    return max(0.0, min(score, 0.95))


def _estimate_menu_set_confidence(menus: list[dict]) -> float:
    if not menus:
        return 0.0
    values = []
    for menu in menus:
        try:
            values.append(float(menu.get("menu_confidence") or 0.0))
        except Exception:
            continue
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    coverage_bonus = min(len(values), 5) * 0.03
    return max(0.0, min(avg + coverage_bonus, 1.0))


def _search_menu_candidates(restaurant: Restaurant) -> list[dict]:
    queries = _build_menu_seed_queries(restaurant)
    place_url = (restaurant.place_url or "").strip()

    merged_rows: list[dict] = []
    seen_url: set[str] = set()
    for query in queries:
        rows = _search_web(query, max_results=8)
        for row in rows:
            url = (row.get("url") or "").strip()
            key = url or f"{row.get('title','')}|{row.get('snippet','')}"
            if key in seen_url:
                continue
            seen_url.add(key)
            merged_rows.append(row)
        if len(merged_rows) >= 8:
            break

    out: list[dict] = []
    dedup: set[tuple[str, int]] = set()
    for row in merged_rows:
        if not _is_relevant_row_for_restaurant(row, restaurant):
            continue
        row_text = "\n".join(
            part for part in [row.get("title") or "", row.get("snippet") or ""] if part
        )
        for menu in _extract_menu_candidates_from_text(row_text, restaurant.name or ""):
            name = _sanitize_menu_name(str(menu.get("name") or "").strip(), restaurant.name or "")
            price_value = _safe_float(str(menu.get("price")))
            if not name or price_value is None or not _is_valid_menu_name(name, restaurant.name or ""):
                continue
            price = int(price_value)
            key = (_normalize_menu_name(name), price)
            if not key[0] or key in dedup:
                continue
            dedup.add(key)
            out.append(
                {
                    "name": name,
                    "price": float(price),
                    "description": menu.get("description") or "web-search",
                    "source_url": (row.get("url") or "").strip() or place_url or None,
                    "menu_confidence": _score_menu_candidate(row, restaurant, name),
                }
            )

    # row별 추출이 부족하면 전체 텍스트에서도 보강 추출
    if len(out) < 3 and merged_rows:
        merged_text = "\n".join(
            f"{row.get('title','')} {row.get('snippet','')}".strip()
            for row in merged_rows
            if _is_relevant_row_for_restaurant(row, restaurant)
        )
        default_source_url = (merged_rows[0].get("url") or "").strip() or place_url or None
        for menu in _extract_menu_candidates_from_text(merged_text, restaurant.name or ""):
            name = _sanitize_menu_name(str(menu.get("name") or "").strip(), restaurant.name or "")
            price_value = _safe_float(str(menu.get("price")))
            if not name or price_value is None or not _is_valid_menu_name(name, restaurant.name or ""):
                continue
            price = int(price_value)
            key = (_normalize_menu_name(name), price)
            if not key[0] or key in dedup:
                continue
            dedup.add(key)
            out.append(
                {
                    "name": name,
                    "price": float(price),
                    "description": menu.get("description") or "web-search-merged",
                    "source_url": default_source_url,
                    "menu_confidence": 0.45,
                }
            )
    return out[:10]


def _llm_infer_menu_candidates(restaurant: Restaurant) -> list[dict]:
    if openai_client is None:
        return []
    try:
        prompt = (
            "다음 음식점의 대표 메뉴를 추정해서 JSON 배열로 반환해라.\n"
            f"- 음식점명: {restaurant.name}\n"
            f"- 카테고리: {restaurant.category or ''}\n"
            f"- 주소: {restaurant.address_text or ''}\n"
            "형식: [{name, price, description}] 최대 8개. price는 숫자(원)로."
        )
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MENU_MODEL", "gpt-4.1-mini"),
            temperature=0.3,
            response_format={"type": "json_object"},
            timeout=TIMEOUT,
            messages=[
                {"role": "system", "content": "너는 메뉴 데이터 수집기다. JSON만 반환한다."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        rows = data.get("menus")
        if not isinstance(rows, list):
            rows = data if isinstance(data, list) else []
        out: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = _sanitize_menu_name(str(row.get("name") or "").strip(), restaurant.name or "")
            price = row.get("price")
            try:
                price_value = float(price)
            except Exception:
                continue
            if not name or price_value < 1000 or not _is_valid_menu_name(name, restaurant.name or ""):
                continue
            out.append(
                {
                    "name": name,
                    "price": price_value,
                    "description": (row.get("description") or "llm-fallback"),
                    "source_url": None,
                    "menu_confidence": 0.55,
                }
            )
        return out[:8]
    except Exception as exc:
        logger.warning("llm menu inference failed: %s", exc)
        return []


def _collect_menus_for_restaurant(restaurant: Restaurant) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def _append_valid(candidates: list[dict]) -> None:
        for menu in candidates:
            name = _sanitize_menu_name(str(menu.get("name") or "").strip(), restaurant.name or "")
            if not _is_valid_menu_name(name, restaurant.name or ""):
                continue
            price_value = _safe_float(str(menu.get("price")))
            if price_value is None or price_value < 1000:
                continue
            price = int(price_value)
            key = (_normalize_menu_name(name), price)
            if not key[0] or key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "name": name,
                    "price": float(price),
                    "description": menu.get("description"),
                    "source_url": menu.get("source_url"),
                    "menu_confidence": float(menu.get("menu_confidence") or 0.0),
                }
            )
            if len(merged) >= 10:
                return

    # 1) Kakao place 메뉴
    _append_valid(_fetch_kakao_place_menu_candidates(restaurant))
    # 2) Web 검색 보조
    if len(merged) < 3:
        _append_valid(_search_menu_candidates(restaurant))
    # 3) LLM 추정 보조
    if len(merged) < 3:
        _append_valid(_llm_infer_menu_candidates(restaurant))

    return merged[:10]


def _parse_nutrition_from_text(text: str) -> Optional[dict]:
    if not text:
        return None
    lower = text.lower()
    kcal_patterns = [r"([0-9]+(?:\.[0-9]+)?)\s*kcal", r"칼로리\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)"]
    carb_patterns = [r"탄수화물\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*g", r"carb[s]?\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)"]
    protein_patterns = [r"단백질\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*g", r"protein\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)"]
    fat_patterns = [r"지방\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*g", r"fat\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)"]

    def first(patterns: list[str]) -> Optional[float]:
        for p in patterns:
            m = re.search(p, lower, flags=re.IGNORECASE)
            if not m:
                continue
            v = _safe_float(m.group(1))
            if v is not None:
                return v
        return None

    calories = first(kcal_patterns)
    carbs = first(carb_patterns)
    protein = first(protein_patterns)
    fat = first(fat_patterns)
    if any(v is None for v in (calories, carbs, protein, fat)):
        return None
    return {
        "calories_kcal": float(calories),
        "carbs_g": float(carbs),
        "protein_g": float(protein),
        "fat_g": float(fat),
        "confidence": 0.75,
        "source_type": "search",
    }


def _search_nutrition(restaurant: Restaurant, menu_name: str) -> Optional[dict]:
    query = f"{restaurant.name} {menu_name} 칼로리 탄수화물 단백질 지방"
    rows = _search_web(query, max_results=6)
    snippets = "\n".join([f"{row.get('title','')} {row.get('snippet','')}" for row in rows])
    parsed = _parse_nutrition_from_text(snippets)
    if parsed:
        parsed["source_ref"] = rows[0].get("url") if rows else None
    return parsed


def _llm_infer_nutrition(restaurant: Restaurant, menu_name: str, price: float) -> Optional[dict]:
    if openai_client is None:
        return None
    try:
        prompt = (
            "다음 메뉴의 영양정보를 추정해라. JSON만 출력.\n"
            f"- 음식점: {restaurant.name}\n"
            f"- 카테고리: {restaurant.category or ''}\n"
            f"- 메뉴: {menu_name}\n"
            f"- 가격: {int(price)}원\n"
            "필수키: calories_kcal, carbs_g, protein_g, fat_g, confidence"
        )
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_NUTRITION_MODEL", "gpt-4.1-mini"),
            temperature=0.2,
            response_format={"type": "json_object"},
            timeout=TIMEOUT,
            messages=[
                {"role": "system", "content": "너는 메뉴 영양정보 추정 전문가다. JSON만 반환한다."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        for key in ("calories_kcal", "carbs_g", "protein_g", "fat_g", "confidence"):
            if key not in data:
                return None
        raw_conf = data.get("confidence")
        if isinstance(raw_conf, str):
            conf_map = {"low": 0.45, "medium": 0.65, "high": 0.85}
            confidence = conf_map.get(raw_conf.strip().lower(), 0.6)
        else:
            confidence = float(raw_conf)

        return {
            "calories_kcal": float(data["calories_kcal"]),
            "carbs_g": float(data["carbs_g"]),
            "protein_g": float(data["protein_g"]),
            "fat_g": float(data["fat_g"]),
            "confidence": confidence,
            "source_type": "infer",
            "source_ref": "openai-fallback",
        }
    except Exception as exc:
        logger.warning("llm nutrition inference failed: %s", exc)
        return None


def _resolve_nutrition(restaurant: Restaurant, menu_name: str, price: float) -> Optional[dict]:
    searched = _search_nutrition(restaurant, menu_name)
    if searched:
        return searched
    return _llm_infer_nutrition(restaurant, menu_name, price)


def _normalize_menu_name(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _get_existing_verified_by_restaurant(db: Session, restaurant_ids: list[int]) -> dict[int, list[dict]]:
    if not restaurant_ids:
        return {}
    rows = (
        db.query(
            MenuItem.menu_id,
            MenuItem.restaurant_id,
            MenuItem.name.label("menu_name"),
            MenuItem.description,
            MenuItem.price,
            MenuItem.source_url,
            NutritionFacts.nutrition_id,
            NutritionFacts.calories_kcal,
            NutritionFacts.carbs_g,
            NutritionFacts.protein_g,
            NutritionFacts.fat_g,
            NutritionFacts.confidence,
            NutritionFacts.source_type,
            NutritionFacts.source_ref,
        )
        .join(NutritionFacts, NutritionFacts.menu_item_id == MenuItem.menu_id)
        .filter(MenuItem.restaurant_id.in_(restaurant_ids))
        .filter(MenuItem.name.isnot(None), MenuItem.price.isnot(None))
        .filter(
            NutritionFacts.calories_kcal.isnot(None),
            NutritionFacts.carbs_g.isnot(None),
            NutritionFacts.protein_g.isnot(None),
            NutritionFacts.fat_g.isnot(None),
        )
        .filter(NutritionFacts.confidence.isnot(None), NutritionFacts.confidence >= 0.6)
        .order_by(MenuItem.menu_id.asc(), NutritionFacts.nutrition_id.desc())
        .all()
    )
    latest_by_menu: dict[int, dict] = {}
    for row in rows:
        rec = row._asdict()
        menu_id = int(rec["menu_id"])
        if menu_id in latest_by_menu:
            continue
        latest_by_menu[menu_id] = rec

    out: dict[int, list[dict]] = {}
    for rec in latest_by_menu.values():
        rid = int(rec["restaurant_id"])
        out.setdefault(rid, []).append(
            {
                "name": rec["menu_name"],
                "price": float(rec["price"]),
                "description": rec.get("description"),
                "source_url": rec.get("source_url"),
                "nutrition": {
                    "calories_kcal": float(rec["calories_kcal"]),
                    "carbs_g": float(rec["carbs_g"]),
                    "protein_g": float(rec["protein_g"]),
                    "fat_g": float(rec["fat_g"]),
                    "confidence": float(rec["confidence"]),
                    "source_type": rec.get("source_type") or "search",
                    "source_ref": rec.get("source_ref"),
                },
            }
        )
    return out


def _get_global_nutrition_cache(db: Session) -> dict[str, dict]:
    rows = (
        db.query(
            MenuItem.name.label("menu_name"),
            NutritionFacts.nutrition_id,
            NutritionFacts.calories_kcal,
            NutritionFacts.carbs_g,
            NutritionFacts.protein_g,
            NutritionFacts.fat_g,
            NutritionFacts.confidence,
            NutritionFacts.source_ref,
        )
        .join(NutritionFacts, NutritionFacts.menu_item_id == MenuItem.menu_id)
        .filter(MenuItem.name.isnot(None))
        .filter(
            NutritionFacts.calories_kcal.isnot(None),
            NutritionFacts.carbs_g.isnot(None),
            NutritionFacts.protein_g.isnot(None),
            NutritionFacts.fat_g.isnot(None),
        )
        .filter(NutritionFacts.confidence.isnot(None), NutritionFacts.confidence >= 0.6)
        .order_by(NutritionFacts.nutrition_id.desc())
        .limit(5000)
        .all()
    )
    out: dict[str, dict] = {}
    for row in rows:
        rec = row._asdict()
        key = _normalize_menu_name(str(rec["menu_name"] or ""))
        if not key or key in out:
            continue
        out[key] = {
            "calories_kcal": float(rec["calories_kcal"]),
            "carbs_g": float(rec["carbs_g"]),
            "protein_g": float(rec["protein_g"]),
            "fat_g": float(rec["fat_g"]),
            "confidence": float(rec["confidence"]),
            "source_type": "db_cache",
            "source_ref": rec.get("source_ref") or "db-cache",
        }
    return out


def _collect_restaurant_payload_sync(restaurant: Restaurant, nutrition_cache: dict[str, dict]) -> dict:
    skipped = 0
    menus = _collect_menus_for_restaurant(restaurant)
    if not menus:
        logger.info(
            "collector_skip restaurant_id=%s reason=no_menu_candidates",
            restaurant.restaurant_id,
        )
        return {"restaurant_id": restaurant.restaurant_id, "items": [], "skipped": 1}

    seen_menu_norm: set[str] = set()
    complete_items: list[dict] = []
    reason_counts = {"invalid_menu": 0, "no_nutrition": 0, "low_confidence": 0, "missing_macros": 0}
    for menu in menus:
        menu_name = (menu.get("name") or "").strip()
        price = menu.get("price")
        if not menu_name or not restaurant.name or price is None:
            skipped += 1
            reason_counts["invalid_menu"] += 1
            continue
        norm = _normalize_menu_name(menu_name)
        if not norm or norm in seen_menu_norm:
            skipped += 1
            reason_counts["invalid_menu"] += 1
            continue
        seen_menu_norm.add(norm)

        nutrition = nutrition_cache.get(norm)
        if nutrition is None:
            nutrition = _resolve_nutrition(restaurant, menu_name, float(price))
        if not nutrition:
            skipped += 1
            reason_counts["no_nutrition"] += 1
            continue
        if nutrition.get("confidence", 0.0) < 0.6:
            skipped += 1
            reason_counts["low_confidence"] += 1
            continue
        if any(nutrition.get(k) is None for k in ("calories_kcal", "carbs_g", "protein_g", "fat_g")):
            skipped += 1
            reason_counts["missing_macros"] += 1
            continue

        complete_items.append(
            {
                "name": menu_name,
                "price": float(price),
                "description": menu.get("description"),
                "source_url": menu.get("source_url"),
                "nutrition": nutrition,
            }
        )
    logger.info(
        "collector_restaurant_result restaurant_id=%s menu_candidates=%s complete_items=%s skipped=%s reasons=%s",
        restaurant.restaurant_id,
        len(menus),
        len(complete_items),
        skipped,
        reason_counts,
    )
    return {"restaurant_id": restaurant.restaurant_id, "items": complete_items, "skipped": skipped}


async def _collect_restaurant_payloads_async(
    restaurants: list[Restaurant],
    nutrition_cache: dict[str, dict],
    concurrency: int = 6,
) -> list[dict]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(restaurant: Restaurant) -> dict:
        async with sem:
            return await asyncio.to_thread(_collect_restaurant_payload_sync, restaurant, nutrition_cache)

    tasks = [_one(restaurant) for restaurant in restaurants]
    return await asyncio.gather(*tasks)


def _run_collector_pipeline(
    db: Session,
    user: User,
    payload: CollectorRunRequest,
) -> dict:
    resolved_label = _validate_location_label(payload.label)
    existing_location = _get_location_profile(db, user.user_number, resolved_label)
    if payload.lat is None or payload.lng is None:
        if payload.address_text and payload.address_text.strip():
            lat, lng = _kakao_geocode_address(payload.address_text)
            address_text = payload.address_text.strip()
        elif existing_location and existing_location.lat is not None and existing_location.lng is not None:
            lat, lng = float(existing_location.lat), float(existing_location.lng)
            address_text = (existing_location.address_text or f"{lat:.6f},{lng:.6f}").strip()
        else:
            raise HTTPException(status_code=400, detail="lat/lng 또는 address_text가 필요합니다.")
    else:
        lat, lng = float(payload.lat), float(payload.lng)
        address_text = (payload.address_text or (existing_location.address_text if existing_location else None) or f"{lat:.6f},{lng:.6f}").strip()
    requested_radius = max(100, min(int(payload.radius_m or 1000), 3000))

    location = _upsert_location_profile(
        db=db,
        user_number=user.user_number,
        label=resolved_label,
        address_text=address_text,
        lat=lat,
        lng=lng,
    )

    menus_saved = 0
    nutritions_saved = 0
    skipped = 0
    cache_reused = False
    max_restaurants = max(1, min(int(payload.max_restaurants or 100), 100))

    restaurants: list[Restaurant] = []
    now_utc = datetime.now(timezone.utc)
    fresh_cutoff = _snapshot_cache_fresh_cutoff(now_utc)
    cache_radius_m = max(50, min(int(os.getenv("LOCATION_CACHE_RADIUS_M", "250")), 3000))
    reusable_profile = _find_reusable_location_profile(
        db=db,
        lat=lat,
        lng=lng,
        max_distance_m=cache_radius_m,
        fresh_cutoff=fresh_cutoff,
        current_location_id=int(location.location_id),
    )
    if reusable_profile is not None:
        if reusable_profile.location_id == location.location_id:
            restaurants = _load_restaurants_from_location_snapshots(
                db=db,
                location_profile_id=int(location.location_id),
                max_count=max_restaurants,
            )
        else:
            restaurants = _copy_snapshots_to_location(
                db=db,
                source_location_id=int(reusable_profile.location_id),
                target_location=location,
                max_count=max_restaurants,
                collected_at=now_utc,
            )
        cache_reused = len(restaurants) > 0
        logger.info(
            "collector_cache_reuse location_id=%s source_location_id=%s cache_reused=%s restaurants=%s fresh_cutoff=%s radius_m=%s",
            location.location_id,
            reusable_profile.location_id,
            cache_reused,
            len(restaurants),
            fresh_cutoff.isoformat(),
            cache_radius_m,
        )

    if not restaurants:
        restaurants_raw = _kakao_collect_restaurants(lat, lng, requested_radius, max_restaurants)
        for raw in restaurants_raw:
            if not raw.get("id") or not raw.get("place_name"):
                skipped += 1
                continue
            restaurants.append(_upsert_restaurant_and_snapshot(db, location.location_id, raw))
    db.flush()
    restaurant_count = len(restaurants)

    restaurant_ids = [restaurant.restaurant_id for restaurant in restaurants]
    existing_verified = _get_existing_verified_by_restaurant(db, restaurant_ids)
    global_nutrition_cache = _get_global_nutrition_cache(db)

    network_targets = [restaurant for restaurant in restaurants if not existing_verified.get(restaurant.restaurant_id)]
    fetched_payloads: dict[int, dict] = {}
    if network_targets:
        concurrency = min(10, max(3, int(os.getenv("COLLECTOR_CONCURRENCY", "6"))))
        fetched = asyncio.run(
            _collect_restaurant_payloads_async(
                restaurants=network_targets,
                nutrition_cache=global_nutrition_cache,
                concurrency=concurrency,
            )
        )
        for item in fetched:
            fetched_payloads[int(item["restaurant_id"])] = item
            skipped += int(item.get("skipped") or 0)

    for restaurant in restaurants:
        rid = restaurant.restaurant_id
        complete_items = existing_verified.get(rid)
        if complete_items is None:
            complete_items = (fetched_payloads.get(rid) or {}).get("items") or []
        if not complete_items:
            skipped += 1
            continue

        existing_menus = (
            db.query(MenuItem)
            .filter(MenuItem.restaurant_id == rid)
            .all()
        )
        menu_map = {_normalize_menu_name(menu.name): menu for menu in existing_menus}
        latest_nutrition_by_menu: dict[int, NutritionFacts] = {}
        if existing_menus:
            menu_ids = [m.menu_id for m in existing_menus]
            nutrition_rows = (
                db.query(NutritionFacts)
                .filter(NutritionFacts.menu_item_id.in_(menu_ids))
                .order_by(NutritionFacts.menu_item_id.asc(), NutritionFacts.nutrition_id.desc())
                .all()
            )
            for row in nutrition_rows:
                if row.menu_item_id not in latest_nutrition_by_menu:
                    latest_nutrition_by_menu[row.menu_item_id] = row

        seen_batch: set[str] = set()
        for item in complete_items:
            name = (item.get("name") or "").strip()
            price = item.get("price")
            nutrition = item.get("nutrition") or {}
            if not name or price is None:
                skipped += 1
                continue
            norm = _normalize_menu_name(name)
            if not norm or norm in seen_batch:
                skipped += 1
                continue
            seen_batch.add(norm)

            menu = menu_map.get(norm)
            if menu is None:
                menu = MenuItem(
                    restaurant_id=rid,
                    name=name,
                    description=(item.get("description") or "").strip() or None,
                    price=float(price),
                    source="search_or_llm",
                    source_url=_truncate_source_url(item.get("source_url") or restaurant.place_url),
                )
                db.add(menu)
                db.flush()
                menu_map[norm] = menu
                menus_saved += 1
            else:
                menu.price = float(price)
                menu.description = (item.get("description") or menu.description or "").strip() or None
                menu.source = "search_or_llm"
                menu.source_url = _truncate_source_url(item.get("source_url") or restaurant.place_url)

            last = latest_nutrition_by_menu.get(menu.menu_id)
            same_as_last = (
                last is not None
                and round(float(last.calories_kcal or 0.0), 2) == round(float(nutrition.get("calories_kcal") or 0.0), 2)
                and round(float(last.carbs_g or 0.0), 2) == round(float(nutrition.get("carbs_g") or 0.0), 2)
                and round(float(last.protein_g or 0.0), 2) == round(float(nutrition.get("protein_g") or 0.0), 2)
                and round(float(last.fat_g or 0.0), 2) == round(float(nutrition.get("fat_g") or 0.0), 2)
                and round(float(last.confidence or 0.0), 2) == round(float(nutrition.get("confidence") or 0.0), 2)
            )
            if same_as_last:
                continue

            row = NutritionFacts(
                menu_item_id=menu.menu_id,
                calories_kcal=float(nutrition["calories_kcal"]),
                carbs_g=float(nutrition["carbs_g"]),
                protein_g=float(nutrition["protein_g"]),
                fat_g=float(nutrition["fat_g"]),
                source_type=nutrition.get("source_type", "infer"),
                source_ref=nutrition.get("source_ref"),
                confidence=float(nutrition["confidence"]),
            )
            db.add(row)
            latest_nutrition_by_menu[menu.menu_id] = row
            nutritions_saved += 1

    db.commit()
    return {
        "label": resolved_label,
        "location_profile_id": int(location.location_id),
        "lat": float(lat),
        "lng": float(lng),
        "requested_radius_m": requested_radius,
        "used_radius_m": requested_radius,
        "restaurants_collected": restaurant_count,
        "menus_saved": menus_saved,
        "nutritions_saved": nutritions_saved,
        "skipped_items": skipped,
    }


def _run_collector_pipeline_background(user_number: int, payload_data: dict) -> None:
    """Run collector in background with a fresh DB session (do not reuse request session)."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_number == user_number).first()
        if user is None:
            logger.warning("collector_background_skip user_not_found user_number=%s", user_number)
            return
        payload = CollectorRunRequest(**payload_data)
        result = _run_collector_pipeline(db=db, user=user, payload=payload)
        logger.info(
            "collector_background_done user_number=%s label=%s restaurants=%s menus=%s nutritions=%s",
            user_number,
            result.get("label"),
            result.get("restaurants_collected"),
            result.get("menus_saved"),
            result.get("nutritions_saved"),
        )
    except Exception:
        db.rollback()
        logger.exception("collector_background_failed user_number=%s", user_number)
    finally:
        db.close()


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

    # 목표를 먼저 DB에 저장 (AI 식단 생성 전에 커밋)
    db.commit()

    # AI 식단 생성 (실패해도 목표 저장은 유지됨)
    plan = None
    try:
        plan = generate_one_day_plan(prompt, preferred_meals=preferred_meals)
    except Exception as exc:
        # AI 식단 생성 실패 시 목표만 저장하고 반환
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
            "plan": None,
            "today_intake": None,
        }

    # AI 식단 생성 성공 시 이미지 URL 추가 및 식단 저장
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
        "plan": plan.model_dump() if plan else None,
        "today_intake": today_intake,
    }


def _save_recommend_meals_to_records(
    db: Session,
    user: User,
    meals: List[RecommendMealRecordIn],
    record_date: Optional[str] = None,
) -> tuple[List[int], List[RecommendRecordResultItem]]:
    if not meals:
        return [], []

    record_day = datetime.now().date()
    if record_date:
        try:
            record_day = datetime.strptime(record_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="record_date는 YYYY-MM-DD 형식이어야 합니다.")

    allowed_meal_types = {"breakfast", "lunch", "dinner", "snack", "아침", "점심", "저녁", "간식"}
    meal_type_alias = {
        "아침": "breakfast",
        "점심": "lunch",
        "저녁": "dinner",
        "간식": "snack",
    }
    meal_time_map = {
        "breakfast": time(8, 0, 0),
        "lunch": time(13, 0, 0),
        "dinner": time(19, 0, 0),
        "snack": time(16, 0, 0),
    }
    day_start = datetime.combine(record_day, time.min)
    day_end = day_start + timedelta(days=1)
    record_ids: List[int] = []
    record_results: List[RecommendRecordResultItem] = []
    for meal in meals:
        raw_type = (meal.meal_type or "").strip().lower()
        if raw_type not in allowed_meal_types:
            raise HTTPException(status_code=400, detail="meal_type은 아침/점심/저녁/간식 중 하나여야 합니다.")
        meal_type = meal_type_alias.get(raw_type, raw_type)

        menu = (
            db.query(
                MenuItem.menu_id,
                MenuItem.name.label("menu_name"),
                Restaurant.name.label("restaurant_name"),
                NutritionFacts.calories_kcal,
                NutritionFacts.carbs_g,
                NutritionFacts.protein_g,
                NutritionFacts.fat_g,
            )
            .join(Restaurant, Restaurant.restaurant_id == MenuItem.restaurant_id)
            .join(NutritionFacts, NutritionFacts.menu_item_id == MenuItem.menu_id)
            .filter(MenuItem.menu_id == int(meal.menu_id))
            .order_by(NutritionFacts.nutrition_id.desc())
            .first()
        )
        if not menu:
            raise HTTPException(status_code=404, detail=f"menu_id={meal.menu_id} 메뉴를 찾을 수 없습니다.")

        nutrition_ok = all(
            value is not None
            for value in (menu.calories_kcal, menu.carbs_g, menu.protein_g, menu.fat_g)
        )
        if not nutrition_ok and any(
            value is None for value in (meal.calories_kcal, meal.carbs_g, meal.protein_g, meal.fat_g)
        ):
            raise HTTPException(
                status_code=400,
                detail=f"menu_id={meal.menu_id} 영양정보가 부족합니다. calories/carbs/protein/fat를 함께 보내주세요.",
            )

        food_name = (meal.name or "").strip() or f"{menu.restaurant_name} {menu.menu_name}".strip()

        if not bool(meal.checked):
            row = None
            if meal.record_id is not None:
                row = (
                    db.query(Record)
                    .filter(
                        Record.record_id == int(meal.record_id),
                        Record.user_number == user.user_number,
                    )
                    .first()
                )
            else:
                # 프론트가 record_id를 아직 보내지 않는 경우를 위한 임시 fallback
                row = (
                    db.query(Record)
                    .filter(
                        Record.user_number == user.user_number,
                        Record.meal_type == meal_type,
                        Record.food_name == food_name,
                        Record.record_created_at >= day_start,
                        Record.record_created_at < day_end,
                    )
                    .order_by(Record.record_created_at.desc(), Record.record_id.desc())
                    .first()
                )
            deleted_record_id: Optional[int] = None
            if row:
                deleted_record_id = int(row.record_id)
                db.delete(row)
            record_results.append(
                RecommendRecordResultItem(
                    menu_id=int(meal.menu_id),
                    record_id=deleted_record_id,
                    deleted=bool(row),
                )
            )
            continue

        rec = Record(
            user_number=user.user_number,
            food_name=food_name,
            food_calories=float(menu.calories_kcal if menu.calories_kcal is not None else meal.calories_kcal),
            food_protein=float(menu.protein_g if menu.protein_g is not None else meal.protein_g),
            food_carb=float(menu.carbs_g if menu.carbs_g is not None else meal.carbs_g),
            food_fat=float(menu.fat_g if menu.fat_g is not None else meal.fat_g),
            meal_type=meal_type,
            # record 테이블의 기록일시 컬럼(record_created_at)을 명시적으로 사용
            record_created_at=datetime.combine(record_day, meal_time_map.get(meal_type, time(12, 0, 0))),
        )
        db.add(rec)
        db.flush()
        record_ids.append(rec.record_id)
        record_results.append(
            RecommendRecordResultItem(
                menu_id=int(meal.menu_id),
                record_id=int(rec.record_id),
                deleted=False,
            )
        )

    return record_ids, record_results


import traceback  # 코드 상단에 추가되어 있지 않다면 추가해주세요
from fastapi import HTTPException

@app.post("/api/recommend/menu-save", response_model=PersonalizedMenuResponse)
def generate_menu_save(
    payload: PersonalizedMenuRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    try:
        # --- 기존 로직 시작 ---
        _migrate_work_to_company(db, user_number=current_user.user_number)
        label = _validate_location_label(payload.label)

        requested_radius = max(100, min(int(payload.radius_m or 1000), 3000))
        goal_type = _resolve_goal_type_for_user(db, current_user.user_number)
        tdee_kcal = _resolve_tdee_kcal_for_user(db, current_user)
        daily_target_kcal = _goal_daily_target_kcal(tdee_kcal, goal_type)
        meal_targets = _meal_targets_from_daily(daily_target_kcal)

        input_address_text = (payload.address_text or "").strip() or None
        input_lat = payload.lat
        input_lng = payload.lng
        has_request_location = bool(input_address_text) or (input_lat is not None and input_lng is not None)

        resolved_address_text = input_address_text
        resolved_lat = input_lat
        resolved_lng = input_lng

        if has_request_location:
            if resolved_lat is None or resolved_lng is None:
                if not resolved_address_text:
                    raise HTTPException(status_code=400, detail="lat/lng 또는 address_text가 필요합니다.")
                geo_lat, geo_lng = _kakao_geocode_address(resolved_address_text)
                resolved_lat, resolved_lng = float(geo_lat), float(geo_lng)
            
            if not resolved_address_text:
                existing_location = _get_location_profile(db, current_user.user_number, label)
                resolved_address_text = (
                    (existing_location.address_text.strip() if existing_location and existing_location.address_text else "")
                    or f"{resolved_lat:.6f},{resolved_lng:.6f}"
                )
            
            _upsert_location_profile(
                db=db,
                user_number=current_user.user_number,
                label=label,
                address_text=resolved_address_text,
                lat=float(resolved_lat),
                lng=float(resolved_lng),
            )
            db.flush()

        collector_triggered = False

        try:
            candidates = _query_verified_menu_candidates(
                db=db,
                user_number=current_user.user_number,
                label=label,
                radius_m=requested_radius,
            )
        except HTTPException as exc:
            if exc.status_code == 404 and not has_request_location:
                raise HTTPException(
                    status_code=400,
                    detail="요청 위치(address_text 또는 lat/lng)와 저장된 location_profiles가 모두 없습니다.",
                )
            raise

        used_radius = requested_radius
        if len(candidates) < 9:
            collector_triggered = True
            used_radius = 1000 if requested_radius < 1000 else requested_radius

            collector_payload = CollectorRunRequest(
                label=label,
                address_text=resolved_address_text,
                lat=resolved_lat,
                lng=resolved_lng,
                radius_m=used_radius,
                max_restaurants=100,
            )
            # 응답을 막지 않도록 수집은 백그라운드에서 별도 DB 세션으로 수행한다.
            background_tasks.add_task(
                _run_collector_pipeline_background,
                int(current_user.user_number),
                collector_payload.model_dump(),
            )

        total_candidates = len(candidates)
        ranked: dict[str, list[dict]] = {"breakfast": [], "lunch": [], "dinner": []}
        used_menu_ids: set[int] = set()
        
        for meal in ("breakfast", "lunch", "dinner"):
            target = meal_targets[meal]
            filtered = [
                item for item in candidates
                if _is_likely_open_for_meal(item, meal)
                and _is_goal_compatible(item, goal_type, target)
            ]
            scored = sorted(
                filtered,
                key=lambda item: _menu_score(item, target, goal_type),
                reverse=True,
            )
            picked: list[dict] = []
            for item in scored:
                menu_id = int(item.get("menu_id"))
                if menu_id in used_menu_ids:
                    continue
                picked.append(item)
                used_menu_ids.add(menu_id)
                if len(picked) >= 3:
                    break
            ranked[meal] = picked

        def _to_personalized_menu_item(row: dict) -> PersonalizedMenuItem:
            return PersonalizedMenuItem(
                restaurant_id=int(row["restaurant_id"]),
                restaurant_name=str(row["restaurant_name"]),
                place_url=str(row.get("place_url") or ""),
                menu_id=int(row["menu_id"]),
                menu_name=str(row["menu_name"]),
                price=float(row["price"]),
                distance_m=float(row["distance_m"]),
                calories_kcal=float(row["calories_kcal"]),
                carbs_g=float(row["carbs_g"]),
                protein_g=float(row["protein_g"]),
                fat_g=float(row["fat_g"]),
                confidence=float(row["confidence"]),
            )

        record_ids: Optional[List[int]] = None
        record_results: Optional[List[RecommendRecordResultItem]] = None
        if payload.meals:
            record_ids, record_results = _save_recommend_meals_to_records(
                db=db,
                user=current_user,
                meals=payload.meals,
                record_date=payload.record_date,
            )
            db.commit()

        return PersonalizedMenuResponse(
            goal_type=goal_type,
            tdee_kcal=tdee_kcal,
            daily_target_kcal=daily_target_kcal,
            meal_target_kcal=meal_targets,
            total_candidates=total_candidates,
            used_radius_m=used_radius,
            collector_triggered=collector_triggered,
            breakfast=[_to_personalized_menu_item(row) for row in ranked["breakfast"]],
            lunch=[_to_personalized_menu_item(row) for row in ranked["lunch"]],
            dinner=[_to_personalized_menu_item(row) for row in ranked["dinner"]],
            record_ids=record_ids,
            record_results=record_results,
        )

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print("\n" + "="*60)
        print("🔥 [CRITICAL ERROR] menu-save API 에러 발생!")
        print(f"에러 메시지: {str(e)}")
        print(f"상세 경로:\n{error_traceback}")
        print("="*60 + "\n")
        
        raise HTTPException(
            status_code=500, 
            detail=f"Internal Server Error: {str(e)}"
        )
        

@app.post("/api/collector/run", response_model=CollectorRunResponse)
def run_collector_pipeline(
    payload: CollectorRunRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    return CollectorRunResponse(**_run_collector_pipeline(db=db, user=current_user, payload=payload))



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


@app.post("/api/recommend/record", response_model=PlanRecordCreateResult)
def create_records_from_recommendation(
    payload: RecommendRecordCreateRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """카카오맵 추천(menu-save) 메뉴 선택 항목을 오늘 기록으로 저장"""
    user = current_user

    if not payload.meals:
        raise HTTPException(status_code=400, detail="meals가 비어 있습니다.")

    record_ids, _ = _save_recommend_meals_to_records(
        db=db,
        user=user,
        meals=payload.meals,
        record_date=payload.record_date,
    )

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
            detail="activity_level은 sedentary/light/moderate/active/very_active 중 하나여야 합니다.",
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
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_number == current_user.user_number)
        .one_or_none()
    )
    bmr_value = record.bmr if record.bmr is not None else (profile.bmr if profile else None)
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
                "bmr": bmr_value,
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
    locations = db.query(LocationProfile).filter(LocationProfile.user_number == user_number).all()
    from app.schemas import LocationProfileResponse
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
        "locations": [LocationProfileResponse.model_validate(loc) for loc in locations] if locations else [],
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
    s3_file_name = image.filename or "inbody.jpg"
    image_url = upload_image_to_s3(content, s3_file_name)

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
            "image_url": image_url,
            "activity_level": profile.activity_level if profile else None,
            "activity_level_options": {
                "sedentary": 1.2,
                "light": 1.375,
                "moderate": 1.55,
                "active": 1.725,
                "very_active": 1.9,
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
        "image_url": image_url,
        "activity_level": profile.activity_level if profile else None,
        "activity_level_options": {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
            "very_active": 1.9,
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
    s3_file_name = image.filename or "inbody.jpg"
    image_url = upload_image_to_s3(content, s3_file_name)

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
            "image_url": image_url,
            "activity_level": profile.activity_level if profile else None,
            "activity_level_options": {
                "sedentary": 1.2,
                "light": 1.375,
                "moderate": 1.55,
                "active": 1.725,
                "very_active": 1.9,
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
        "image_url": image_url,
        "activity_level": profile.activity_level if profile else None,
        "activity_level_options": {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
            "very_active": 1.9,
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
        s3_file_name = image.filename or filename
        image_url = upload_image_to_s3(content, s3_file_name)
        save_path.write_bytes(content)

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



# 위치 정보 업데이트 API (FastAPI 인스턴스 생성 이후, 파일 마지막에 정의)
from app.schemas import (
    UserAddressEditRequest,
    UserAddressResponse,
)

@app.post("/api/user/address", response_model=UserAddressResponse)
def edit_mypage_address(
    payload: UserAddressEditRequest,
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 주소 등록/수정 (home/company)"""
    _migrate_work_to_company(db, user_number=current_user.user_number)
    if payload.user_number is not None and payload.user_number != current_user.user_number:
        raise HTTPException(status_code=403, detail="user_number가 일치하지 않습니다.")

    home_address = (payload.home_address or "").strip()
    company_address = (payload.company_address or "").strip()
    if not home_address and not company_address:
        raise HTTPException(status_code=400, detail="home_address 또는 company_address가 필요합니다.")

    def upsert_label(label: str, address_text: str) -> None:
        if not address_text:
            return
        norm_label = _validate_location_label(label)
        profile = (
            db.query(LocationProfile)
            .filter(
                LocationProfile.user_number == current_user.user_number,
                LocationProfile.label == norm_label,
            )
            .one_or_none()
        )
        if profile is None and norm_label == "company":
            profile = (
                db.query(LocationProfile)
                .filter(
                    LocationProfile.user_number == current_user.user_number,
                    LocationProfile.label == "work",
                )
                .one_or_none()
            )
            if profile is not None:
                profile.label = "company"
        if profile is None:
            profile = LocationProfile(
                user_number=current_user.user_number,
                label=norm_label,
                address_text=address_text,
                lat=None,
                lng=None,
            )
            db.add(profile)
        else:
            profile.address_text = address_text

    upsert_label("home", home_address)
    upsert_label("company", company_address)
    db.commit()

    locations = (
        db.query(LocationProfile)
        .filter(LocationProfile.user_number == current_user.user_number)
        .all()
    )
    home = next((loc.address_text for loc in locations if loc.label == "home"), None)
    work = next((loc.address_text for loc in locations if loc.label == "company"), None)
    if work is None:
        work = next((loc.address_text for loc in locations if loc.label == "work"), None)
    return UserAddressResponse(
        user_number=current_user.user_number,
        home_address=home,
        company_address=work,
    )


@app.get("/api/user/address", response_model=UserAddressResponse)
def get_user_addresses(
    current_user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """사용자 주소 조회"""
    _migrate_work_to_company(db, user_number=current_user.user_number)
    locations = (
        db.query(LocationProfile)
        .filter(LocationProfile.user_number == current_user.user_number)
        .all()
    )
    home = next((loc.address_text for loc in locations if loc.label == "home"), None)
    work = next((loc.address_text for loc in locations if loc.label == "company"), None)
    if work is None:
        work = next((loc.address_text for loc in locations if loc.label == "work"), None)
    return UserAddressResponse(
        user_number=current_user.user_number,
        home_address=home,
        company_address=work,
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
