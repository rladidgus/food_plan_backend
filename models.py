from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    """사용자 테이블 모델"""
    __tablename__ = "users"
    
    # 기본 정보
    user_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(100), nullable=False)
    user_created_at = Column(DateTime(timezone=True), server_default=func.now())
    email = Column(String(100), unique=True, nullable=False)
    height = Column(Float, nullable=False)
    weight = Column(Float, nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(10), nullable=False)
    goal = Column(String(50), nullable=False)
    
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

    # 관계 설정
    records = relationship("Record", back_populates="user")
    bmi_histories = relationship("BMIHistory", back_populates="user")
    
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


