# PRD: 식단 데이터 수집 Agent

## 1. 개요

### 1.1 목적
사용자의 **집/회사 주소**를 입력받아, 각 위치 반경 내 음식점·메뉴·영양정보를 자동 수집하여 DB를 구축하는 **데이터 수집 Agent**를 구현한다.

### 1.2 Agent 분리 전략

| Agent | 역할 | 상태 |
|-------|------|------|
| **데이터 수집 Agent** (본 문서) | 주소 기반 음식점·메뉴·영양정보 수집 → DB 저장 | 현재 구현 대상 |
| **추천 Agent** (별도 문서) | 수집된 DB 기반 LLM 식단 추천 (아침/점심/저녁) | 향후 구현 예정 |

두 Agent는 **DB를 인터페이스**로 완전히 분리된다. 데이터 수집 Agent가 DB를 구축하면, 추천 Agent는 해당 DB만 조회하여 추천을 수행한다.

### 1.3 핵심 원칙
- 데이터는 **User별, Location(home/work)별**로 명확히 분리되어 저장
- 한 User의 home 근처 음식점과 work 근처 음식점은 독립적으로 조회 가능해야 함
- 동일 음식점이 여러 User/Location에서 발견되더라도 음식점 마스터는 중복 저장하지 않음 (참조 관계로 연결)

---

## 2. 시스템 아키텍처

### 2.1 LangGraph 파이프라인

```
[Input: user_number, address(home/work)]
         │
         ▼
 ┌─────────────────────┐
 │  A_fetch_restaurants │  Naver Maps API로 반경 내 음식점 수집
 └─────────┬───────────┘
           │ restaurants[]
           ▼
 ┌─────────────────────┐
 │   B_fetch_menus     │  음식점별 메뉴 수집 (웹 스크래핑)
 └─────────┬───────────┘
           │ menu_items[]
           ▼
 ┌─────────────────────┐
 │  C_fetch_nutrition  │  메뉴별 영양정보 수집 (웹서치 → LLM fallback)
 └─────────┬───────────┘
           │
           ▼
       [DB 저장]
```

### 2.2 실행 단위
- **입력**: `user_number` + `label`(home 또는 work) + `address_text`
- **출력**: 해당 위치 기준 음식점·메뉴·영양정보가 DB에 저장됨
- 한 사용자에 대해 home/work 각각 1회씩 실행 (총 2회)

### 2.3 코드 구조

```
app/
├── agent_graph.py        # LangGraph 그래프 정의 (A→B→C 파이프라인)
├── nodes/
│   ├── a_fetch_restaurants.py   # 노드 A 구현
│   ├── b_fetch_menus.py         # 노드 B 구현
│   └── c_fetch_nutrition.py     # 노드 C 구현
├── models.py             # SQLAlchemy ORM 모델 (스키마 정의)
├── schemas.py            # Pydantic 스키마 (State, Input/Output)
└── services/
    ├── naver_api.py      # Naver Maps API 클라이언트
    ├── menu_scraper.py   # 메뉴 스크래핑 서비스
    └── nutrition_searcher.py  # 영양정보 검색/추론 서비스
```

---

## 3. 노드별 상세 스펙

### 3.1 Node A: `A_fetch_restaurants`

**목적**: 주어진 주소 기준 반경 500m 내 음식점 리스트 수집

**데이터 소스**: Naver Maps API (Local Search)

**입력**:
- `address_text`: 사용자 주소 (예: "서울 강남구 테헤란로 123")
- `radius_m`: 검색 반경 (기본값: 500)

**처리 흐름**:
1. 주소 → 좌표 변환 (Naver Geocoding API)
2. 좌표 + 반경으로 음식점 카테고리 검색
3. 페이지네이션 처리 (최대 45건 = 15건 × 3페이지)
4. 결과를 `restaurants` 테이블에 upsert (source + source_place_id 기준)
5. `restaurant_snapshots` 테이블에 해당 location_profile과의 거리 스냅샷 저장

**출력 State**:
- `restaurant_ids: list[int]` — 수집된 음식점 ID 목록

**Naver API 필드 매핑**:

| Naver 응답 필드 | DB 컬럼 |
|-----------------|---------|
| `link` (또는 place id) | `source_place_id` |
| `title` | `name` |
| `category` | `category` |
| `roadAddress` | `address_text` |
| `mapy` | `lat` |
| `mapx` | `lng` |
| `telephone` | `phone` |
| `link` | `place_url` |

### 3.2 Node B: `B_fetch_menus`

**목적**: 수집된 음식점별 메뉴 정보 수집

**데이터 소스**: 웹 스크래핑 (음식점 `place_url` 활용)

**입력**:
- `restaurant_ids: list[int]` — Node A에서 전달받은 음식점 ID 목록

**처리 흐름**:
1. 각 restaurant의 `place_url` 접근하여 메뉴 정보 스크래핑
2. 메뉴명, 가격, 설명 추출
3. `menu_items` 테이블에 저장
4. 스크래핑 실패 시 해당 음식점 skip (로그 기록)

**출력 State**:
- `menu_item_ids: list[int]` — 수집된 메뉴 ID 목록

**주의사항**:
- 요청 간 적절한 딜레이 (rate limiting 준수)
- robots.txt 확인
- place_url이 없는 경우 skip

### 3.3 Node C: `C_fetch_nutrition`

**목적**: 메뉴별 영양/칼로리 정보 수집

**데이터 소스**: 2단계 전략
1. **1차 — 웹서치**: 해당 음식점의 해당 메뉴에 대한 칼로리/영양정보를 웹에서 검색
2. **2차 — LLM 추론 (fallback)**: 웹서치로 못 찾은 경우, LLM이 메뉴명·카테고리 기반으로 영양정보 추론

**입력**:
- `menu_item_ids: list[int]` — Node B에서 전달받은 메뉴 ID 목록

**처리 흐름**:
1. 각 menu_item에 대해:
   a. 웹서치 수행: `"{음식점명} {메뉴명} 칼로리"` 등의 쿼리
   b. 검색 결과에서 영양정보 추출 (파싱)
   c. 추출 성공 시 `source_type = 'search'`, `confidence` 산출
   d. 추출 실패 시 LLM에 메뉴명·카테고리 전달하여 추론
      - `source_type = 'infer'`, `confidence`는 LLM 자체 평가
2. `nutrition_facts` 테이블에 저장

**출력 State**:
- `nutrition_ids: list[int]` — 저장된 영양정보 ID 목록

**LLM 추론 프롬프트 (예시)**:
```
다음 메뉴의 영양정보를 추정해주세요.
- 음식점: {restaurant_name} ({category})
- 메뉴: {menu_name}
- 가격: {price}원

JSON 형식으로 응답:
{calories_kcal, carbs_g, protein_g, fat_g, sodium_mg, sugar_g, fiber_g, confidence}
```

---

## 4. DB 스키마

PostgreSQL 기반. 테이블 생성은 별도 프로세스에서 수행하며, 여기서는 스키마만 정의한다.

### 4.1 ER 다이어그램 (텍스트)

```
users (기존)
  │
  ├──< location_profiles        (User당 home/work 각 1건)
  │       │
  │       └──< restaurant_snapshots   (Location별 음식점 거리 스냅샷)
  │               │
  │               └──── restaurants    (음식점 마스터, 중복 없음)
  │                        │
  │                        └──< menu_items        (음식점별 메뉴)
  │                                │
  │                                └──< nutrition_facts   (메뉴별 영양정보)
```

**핵심 분리 구조**:
- `location_profiles`가 User와 Location을 연결
- `restaurant_snapshots`가 Location과 Restaurant을 연결 (N:M 관계 해소)
- 같은 음식점이 여러 User/Location에서 발견되면 `restaurants`에는 1건만 존재하고, `restaurant_snapshots`에 각각 기록

### 4.2 테이블 정의

#### `location_profiles`
사용자별 집/회사 위치 프로필

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `location_id` | `SERIAL PK` | |
| `user_number` | `INTEGER FK → users.user_number` | |
| `label` | `VARCHAR(10)` | `'home'` 또는 `'work'` |
| `address_text` | `TEXT` | 주소 원문 |
| `lat` | `DOUBLE PRECISION` | 위도 (Geocoding 결과) |
| `lng` | `DOUBLE PRECISION` | 경도 (Geocoding 결과) |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

- **제약**: `UNIQUE(user_number, label)`
- **인덱스**: `INDEX(user_number)`

#### `restaurants`
음식점 마스터 (전역 단일 테이블, 중복 없음)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `restaurant_id` | `SERIAL PK` | |
| `source` | `VARCHAR(20)` | `'naver'` |
| `source_place_id` | `VARCHAR(50)` | 외부 API 고유 ID |
| `name` | `VARCHAR(200)` | 음식점명 |
| `category` | `VARCHAR(100)` | 카테고리 |
| `address_text` | `TEXT` | 도로명 주소 |
| `lat` | `DOUBLE PRECISION` | |
| `lng` | `DOUBLE PRECISION` | |
| `phone` | `VARCHAR(20)` | |
| `place_url` | `TEXT` | |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

- **제약**: `UNIQUE(source, source_place_id)`
- **인덱스**: `INDEX(source, source_place_id)`

#### `restaurant_snapshots`
Location별 음식점 스냅샷 (User-Location과 Restaurant의 관계 테이블)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `snapshot_id` | `SERIAL PK` | |
| `location_profile_id` | `INTEGER FK → location_profiles.location_id` | |
| `restaurant_id` | `INTEGER FK → restaurants.restaurant_id` | |
| `distance_m` | `INTEGER` | 위치로부터의 거리 (m) |
| `collected_at` | `TIMESTAMPTZ` | 수집 시각 |

- **제약**: `UNIQUE(location_profile_id, restaurant_id)` — 동일 Location에 같은 음식점 중복 방지
- **인덱스**: `INDEX(location_profile_id)`, `INDEX(restaurant_id)`

#### `menu_items`
음식점별 메뉴

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `menu_id` | `SERIAL PK` | |
| `restaurant_id` | `INTEGER FK → restaurants.restaurant_id` | |
| `name` | `VARCHAR(200)` | 메뉴명 |
| `description` | `TEXT` | 메뉴 설명 |
| `price` | `INTEGER` | 가격 (원) |
| `source` | `VARCHAR(20)` | `'scraping'` / `'ocr'` / `'manual'` |
| `source_url` | `TEXT` | 출처 URL |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

- **제약**: `UNIQUE(restaurant_id, name)` — 같은 음식점 내 메뉴명 중복 방지
- **인덱스**: `INDEX(restaurant_id)`

#### `nutrition_facts`
메뉴별 영양정보

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `nutrition_id` | `SERIAL PK` | |
| `menu_item_id` | `INTEGER FK → menu_items.menu_id` | |
| `calories_kcal` | `REAL` | 칼로리 (kcal) |
| `carbs_g` | `REAL` | 탄수화물 (g) |
| `protein_g` | `REAL` | 단백질 (g) |
| `fat_g` | `REAL` | 지방 (g) |
| `sodium_mg` | `REAL` | 나트륨 (mg) |
| `sugar_g` | `REAL` | 당류 (g) |
| `fiber_g` | `REAL` | 식이섬유 (g) |
| `source_type` | `VARCHAR(10)` | `'search'` / `'infer'` |
| `source_ref` | `TEXT` | 출처 URL 또는 참고 텍스트 |
| `confidence` | `REAL` | 신뢰도 (0.0 ~ 1.0) |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

- **인덱스**: `INDEX(menu_item_id)`

### 4.3 데이터 분리 조회 예시

**User 1의 home 근처 음식점 + 메뉴 조회**:
```sql
SELECT r.name, r.category, mi.name AS menu, mi.price,
       nf.calories_kcal, nf.protein_g
FROM location_profiles lp
JOIN restaurant_snapshots rs ON rs.location_profile_id = lp.location_id
JOIN restaurants r ON r.restaurant_id = rs.restaurant_id
JOIN menu_items mi ON mi.restaurant_id = r.restaurant_id
LEFT JOIN nutrition_facts nf ON nf.menu_item_id = mi.menu_id
WHERE lp.user_number = 1
  AND lp.label = 'home'
ORDER BY rs.distance_m;
```

**User 1의 work 근처 저칼로리 메뉴 조회**:
```sql
SELECT r.name, mi.name AS menu, nf.calories_kcal
FROM location_profiles lp
JOIN restaurant_snapshots rs ON rs.location_profile_id = lp.location_id
JOIN restaurants r ON r.restaurant_id = rs.restaurant_id
JOIN menu_items mi ON mi.restaurant_id = r.restaurant_id
JOIN nutrition_facts nf ON nf.menu_item_id = mi.menu_id
WHERE lp.user_number = 1
  AND lp.label = 'work'
  AND nf.calories_kcal < 500
ORDER BY nf.calories_kcal;
```

---

## 5. LangGraph State 정의

```python
from typing import TypedDict

class AgentState(TypedDict):
    # 입력
    user_number: int
    label: str           # 'home' | 'work'
    address_text: str

    # 중간 결과
    location_profile_id: int
    lat: float
    lng: float
    restaurant_ids: list[int]
    menu_item_ids: list[int]
    nutrition_ids: list[int]

    # 실행 로그
    errors: list[str]
```

---

## 6. 기술 스택

| 구분 | 기술 |
|------|------|
| Agent 프레임워크 | LangGraph |
| LLM | Claude API (영양정보 추론 fallback) |
| 음식점 검색 | Naver Maps API |
| 메뉴 수집 | 웹 스크래핑 (httpx + BeautifulSoup) |
| 영양정보 검색 | 웹서치 API (Tavily / SerpAPI 등) |
| ORM | SQLAlchemy |
| DB | PostgreSQL (스키마 정의만, 테이블 생성은 별도) |
| 비동기 | asyncio (API 호출 병렬화) |

---

## 7. 향후 계획 (Out of Scope)

아래 항목은 본 Agent의 범위 밖이며, 별도 Agent/프로세스로 구현 예정:

- **추천 Agent**: DB 기반 아침/점심/저녁 식단 추천 (LLM 활용)
- **테이블 생성**: `Base.metadata.create_all` 또는 Alembic 마이그레이션
- **스케줄러**: 데이터 수집 주기적 갱신 (변경 감지)
- **사용자 선호도**: 알레르기, 식이 제한 등 개인화 데이터
