from sqlalchemy import Column, Integer, String, DateTime, Date, Float, ForeignKey, UniqueConstraint, Index, text
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
    target_calory = Column(Float, nullable=True)
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
    food_proteins = Column(Float, nullable=False)
    food_carbs = Column(Float, nullable=False)
    food_fats = Column(Float, nullable=False)
    food_image = Column(String, nullable=True)
    
    # 관계 설정
    user = relationship("User", back_populates="foods")
    records = relationship("Record", back_populates="food")
    
    def __repr__(self):
        return f"<Food(food_id={self.food_id}, food_name='{self.food_name}')>"


class Record(Base):
    """식단 기록 테이블 - 기록 당시의 영양 정보를 스냅샷으로 저장"""
    __tablename__ = "records"
    
    record_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_number = Column(Integer, ForeignKey("users.user_number"), nullable=False)
    
    food_id = Column(Integer, ForeignKey("food.food_id"), nullable=True)
    
    # 기록 당시의 영양 정보 스냅샷 (항상 저장)
    # Food 테이블이 변경되어도 과거 기록은 불변!
    food_name = Column(String(100), nullable=False)
    food_calories = Column(Float, nullable=False)
    food_protein = Column(Float, nullable=False)
    food_carbs = Column(Float, nullable=False)
    food_fats = Column(Float, nullable=False)
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
    predicted_cluster = Column(Integer, nullable=True)  # 체형 클러스터 ID (선택)
    cluster_name = Column(String(50), nullable=True)    # 체형 클러스터 이름 (선택)
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
