import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 환경 변수에서 DB 접속 정보 가져오기 (기존 설정과 동일)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")  # 기존 DB 이름
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")
DB_PORT = os.getenv("DB_PORT", "5432")

# SQLAlchemy DATABASE URL 구성
SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# 엔진 생성
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,  # DB 연결 상태 자동 체크
    pool_size=10,
    max_overflow=20
)

# 세션 생성기
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 모델의 기본 클래스
Base = declarative_base()

# DB 세션 의존성 주입 함수
def get_db():
    """FastAPI의 Depends에서 사용할 DB 세션 생성 함수"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()