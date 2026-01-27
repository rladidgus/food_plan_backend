# 인바디 기반 개인 맞춤 식단 추천 시스템

성별 특화 AI 체형 분류 및 맞춤형 식단 추천 시스템

## 🎯 주요 기능

- **6가지 체형 분류**: 성별별 K=3 군집 모델
  - 남성: 표준형, 과체중형, 근육질형
  - 여성: 표준형, 마른형, 과체중형
- **건강 상태 평가**: BMI, 체지방률, 내장지방, 골격근량 종합 분석
- **맞춤형 식단 추천**: 체형별 칼로리, 영양소, 식품 추천
- **FastAPI 백엔드**: RESTful API 제공
- **PostgreSQL**: 인바디 측정 히스토리 관리

## 📁 프로젝트 구조

```
food_plan/
├── app/                          # FastAPI 애플리케이션
│   ├── main.py                   # API 서버
│   ├── models.py                 # DB 모델
│   └── database.py               # DB 연결
│
├── ml/                           # 머신러닝 모듈
│   ├── inbody_scoring.py         # 건강 평가
│   ├── predict_cluster.py        # 체형 예측
│   └── diet_recommendation.py    # 식단 추천
│
├── models/                       # 학습된 모델
│   ├── inbody_male_k3_model.joblib
│   └── inbody_female_k3_model.joblib
│
├── scripts/                      # 학습 스크립트
│   ├── train_gender_specific.py  # 모델 학습
│   ├── analyze_clusters.py       # 군집 분석
│   └── find_optimal_k.py         # K 최적화
│
├── tests/                        # 테스트
│   └── test_inbody_system.py
│
├── data/                         # 데이터 및 분석 결과
│   ├── inbody_cleaned_ml_ready.csv
│   └── analysis/
│       ├── cluster_analysis.json
│       └── optimal_k_analysis.png
│
├── archive/                      # 구버전 파일
│   ├── train_inbody_cluster.py
│   └── inbody_cluster_model.joblib
│
├── run_app.sh                    # 서버 실행 스크립트
├── run_tests.sh                  # 테스트 실행 스크립트
├── requirements.txt
└── docker-compose.yml
```

## 🚀 빠른 시작

### 1. 환경 설정

```bash
# 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

### 2. 모델 학습 (최초 1회)

```bash
cd /home/user/food_plan
source venv/bin/activate
export PYTHONPATH=/home/user/food_plan:$PYTHONPATH

python scripts/train_gender_specific.py \
  --csv data/inbody_cleaned_ml_ready.csv \
  --k 3 \
  --latest_per_user
```

### 3. 테스트 실행

```bash
# 간편 실행
./run_tests.sh

# 또는 직접 실행
PYTHONPATH=/home/user/food_plan:$PYTHONPATH python tests/test_inbody_system.py
```

### 4. API 서버 실행

```bash
# 간편 실행
./run_app.sh

# 또는 직접 실행
PYTHONPATH=/home/user/food_plan:$PYTHONPATH python -m app.main
```

서버가 `http://localhost:8000`에서 실행됩니다.

## 📡 API 사용법

### 인바디 분석 요청

```bash
curl -X POST http://localhost:8000/api/analyze-inbody \
  -H "Content-Type: application/json" \
  -d '{
    "height": 175,
    "weight": 70,
    "body_fat_pct": 18,
    "skeletal_muscle_mass": 33,
    "bmr": 1600,
    "visceral_fat_level": 5,
    "gender": "M",
    "age": 30
  }'
```

### 응답 예시

```json
{
  "cluster_id": 2,
  "cluster_name": "남성 근육질형",
  "description": "체지방률이 낮고 골격근량이 매우 우수한...",
  "health_evaluation": {
    "bmi": {"value": 22.9, "category": "정상"},
    "body_fat": {"level": "정상"},
    "visceral_fat": {"level": "정상", "risk": "낮음"},
    "skeletal_muscle": {"level": "우수", "percentage": 47.1}
  },
  "recommended_diet": {
    "target_calories": 2728,
    "macros": {
      "protein_g": 238.7,
      "carbs_g": 306.9,
      "fat_g": 60.6
    },
    "recommended_foods": ["스테이크", "닭가슴살", ...],
    "tips": [...]
  }
}
```

### 측정 히스토리 조회

```bash
curl http://localhost:8000/api/inbody-history?user_id=1&limit=10
```

## 🐳 Docker 실행

```bash
docker-compose up -d
```

## 🧪 모델 성능

| 모델 | 샘플 수 | Silhouette Score | 개선율 |
|------|---------|------------------|--------|
| 남성 K=3 | 2,629 | 0.1680 | +17.5% |
| 여성 K=3 | 4,370 | 0.1362 | - |

## 📊 체형별 특성

### 남성
- **표준형** (30.9%): 평균 체지방률 23.2%, 골격근량 28.1kg
- **과체중형** (25.8%): 평균 체지방률 30.2%, 골격근량 35.9kg, 내장지방 11.5
- **근육질형** (43.3%): 평균 체지방률 18.1%, 골격근량 34.1kg

### 여성
- **표준형** (35.5%): 평균 체지방률 26.7%, 골격근량 23.2kg
- **마른형** (38.6%): 평균 체지방률 29.6%, 골격근량 19.0kg
- **과체중형** (25.9%): 평균 체지방률 38.7%, 골격근량 23.1kg, 내장지방 12.8

## 🛠️ 기술 스택

- **Backend**: FastAPI, SQLAlchemy, PostgreSQL
- **ML**: scikit-learn, pandas, numpy, joblib
- **API**: REST API, Pydantic
- **DevOps**: Docker, docker-compose

## 📝 라이선스

MIT License

## 👨‍💻 개발자

InBody Diet Recommendation System
