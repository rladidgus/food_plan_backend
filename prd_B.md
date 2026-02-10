# PRD_B: 음식점별 메뉴 수집 (Node B)

## 1. 목적
Node A에서 **주소 기준 반경 내 음식점 리스트**가 수집되면, 각 음식점의 **메뉴 정보를 추출**하여 DB에 저장한다.

## 2. 입력/출력

### 입력
- `restaurant_ids: list[int]`
  - Node A에서 수집된 음식점 ID 목록

### 출력
- `menu_item_ids: list[int]`
  - DB에 저장된 메뉴 ID 목록

## 3. 처리 흐름

1. `restaurant_ids` 순회
2. 각 음식점의 `place_url` 확인
3. `place_url` 기준 메뉴 스크래핑 수행
4. 메뉴명/가격/설명 추출
5. `menu_items` 테이블에 저장 (업서트)
6. 실패한 음식점은 스킵하고 로그 기록

## 4. 데이터 소스
- 음식점 `place_url` 기반 웹 스크래핑
- (추후) OCR / 제휴 API 확장 가능

## 5. 저장 정책

### 테이블: `menu_items`
- `restaurant_id` 기준으로 메뉴 저장
- 동일 음식점 내 동일 메뉴명 중복 방지

**제약**
- `UNIQUE(restaurant_id, name)`

## 6. 실패/예외 처리
- `place_url`이 없는 경우: 스킵 + 로그
- 스크래핑 실패: 스킵 + 실패 로그
- 일시 오류: 재시도 가능 (추후 정책)

## 7. 속도/정책
- 요청 간 rate limiting 적용
- robots.txt 준수
- 대량 수집 시 배치 처리 고려

## 8. 코드 연결점

### 그래프 연결
- `A_fetch_restaurants` → `B_fetch_menus`

### 구현 파일
- 노드: `app/nodes/b_fetch_menus.py`
- 서비스: `app/services/menu_scraper.py`

## 9. 향후 확장
- 메뉴 이미지 수집
- 메뉴 영양 라벨 OCR
- 메뉴 변동 감지 및 업데이트
=======
PRD_B: 음식점별 메뉴 수집 (Node B) - v2
1. 목적
Node A에서 수집된 음식점 정보를 바탕으로 상세 메뉴 정보를 수집한다. 기본적으로 웹 스크래핑을 시도하되, 실패 시 LLM을 활용한 정보 추론(검색 기반)을 수행하여 수집 성공률을 극대화한다. 최종적으로 네이버 지도 API와 연동하여 데이터의 정밀도를 높이고 DB에 저장한다.

2. 입력/출력
입력
restaurant_ids: list[int]

Node A에서 수집된 음식점 ID 목록 및 기본 정보(상호명, 주소 등)

출력
menu_item_ids: list[int]

DB에 저장된 메뉴 ID 목록

3. 처리 흐름
기본 수집 (Scraping): 각 음식점의 place_url을 기반으로 메뉴명/가격/설명 스크래핑 수행.

실패 시 대응 (LLM Fallback):

스크래핑 차단 또는 구조 변경으로 실패 시, LLM(GPT-4o 등)을 활용하여 해당 식당의 최신 메뉴 정보를 검색 및 추론.

데이터 보정 (Naver Maps API):

메뉴 정보가 수집된 경우, 네이버 지도 API를 호출하여 식당의 최신 메타데이터(카테고리, 영업 여부 등)와 메뉴 정보의 일치 여부 확인 및 보정.

DB 저장: menu_items 테이블에 업서트(Upsert).

실패 기록: 모든 시도(Scraping + LLM)가 실패한 경우에만 최종 실패 로그 기록.

4. 데이터 소스
Primary: 네이버 플레이스(place_url) 웹 스크래핑

Secondary (Fallback): LLM 기반 검색 및 정보 추론 (Search-Augmented Generation)

Validation/Enrichment: 네이버 지도 API (Search API 등)

5. 저장 정책
테이블: menu_items
restaurant_id 기준으로 메뉴 저장

collection_method: 수집 방법(SCRAPING, LLM, API)을 구분하여 기록

동일 음식점 내 동일 메뉴명 중복 방지 (UNIQUE 제약 유지)

테이블: nutrition_facts (Node C 연계)
수집된 메뉴명을 바탕으로 Node C에서 영양정보 검색/매칭 수행

6. 실패/예외 처리
스크래핑 실패: 즉시 LLM 기반 추론 단계로 전환.

LLM 추론 실패: 검색 결과가 없거나 신뢰도가 낮은 경우 해당 음식점 스킵 및 로그 생성.

API 연동 오류: API 호출 실패 시 스크래핑/LLM으로 수집된 데이터 우선 저장(Warning 로그).

7. 속도/정책
요청 간 Rate Limiting 적용 (IP 차단 방지)

LLM 호출 비용 최적화를 위해 스크래핑 실패 건에 대해서만 선별적 수행

네이버 지도 API 할당량(Quota) 관리 및 모니터링

8. 코드 연결점
그래프 연결
A_fetch_restaurants → B_fetch_menus (Scraping + LLM + API) → C_fetch_nutrition

구현 파일
노드: app/nodes/b_fetch_menus.py

서비스:

app/services/menu_scraper.py (스크래핑 로직)

app/services/llm_inference.py (실패 시 LLM 추론 로직)

app/services/naver_api_client.py (네이버 지도 API 연동)

9. 향후 확장
메뉴판 사진 OCR 도입 (스크래핑/LLM 보완)

메뉴 변동 이력 관리 (Versioning)

10. API 명세 (음식점별 메뉴 수집)
10.1 공통
Base URL: /api/v1

Auth: 내부 서비스 호출 기준 (추후 JWT 등으로 확장 가능)

Content-Type: application/json

10.2 메뉴 수집 실행 (비동기 작업 생성)
POST /api/v1/menus/collect

목적
지정한 restaurant_ids에 대해 Node B 메뉴 수집을 실행하고, 작업(Job)을 생성한다.

Request
{
  "restaurant_ids": [123, 456],
  "collection_method_priority": ["SCRAPING", "LLM", "API"],
  "rate_limit_ms": 800,
  "naver_validation": true,
  "llm_fallback": true
}

Response 202
{
  "job_id": "bmenu-20250210-001",
  "status": "queued",
  "requested_count": 2,
  "created_at": "2026-02-10T09:15:00Z"
}

Validation
- restaurant_ids는 1개 이상, 최대 100개
- collection_method_priority는 SCRAPING/LLM/API의 부분집합

10.3 메뉴 수집 결과 조회 (Job 상태)
GET /api/v1/menus/collect/{job_id}

Response 200
{
  "job_id": "bmenu-20250210-001",
  "status": "running",
  "requested_count": 2,
  "success_count": 1,
  "failure_count": 0,
  "started_at": "2026-02-10T09:15:10Z",
  "updated_at": "2026-02-10T09:15:20Z",
  "errors": []
}

Status 값
- queued | running | completed | failed | partial

10.4 메뉴 수집 결과 상세 (레스토랑 단위)
GET /api/v1/restaurants/{restaurant_id}/menus

Query
- include_source: bool (default: false)
- include_confidence: bool (default: false)

Response 200
{
  "restaurant_id": 123,
  "menus": [
    {
      "menu_id": 9001,
      "name": "김치찌개",
      "description": "돼지고기, 김치, 두부",
      "price": 9000,
      "collection_method": "SCRAPING",
      "source_url": "https://place.naver.com/xxxx"
    }
  ],
  "updated_at": "2026-02-10T09:16:00Z"
}

10.5 실패 로그 조회 (Job)
GET /api/v1/menus/collect/{job_id}/failures

Response 200
{
  "job_id": "bmenu-20250210-001",
  "failures": [
    {
      "restaurant_id": 456,
      "stage": "LLM",
      "reason": "search_no_result",
      "detail": "no menu result with confidence >= 0.6"
    }
  ]
}

10.6 에러 코드
- 400 INVALID_REQUEST: 입력 파라미터 오류
- 404 NOT_FOUND: restaurant_id 또는 job_id 없음
- 409 CONFLICT: 이미 수행 중인 동일 수집 작업
- 429 RATE_LIMITED: 내부 rate limit 초과
- 500 INTERNAL_ERROR: 알 수 없는 서버 오류
