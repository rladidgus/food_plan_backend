# Agent Workflow 구조 및 DB 스키마

이 문서는 `prd.md` 기반으로 **LangGraph 그래프 구조**와 **DB 스키마(초안)**를 정리한다.

## 코드 구조

### 그래프 모듈
- 파일: `app/agent_graph.py`
- 목적: LangGraph 기반 워크플로우 **스켈레톤** 제공
- 노드 구현은 추후 개별 에이전트에서 상세화

**그래프 흐름**
- `A_fetch_restaurants` → `B_fetch_menus` → `C_fetch_nutrition`
- `Final_recommend`는 **지속 실행 루프**로 별도 트리거/스케줄러에서 실행

**노드 역할**
- A: 주소 기준 500m 음식점 리스트 수집
- B: 음식점별 메뉴 수집 (A 결과를 순회)
- C: 메뉴 영양/칼로리 추출 (검색/추론 + 레퍼런스)
- Final: DB 기반으로 아침/점심/저녁 추천 (지속 실행 루프)

### 모델 정의
- 파일: `app/models.py`
- 신규 테이블 모델 추가
- `User`에 관계 필드 추가

### 의존성
- 파일: `requirements.txt`
- 추가: `langgraph`

## DB 스키마 (초안)

아래 테이블은 LangGraph 워크플로우 및 추천 시스템을 위한 최소 스키마이다.

### 1) location_profiles
사용자별 집/회사 위치 프로필 (사용자당 home/work 1개씩)
- `location_id` (PK)
- `user_number` (FK -> users.user_number)
- `label` (home/work)
- `address_text`
- `lat`
- `lng`
- `created_at`
- `updated_at`

제약/인덱스
- `UNIQUE(user_number, label)`
- `INDEX(user_number)`

### 2) restaurants
반경 내 음식점 마스터
- `restaurant_id` (PK)
- `source` (kakao/naver/etc)
- `source_place_id` (외부 고유 ID)
- `name`
- `category`
- `address_text`
- `lat`
- `lng`
- `phone`
- `place_url`
- `created_at`
- `updated_at`

제약/인덱스
- `UNIQUE(source, source_place_id)`
- `INDEX(source, source_place_id)`

### 3) restaurant_snapshots
검색 시점의 스냅샷 (접근성/근접성 평가용)
- `snapshot_id` (PK)
- `location_profile_id` (FK -> location_profiles.location_id)
- `restaurant_id` (FK -> restaurants.restaurant_id)
- `distance_m`
- `collected_at`

인덱스
- `INDEX(location_profile_id)`
- `INDEX(restaurant_id)`

### 4) menu_items
음식점별 메뉴 마스터
- `menu_id` (PK)
- `restaurant_id` (FK -> restaurants.restaurant_id)
- `name`
- `description`
- `price`
- `source` (scraping/ocr/manual)
- `source_url`
- `created_at`
- `updated_at`

인덱스
- `INDEX(restaurant_id)`

### 5) nutrition_facts
메뉴별 영양 정보 (검색/추론 기반 포함)
- `nutrition_id` (PK)
- `menu_item_id` (FK -> menu_items.menu_id)
- `calories_kcal`
- `carbs_g`
- `protein_g`
- `fat_g`
- `sodium_mg`
- `sugar_g`
- `fiber_g`
- `source_type` (search/infer/manual)
- `source_ref` (URL or reference text)
- `confidence` (0~1)
- `created_at`
- `updated_at`

인덱스
- `INDEX(menu_item_id)`

### 6) meal_recommendations
최종 추천 결과
- `recommendation_id` (PK)
- `user_number` (FK -> users.user_number)
- `meal_type` (breakfast/lunch/dinner)
- `menu_item_id` (FK -> menu_items.menu_id)
- `reason_text`
- `score_nutrition`
- `score_accessibility`
- `total_score`
- `created_at`

인덱스
- `INDEX(user_number)`
- `INDEX(meal_type)`

### 7) pipeline_runs
에이전트 워크플로우 실행 기록
- `run_id` (PK)
- `user_number` (FK -> users.user_number)
- `input_payload` (JSON string)
- `status` (running/success/fail)
- `started_at`
- `finished_at`
- `error_message`

인덱스
- `INDEX(user_number)`

### 8) pipeline_run_items
노드별 실행 기록 (선택)
- `run_item_id` (PK)
- `pipeline_run_id` (FK -> pipeline_runs.run_id)
- `node_name` (A/B/C/Final)
- `input_payload` (JSON string)
- `output_payload` (JSON string)
- `status`
- `started_at`
- `finished_at`
- `error_message`

인덱스
- `INDEX(pipeline_run_id)`
- `INDEX(node_name)`

## 적용 메모
- 서버 시작 시 `Base.metadata.create_all`로 테이블 자동 생성
- 노드 구현은 추후 개별 에이전트로 확장
