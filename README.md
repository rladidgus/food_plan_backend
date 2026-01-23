# 🥗 식단 계획 AI 서비스

Next.js + FastAPI + PostgreSQL로 구성된 식단 추천 서비스입니다.

## 📁 프로젝트 구조

```
food_plan/
├── backend/                 # 현재 디렉토리 (FastAPI)
│   ├── main.py             # FastAPI 애플리케이션
│   ├── database.py         # DB 연결 설정
│   ├── models.py           # SQLAlchemy 모델
│   ├── requirements.txt    # Python 패키지
│   ├── Dockerfile          # 백엔드 Docker 이미지
│   └── docker-compose.yml  # 전체 서비스 오케스트레이션
│
└── frontend/               # Next.js 프론트엔드 (별도 생성 필요)
    └── ...
```

## 🚀 실행 방법

### 1. Docker Compose로 전체 실행

```bash
# 컨테이너 빌드 및 실행
docker-compose up --build

# 백그라운드 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f

# 중지
docker-compose down
```

### 2. 로컬에서 개발 (백엔드만)

```bash
# 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# PostgreSQL 실행 (Docker)
docker-compose up db -d

# FastAPI 서버 실행
uvicorn main:app --reload
```

## 📡 API 엔드포인트

- **GET** `/` - 메인 페이지 (헬스 체크)
- **POST** `/api/record` - 식단 기록 생성
  ```json
  {
    "goal_calories": 2200
  }
  ```
  응답:
  ```json
  {
    "food_name": "불고기 덮밥",
    "calories": 800,
    "message": "목표 칼로리 2200kcal에 맞는 추천 메뉴가 기록되었습니다!"
  }
  ```

- **GET** `/api/mypage?limit=10` - 마이페이지 (식단 기록 조회)
  응답:
  ```json
  [
    {
      "id": 1,
      "goal_calories": 2200,
      "food_name": "불고기 덮밥",
      "calories": 800,
      "created_at": "2026-01-23T02:10:05.144227+00:00"
    }
  ]
  ```

### API 문서 (자동 생성)
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🔧 환경 변수

`.env.example` 파일을 참고하여 `.env` 파일을 생성하세요.

## 🌐 Next.js 프론트엔드 연동

프론트엔드는 `http://localhost:3000`에서 실행되며, 백엔드 API를 다음과 같이 호출합니다:

```javascript
// 예시 1: 식단 기록 생성
const response = await fetch('http://localhost:8000/api/record', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ goal_calories: 2200 }),
});
const data = await response.json();
console.log(data.food_name); // "불고기 덮밥"

// 예시 2: 마이페이지 조회
const mypage = await fetch('http://localhost:8000/api/mypage?limit=10');
const records = await mypage.json();
console.log(records); // 식단 기록 배열
```

## 📦 기술 스택

- **Backend**: FastAPI, SQLAlchemy, PostgreSQL
- **Frontend**: Next.js (별도)
- **Infrastructure**: Docker, Docker Compose
