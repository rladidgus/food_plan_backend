from sqlalchemy import Column, Integer, String, DateTime, Date, Float, ForeignKey, UniqueConstraint, Index, text, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    """사용자 테이블 모델"""
    __tablename__ = "users"
    
    # 기본 정보
    user_number = Column(Integer, primary_key=True, index=True, autoincrement=True)
    id = Column(String(50), unique=True, nullable=False)  # 로그인 아이디
    username = Column(String(50), nullable=False)  # 사용자 이름 (표시용)
    password = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    provider_user_id = Column(String(100), nullable=True)  # 소셜 로그인용 아이디
    role = Column(String(20), nullable=False, server_default="user")  # user/admin
    email = Column(String(100), unique=True, nullable=True)

    # 관계 설정
    profile = relationship("UserProfile", back_populates="user", uselist=False)
    goals = relationship("UserGoal", back_populates="user")
    foods = relationship("Food", back_populates="user")
    records = relationship("Record", back_populates="user")
    bmi_histories = relationship("BMIHistory", back_populates="user")
    inbody_records = relationship("InBodyRecord", back_populates="user")

    daily_activities = relationship("DailyActivity", back_populates="user")
    food_analysis_results = relationship("FoodAnalysisResult", back_populates="user")
    diet_plans = relationship("UserDietPlan", back_populates="user")
    location_profiles = relationship("LocationProfile", back_populates="user")
    meal_recommendations = relationship("MealRecommendation", back_populates="user")
    pipeline_runs = relationship("PipelineRun", back_populates="user")
    
    def __repr__(self):
        return f"<User(user_number={self.user_number}, username='{self.username}')>"


class UserProfile(Base):
    """유저 프로필 테이블"""
    __tablename__ = "user_profiles"

    profile_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False, unique=True)

    height = Column(Float, nullable=True)  # 키
    weight = Column(Float, nullable=True)  # 몸무게
    age = Column(Integer, nullable=True)   # 나이
    birth_date = Column(DateTime(timezone=True), nullable=True)  # 생년월일
    gender = Column(String(10), nullable=True)  # 성별
    goal_type = Column(String(20), nullable=True)  # diet/bulk/maintain 등
    activity_level = Column(String(20), nullable=True)  # sedentary/light/moderate/active
    body_fat_percent = Column(Float, nullable=True)  # 체지방률
    skeletal_muscle_mass = Column(Float, nullable=True)  # 골격근량
    bmr = Column(Float, nullable=True)  # 기초대사량

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="profile")

    def __repr__(self):
        return f"<UserProfile(profile_id={self.profile_id}, user_number={self.user_number})>"


class UserGoal(Base):
    """사용자 목표 테이블"""
    __tablename__ = "user_goals"

    goal_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    id = Column(String(50), nullable=True)  # 로그인 아이디(스냅샷)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)

    goal_type = Column(String(20), nullable=False)  # diet/maintain/bulk
    target_calorie = Column(Float, nullable=True)
    target_protein = Column(Float, nullable=True)
    target_carb = Column(Float, nullable=True)
    target_fat = Column(Float, nullable=True)
    target_macros = Column(String(50), nullable=True)  # "C:P:F" 또는 비율 문자열
    target_pace = Column(String(50), nullable=True)  # 감량/증량 목표
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="goals")

    def __repr__(self):
        return f"<UserGoal(goal_id={self.goal_id}, user_number={self.user_number}, goal_type={self.goal_type})>"


class Food(Base):
    """식품 테이블 모델 - 음식 마스터 데이터"""
    __tablename__ = "food"
    
    food_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=True)
    food_name = Column(String(100), nullable=False)
    food_calories = Column(Float, nullable=False)
    food_protein = Column(Float, nullable=False)
    food_carb = Column(Float, nullable=False)
    food_fat = Column(Float, nullable=False)
    food_image = Column(String, nullable=True)
    
    # 관계 설정
    user = relationship("User", back_populates="foods")
    records = relationship("Record", back_populates="food")
    
    def __repr__(self):
        return f"<Food(food_id={self.food_id}, food_name='{self.food_name}')>"


class Record(Base):
    """식단 기록 테이블 - 기록 당시의 영양 정보를 스냅샷으로 저장"""
    __tablename__ = "record"
    
    record_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    
    food_id = Column(Integer, ForeignKey("food.food_id"), nullable=True)
    
    # 기록 당시의 영양 정보 스냅샷 (항상 저장)
    # Food 테이블이 변경되어도 과거 기록은 불변!
    food_name = Column(String(100), nullable=False)
    food_calories = Column(Float, nullable=False)
    food_protein = Column(Float, nullable=False)
    food_carb = Column(Float, nullable=False)
    food_fat = Column(Float, nullable=False)
    serving_size = Column(Float, nullable=True)  # 1회 제공량 (예: 100)
    serving_unit = Column(String(20), nullable=True)  # 제공량 단위 (g, ml, 개 등)
    quantity = Column(Float, nullable=True)  # 섭취량 (예: 1.5)

    # 추가 정보
    goal_calories = Column(Float, nullable=True)  # 해당 기록의 목표 칼로리
    image_url = Column(String, nullable=True)  # 식단 사진 경로
    meal_type = Column(String(20), nullable=True)  # 아침, 점심, 저녁, 간식
    record_created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 관계 설정
    user = relationship("User", back_populates="records")
    food = relationship("Food", back_populates="records")  # 원본 음식 참조 (있으면)
    
    def __repr__(self):
        return f"<Record(record_id={self.record_id}, food_name='{self.food_name}', food_calories={self.food_calories})>"

class FoodAnalysisResult(Base):
    """음식 분석 결과 임시 저장 테이블"""
    __tablename__ = "food_analysis_results"

    far_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)

    image_url = Column(String, nullable=False)

    predicted_food_name = Column(String(100), nullable=False)
    predicted_reason = Column(Text, nullable=True)

    estimated_serving_g = Column(Float, nullable=True)
    estimated_calories_kcal = Column(Float, nullable=True)
    estimated_carb_g = Column(Float, nullable=True)
    estimated_protein_g = Column(Float, nullable=True)
    estimated_fat_g = Column(Float, nullable=True)

    model = Column(String(50), nullable=False, server_default="gpt-4.1-mini")
    status = Column(String(20), nullable=False, server_default="PENDING")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 관계 설정 (원하면 User에도 back_populates 추가)
    user = relationship("User", back_populates="food_analysis_results")

    def __repr__(self):
        return f"<FoodAnalysisResult(far_id={self.far_id}, user_number={self.user_number}, predicted_food_name='{self.predicted_food_name}')>"


class UserDietPlan(Base):
    """사용자 목표 식단 저장 테이블"""
    __tablename__ = "user_diet_plans"

    plan_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)

    goal_type = Column(String(20), nullable=False)  # diet/maintain/bulk
    target_calorie = Column(Float, nullable=True)
    plan_json = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="diet_plans")

    def __repr__(self):
        return f"<UserDietPlan(plan_id={self.plan_id}, user_number={self.user_number}, goal_type={self.goal_type})>"

class BMIHistory(Base):
    """BMI 히스토리 테이블 모델"""
    __tablename__ = "bmi_history"
    
    bmi_history_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    bmi = Column(Float, nullable=False)
    bmi_history_created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 관계 설정
    user = relationship("User", back_populates="bmi_histories")
    
    def __repr__(self):
        return f"<BMIHistory(bmi_history_id={self.bmi_history_id}, user_number={self.user_number}, bmi={self.bmi})>"


class InBodyRecord(Base):
    """인바디 측정 기록 테이블"""
    __tablename__ = "inbody_records"
    
    inbody_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    
    # 인바디 측정값
    measurement_date = Column(DateTime(timezone=True), nullable=True) # 측정 날짜
    height = Column(Float, nullable=True)              # 키 (cm)
    weight = Column(Float, nullable=True)              # 체중 (kg)
    body_fat_mass = Column(Float, nullable=True)       # 체지방량 (kg)
    body_fat_pct = Column(Float, nullable=True)        # 체지방률 (%)
    skeletal_muscle_mass = Column(Float, nullable=True)  # 골격근량 (kg)
    bmr = Column(Float, nullable=True)                 # 기초대사량 (kcal)
    abdominal_fat_ratio = Column(Float, nullable=True)   # 복부지방률
    inbody_score = Column(Integer, nullable=True)      # 인바디점수
    predicted_classify = Column(Integer, nullable=True)  # 체형 분류 ID (선택)
    classify_name = Column(String(50), nullable=True)    # 체형 분류 이름 (선택)
    source = Column(String(20), nullable=True)          # 입력 방식 (manual/ocr/csv)
    note = Column(String(255), nullable=True)           # 사용자 메모

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 관계 설정
    user = relationship("User", back_populates="inbody_records")
    
    def __repr__(self):
        return (
            f"<InBodyRecord(inbody_id={self.inbody_id}, user_number={self.user_number}, "
            f"measurement_date={self.measurement_date}, source={self.source})>"
        )

class DailyActivity(Base):
    """일일 활동 기록 테이블"""
    __tablename__ = "daily_activities"

    activity_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)

    # 활동 정보
    activity_date = Column(Date, nullable=False)  # 활동 날짜
    activity_type = Column(String(50), nullable=False)  # 활동 종류 (예: 걷기, 자전거)
    steps = Column(Integer, nullable=True)  # 걸음 수 (해당 활동에 해당하는 경우)
    active_kcal = Column(Float, nullable=True)  # 활동 대사량
    total_kcal = Column(Float, nullable=True)  # 총 대사량
    workout_minutes = Column(Integer, nullable=True)  # 운동 시간
    distance_meters = Column(Float, nullable=True)  # 이동 거리 (미터 단위)
    
    activity_source = Column(String(20), nullable=True)  # 데이터 출처 (예: 'health_connect' | 'healthkit' | 'manual')
    activity_source_device = Column(String(100), nullable=True)  # 데이터 출처 디바이스 정보
    activity_source_app = Column(String(100), nullable=True)  # 데이터 출처 앱 정보
    activity_source_record_id = Column(String(100), nullable=True)  # 원천 데이터 레코드 ID
    
    activity_synced_at = Column(DateTime(timezone=True), nullable=True)  # 데이터 동기화 시각
    activity_created_at = Column(DateTime(timezone=True), server_default=func.now())  # 활동 데이터 생성 시각
    activity_updated_at = Column(DateTime(timezone=True), onupdate=func.now())  # 활동 데이터 수정 시각

    __table_args__ = (
        UniqueConstraint(
            "user_number",
            "activity_source",
            "activity_source_record_id",
            name="uq_daily_activity_source_record",
        ),
        Index(
            "uq_daily_activity_user_date_type_null_source",
            "user_number",
            "activity_date",
            "activity_type",
            unique=True,
            postgresql_where=text("activity_source_record_id IS NULL"),
        ),
    )

    # 관계 설정
    user = relationship("User", back_populates="daily_activities")

    def __repr__(self):
        return (
            f"<DailyActivity(activity_id={self.activity_id}, user_number={self.user_number}, "
            f"activity_date={self.activity_date}, activity_type='{self.activity_type}')>"
        )


class LocationProfile(Base):
    """사용자별 집/회사 위치 프로필"""
    __tablename__ = "location_profiles"

    location_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    label = Column(String(20), nullable=False)  # home/work
    address_text = Column(String(255), nullable=False)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_number", "label", name="uq_location_profile_user_label"),
        Index("ix_location_profile_user", "user_number"),
    )

    user = relationship("User", back_populates="location_profiles")
    restaurant_snapshots = relationship("RestaurantSnapshot", back_populates="location_profile")

    def __repr__(self):
        return f"<LocationProfile(location_id={self.location_id}, user_number={self.user_number}, label='{self.label}')>"


class Restaurant(Base):
    """반경 내 음식점 마스터"""
    __tablename__ = "restaurants"

    restaurant_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source = Column(String(50), nullable=False)  # kakao/naver/etc
    source_place_id = Column(String(100), nullable=False)
    name = Column(String(200), nullable=False)
    category = Column(String(200), nullable=True)
    address_text = Column(String(255), nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    phone = Column(String(50), nullable=True)
    place_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("source", "source_place_id", name="uq_restaurant_source_place"),
        Index("ix_restaurant_source_place", "source", "source_place_id"),
    )

    restaurant_snapshots = relationship("RestaurantSnapshot", back_populates="restaurant")
    menu_items = relationship("MenuItem", back_populates="restaurant")

    def __repr__(self):
        return f"<Restaurant(restaurant_id={self.restaurant_id}, name='{self.name}')>"


class RestaurantSnapshot(Base):
    """검색 시점의 음식점 스냅샷"""
    __tablename__ = "restaurant_snapshots"

    snapshot_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    location_profile_id = Column(Integer, ForeignKey("location_profiles.location_id"), nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.restaurant_id"), nullable=False)
    distance_m = Column(Float, nullable=True)
    collected_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("location_profile_id", "restaurant_id", name="uq_snapshot_location_restaurant"),
        Index("ix_restaurant_snapshot_location", "location_profile_id"),
        Index("ix_restaurant_snapshot_restaurant", "restaurant_id"),
    )

    location_profile = relationship("LocationProfile", back_populates="restaurant_snapshots")
    restaurant = relationship("Restaurant", back_populates="restaurant_snapshots")

    def __repr__(self):
        return f"<RestaurantSnapshot(snapshot_id={self.snapshot_id}, restaurant_id={self.restaurant_id})>"


class MenuItem(Base):
    """음식점별 메뉴 마스터"""
    __tablename__ = "menu_items"

    menu_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.restaurant_id"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    source = Column(String(50), nullable=True)  # scraping/ocr/manual
    source_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("restaurant_id", "name", name="uq_menu_item_restaurant_name"),
        Index("ix_menu_item_restaurant", "restaurant_id"),
    )

    restaurant = relationship("Restaurant", back_populates="menu_items")
    nutrition_facts = relationship("NutritionFacts", back_populates="menu_item")

    def __repr__(self):
        return f"<MenuItem(menu_id={self.menu_id}, name='{self.name}')>"


class NutritionFacts(Base):
    """메뉴별 영양 정보"""
    __tablename__ = "nutrition_facts"

    nutrition_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.menu_id"), nullable=False)
    calories_kcal = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    sodium_mg = Column(Float, nullable=True)
    sugar_g = Column(Float, nullable=True)
    fiber_g = Column(Float, nullable=True)
    source_type = Column(String(20), nullable=False)  # search/infer/manual
    source_ref = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)  # 0~1
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_nutrition_menu", "menu_item_id"),
    )

    menu_item = relationship("MenuItem", back_populates="nutrition_facts")

    def __repr__(self):
        return f"<NutritionFacts(nutrition_id={self.nutrition_id}, menu_item_id={self.menu_item_id})>"


class MealRecommendation(Base):
    """최종 추천 결과"""
    __tablename__ = "meal_recommendations"

    recommendation_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    meal_type = Column(String(20), nullable=False)  # breakfast/lunch/dinner
    menu_item_id = Column(Integer, ForeignKey("menu_items.menu_id"), nullable=False)
    reason_text = Column(Text, nullable=True)
    score_nutrition = Column(Float, nullable=True)
    score_accessibility = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_meal_reco_user", "user_number"),
        Index("ix_meal_reco_meal", "meal_type"),
    )

    user = relationship("User", back_populates="meal_recommendations")
    menu_item = relationship("MenuItem")

    def __repr__(self):
        return f"<MealRecommendation(recommendation_id={self.recommendation_id}, meal_type='{self.meal_type}')>"


class PipelineRun(Base):
    """에이전트 워크플로우 실행 기록"""
    __tablename__ = "pipeline_runs"

    run_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    input_payload = Column(Text, nullable=True)  # JSON string
    status = Column(String(20), nullable=False, server_default="running")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_pipeline_run_user", "user_number"),
    )

    user = relationship("User", back_populates="pipeline_runs")
    run_items = relationship("PipelineRunItem", back_populates="pipeline_run")

    def __repr__(self):
        return f"<PipelineRun(run_id={self.run_id}, user_number={self.user_number}, status='{self.status}')>"


class PipelineRunItem(Base):
    """노드별 실행 기록"""
    __tablename__ = "pipeline_run_items"

    run_item_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    pipeline_run_id = Column(Integer, ForeignKey("pipeline_runs.run_id"), nullable=False)
    node_name = Column(String(50), nullable=False)  # A/B/C/Final
    input_payload = Column(Text, nullable=True)  # JSON string
    output_payload = Column(Text, nullable=True)  # JSON string
    status = Column(String(20), nullable=False, server_default="running")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_pipeline_run_item_run", "pipeline_run_id"),
        Index("ix_pipeline_run_item_node", "node_name"),
    )

    pipeline_run = relationship("PipelineRun", back_populates="run_items")

    def __repr__(self):
        return f"<PipelineRunItem(run_item_id={self.run_item_id}, node_name='{self.node_name}')>"


class MenuCollectionJob(Base):
    """Node B 메뉴 수집 작업"""
    __tablename__ = "menu_collection_jobs"

    job_id = Column(String(50), primary_key=True)
    status = Column(String(20), nullable=False, server_default="queued")
    requested_count = Column(Integer, nullable=False, server_default="0")
    success_count = Column(Integer, nullable=False, server_default="0")
    failure_count = Column(Integer, nullable=False, server_default="0")
    request_payload = Column(Text, nullable=True)  # JSON string
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    errors = Column(Text, nullable=True)  # JSON string

    __table_args__ = (
        Index("ix_menu_collection_job_status", "status"),
    )

    failures = relationship("MenuCollectionFailure", back_populates="job")

    def __repr__(self):
        return f"<MenuCollectionJob(job_id={self.job_id}, status='{self.status}')>"


class MenuCollectionFailure(Base):
    """Node B 메뉴 수집 실패 기록"""
    __tablename__ = "menu_collection_failures"

    failure_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String(50), ForeignKey("menu_collection_jobs.job_id"), nullable=False)
    restaurant_id = Column(Integer, nullable=True)
    stage = Column(String(30), nullable=False)
    reason = Column(String(50), nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_menu_collection_failure_job", "job_id"),
    )

    job = relationship("MenuCollectionJob", back_populates="failures")

    def __repr__(self):
        return f"<MenuCollectionFailure(failure_id={self.failure_id}, job_id='{self.job_id}')>"
