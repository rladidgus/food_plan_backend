from __future__ import annotations

"""Pydantic request/response schemas used by FastAPI endpoints."""

from datetime import date, datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class DietRecordRequest(BaseModel):
    """식단 기록 요청 모델"""

    goal_calories: int


class DietRecordResponse(BaseModel):
    """식단 기록 응답 모델"""

    food_name: str
    calories: int
    message: str


class LocationProfileUpdateRequest(BaseModel):
    label: str  # home/company
    address_text: str
    lat: float
    lng: float


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



class LocationProfileResponse(BaseModel):
    location_id: int
    label: str
    address_text: str
    lat: Optional[float] = None
    lng: Optional[float] = None

    class Config:
        from_attributes = True


class MyPageEnvelopeResponse(BaseModel):
    """마이페이지 응답 모델 (목표 + 식단 기록 + 위치)"""
    user: Optional[UserResponse] = None
    goal: Optional[UserGoalResponse] = None
    body: Optional[dict[str, Any]] = None
    records: List[MyPageResponse]
    locations: Optional[List[LocationProfileResponse]] = None


class UserAddressEditRequest(BaseModel):
    user_number: Optional[int] = None
    home_address: Optional[str] = None
    company_address: Optional[str] = None


class UserAddressResponse(BaseModel):
    user_number: int
    home_address: Optional[str] = None
    company_address: Optional[str] = None


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
    record_date: Optional[str] = None
    meals: List[PlanMealRecordIn]


class PlanRecordCreateResult(BaseModel):
    record_ids: List[int]


class RecommendMealRecordIn(BaseModel):
    record_id: Optional[int] = None
    meal_type: str
    menu_id: int
    name: Optional[str] = None
    calories_kcal: Optional[float] = None
    carbs_g: Optional[float] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    checked: bool = True


class RecommendRecordCreateRequest(BaseModel):
    user_number: Optional[int] = None
    id: Optional[str] = None
    record_date: Optional[str] = None
    meals: List[RecommendMealRecordIn]


class RecommendRecordResultItem(BaseModel):
    menu_id: int
    record_id: Optional[int] = None
    deleted: bool = False


class TodayIntakeResponse(BaseModel):
    goal_type: str
    target_calorie: Optional[float] = None
    total_calories_kcal: int
    total_carbs_g: float
    total_protein_g: float
    total_fat_g: float
    checked_meal_count: int = 0
    checked_meal_types: List[str] = Field(default_factory=list)
    meal_check_status: dict[str, bool] = Field(default_factory=dict)
    plan_date: Optional[str] = None


class MealPlanItemSchema(BaseModel):
    name: str
    description: Optional[str] = None
    calories_kcal: int
    carbs_g: float
    protein_g: float
    fat_g: float
    image_url: Optional[str] = None


class DayMealPlanSchema(BaseModel):
    day_label: str
    date: Optional[str] = None
    breakfast: MealPlanItemSchema
    lunch: MealPlanItemSchema
    dinner: MealPlanItemSchema
    total_calories_kcal: int
    total_carbs_g: float
    total_protein_g: float
    total_fat_g: float


class OneDayMealPlanSchema(BaseModel):
    goal_type: str
    target_calorie: Optional[int] = None
    body_type_stage1: Optional[str] = None
    body_type_stage2: Optional[str] = None
    notes: List[str]
    days: List[DayMealPlanSchema]


class UserGoalWithPlanResponse(UserGoalResponse):
    """사용자 목표 변경 응답 (목표 + 최신 식단)"""

    plan: OneDayMealPlanSchema | None = None
    today_intake: Optional[TodayIntakeResponse] = None


class DietPlanWithIntakeResponse(BaseModel):
    plan: OneDayMealPlanSchema
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


class InBodyManualUpdateRequest(BaseModel):
    user_number: Optional[int] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    bmi: Optional[float] = None
    body_fat_pct: Optional[float] = None
    skeletal_muscle_mass: Optional[float] = None
    body_fat_mass: Optional[float] = None
    bmr: Optional[float] = None


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
    values: Optional[dict[str, Any]] = None
    created_at: str

    class Config:
        from_attributes = True


class InBodyOcrResponse(BaseModel):
    """인바디 OCR 응답"""

    raw_text: str
    text: str
    values: dict[str, Any]
    updated: bool
    image_url: Optional[str] = None
    activity_level: Optional[str] = None
    activity_level_options: Optional[dict[str, Any]] = None


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


class BodyTypeFromUserRequest(BaseModel):
    user_number: Optional[int] = None


class LogoutResponse(BaseModel):
    message: str


class RecordDeleteResponse(BaseModel):
    """식단 기록 삭제 응답"""

    record_id: int
    message: str


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


class PersonalizedMenuRequest(BaseModel):
    label: str = "home"  # home/company
    address_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_m: int = 1000
    record_date: Optional[str] = None
    meals: Optional[List[RecommendMealRecordIn]] = None


class PersonalizedMenuItem(BaseModel):
    restaurant_id: int
    restaurant_name: str
    place_url: str
    menu_id: int
    menu_name: str
    price: float
    distance_m: float
    calories_kcal: float
    carbs_g: float
    protein_g: float
    fat_g: float
    confidence: float


class PersonalizedMenuResponse(BaseModel):
    goal_type: str
    tdee_kcal: int
    daily_target_kcal: int
    meal_target_kcal: dict[str, int]
    total_candidates: int
    used_radius_m: int
    collector_triggered: bool
    breakfast: List[PersonalizedMenuItem]
    lunch: List[PersonalizedMenuItem]
    dinner: List[PersonalizedMenuItem]
    record_ids: Optional[List[int]] = None
    record_results: Optional[List[RecommendRecordResultItem]] = None


class CollectorRunRequest(BaseModel):
    label: str = "home"  # home/company
    address_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_m: int = 1000
    max_restaurants: int = 100


class CollectorRunResponse(BaseModel):
    label: str
    location_profile_id: int
    lat: float
    lng: float
    requested_radius_m: int
    used_radius_m: int
    restaurants_collected: int
    menus_saved: int
    nutritions_saved: int
    skipped_items: int
