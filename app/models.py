from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    """사용자 테이블 모델"""
    __tablename__ = "users"
    
    # 기본 정보
    user_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    id = Column(String(50), unique=True, nullable=False)  # 로그인 아이디
    username = Column(String(50), nullable=False)  # 사용자 이름 (표시용)
    password = Column(String(100), nullable=False)
    user_created_at = Column(DateTime(timezone=True), server_default=func.now())
    email = Column(String(100), unique=True, nullable=True)
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True)
    goal = Column(String(50), nullable=True)
    
    # 목표 영양소 (메인페이지 계산용)
    goal_calories = Column(Float, nullable=True)
    goal_protein = Column(Float, nullable=True)
    goal_carbs = Column(Float, nullable=True)
    goal_fats = Column(Float, nullable=True)

    # 현재 영양소 (메인페이지 계산용)
    current_calories = Column(Float, nullable=True)
    current_protein = Column(Float, nullable=True)
    current_carbs = Column(Float, nullable=True)
    current_fats = Column(Float, nullable=True)

    # 인바디 관련 (최신값: 필수 항목만)
    body_fat_pct = Column(Float, nullable=True)
    skeletal_muscle_mass = Column(Float, nullable=True)
    bmr = Column(Float, nullable=True)
    inbody_score = Column(Integer, nullable=True)

    # 관계 설정
    records = relationship("Record", back_populates="user")
    bmi_histories = relationship("BMIHistory", back_populates="user")
    inbody_records = relationship("InBodyRecord", back_populates="user")
    
    def __repr__(self):
        return f"<User(user_id={self.user_id}, username='{self.username}')>"


class Food(Base):
    """식품 테이블 모델 - 음식 마스터 데이터"""
    __tablename__ = "food"
    
    food_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    food_name = Column(String(100), nullable=False)
    food_calories = Column(Float, nullable=False)
    food_protein = Column(Float, nullable=False)
    food_carbs = Column(Float, nullable=False)
    food_fats = Column(Float, nullable=False)
    
    # 관계 설정
    records = relationship("Record", back_populates="food")
    
    def __repr__(self):
        return f"<Food(food_id={self.food_id}, food_name='{self.food_name}')>"


class Record(Base):
    """식단 기록 테이블 - 기록 당시의 영양 정보를 스냅샷으로 저장"""
    __tablename__ = "records"
    
    record_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    
    food_id = Column(Integer, ForeignKey("food.food_id"), nullable=True)
    
    # 기록 당시의 영양 정보 스냅샷 (항상 저장)
    # Food 테이블이 변경되어도 과거 기록은 불변!
    food_name = Column(String(100), nullable=False)
    food_calories = Column(Float, nullable=False)
    food_protein = Column(Float, nullable=False)
    food_carbs = Column(Float, nullable=False)
    food_fats = Column(Float, nullable=False)

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
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    bmi = Column(Float, nullable=False)
    bmi_history_created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 관계 설정
    user = relationship("User", back_populates="bmi_histories")
    
    def __repr__(self):
        return f"<BMIHistory(bmi_history_id={self.bmi_history_id}, user_id={self.user_id}, bmi={self.bmi})>"


class InBodyRecord(Base):
    """인바디 측정 기록 테이블"""
    __tablename__ = "inbody_records"
    
    inbody_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    
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
            f"<InBodyRecord(inbody_id={self.inbody_id}, user_id={self.user_id}, "
            f"measurement_date={self.measurement_date}, source={self.source})>"
        )
