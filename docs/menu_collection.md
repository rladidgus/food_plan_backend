# Node B 메뉴 수집 문서

이 문서는 최근 반영된 **메뉴 수집(Job 기반) API/서비스/노드 연결** 내용을 정리합니다.

## 1. 개요
- Node B(음식점별 메뉴 수집)는 **스크래핑 → LLM → Naver API 보정/검증** 순서로 수집을 시도합니다.
- 수집 작업은 **Job**으로 생성되어 비동기 처리됩니다.
- 실패는 별도 테이블에 기록되어 재수집/분석에 활용 가능합니다.

## 2. API (요약)
- `POST /api/v1/menus/collect`  
  메뉴 수집 Job 생성
- `GET /api/v1/menus/collect/{job_id}`  
  Job 상태 조회
- `GET /api/v1/menus/collect/{job_id}/failures`  
  Job 실패 내역 조회
- `GET /api/v1/restaurants/{restaurant_id}/menus`  
  음식점 메뉴 조회 (`include_source`, `include_confidence`)

상세 스펙은 `docs/openapi_b_menus.yaml` 참고.

## 3. DB 테이블
### 3.1 menu_collection_jobs
- Job 메타데이터와 상태를 저장
- 핵심 컬럼: `job_id`, `status`, `requested_count`, `success_count`, `failure_count`, `request_payload`

### 3.2 menu_collection_failures
- Job 실패 기록 저장
- 핵심 컬럼: `job_id`, `restaurant_id`, `stage`, `reason`, `detail`

## 4. 코드 구성
### 4.1 API 라우터
- `app/api/menus.py`
  - Job 생성/상태/실패 조회
  - 메뉴 조회
  - DB 기반 Job/Failure 기록

### 4.2 공용 수집 로직
- `app/services/menu_collection.py`
  - `collect_menus_for_restaurants`
  - `collect_menus_for_restaurant`
  - 메뉴 정규화/업서트 로직

### 4.3 수집 소스 구현
- `app/services/menu_scraper.py`
  - JSON-LD 기반 스크래핑 (best-effort)
- `app/services/llm_inference.py`
  - OpenAI API 기반 메뉴 추론
- `app/services/naver_api_client.py`
  - Naver Local Search로 place URL 확보 후 메뉴 스크래핑

### 4.4 Node B 연결
- `app/nodes/b_fetch_menus.py`
  - 공용 수집 로직 사용
  - 실패는 `state.errors`에 기록

## 5. 환경 변수
- `OPENAI_API_KEY`
- `MENU_LLM_MODEL` (기본 `gpt-4o-mini`)
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `MENU_COLLECTION_PRIORITY` (기본 `SCRAPING,LLM,API`)
- `MENU_LLM_FALLBACK` (기본 `true`)
- `MENU_NAVER_VALIDATION` (기본 `true`)
- `MENU_RATE_LIMIT_MS` (기본 `800`)

## 6. 동작 흐름
1. `POST /menus/collect` 호출 → `menu_collection_jobs` 생성
2. Background task가 restaurant 단위 수집 실행
3. 실패는 `menu_collection_failures`에 기록
4. 상태/실패 조회는 DB 기반으로 제공

## 7. 제약/주의
- 스크래핑은 JSON-LD 기반 파싱이므로 사이트 구조에 따라 빈 결과 가능
- Naver API 사용 시 쿼터/오류 처리가 필요
- Job 상태는 DB에 저장되며, 마이그레이션은 별도 적용 필요
