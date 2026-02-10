# Plan: Node A (A_fetch_restaurants)

**목표**
사용자 `home/work` 주소를 기준으로 반경 500m 내 음식점을 수집하고, 음식점 마스터는 중복 없이 upsert하며, 사용자 위치와 음식점의 관계 스냅샷을 저장한다.

**범위**
- 포함: 주소 → 좌표 변환, Naver Maps API 검색, 음식점 upsert, 스냅샷 저장, 결과 ID 리스트 반환
- 제외: 메뉴 스크래핑, 영양정보 수집

**입력/출력 (State 기준)**
- 입력: `user_number`, `label`(home/work), `address_text`, `radius_m`(기본 500)
- 출력: `location_profile_id`, `lat`, `lng`, `restaurant_ids`

**의존성**
- `services/naver_api.py`: geocoding + local search
- `models.py`: `LocationProfile`, `Restaurant`, `RestaurantSnapshot`
- `schemas.py`: `AgentState`
- DB: PostgreSQL, unique 제약 준수

**데이터 매핑 (Naver → DB)**
| Naver 필드 | DB 컬럼 |
| --- | --- |
| link (또는 place id) | restaurants.source_place_id |
| title | restaurants.name |
| category | restaurants.category |
| roadAddress | restaurants.address_text |
| mapy | restaurants.lat |
| mapx | restaurants.lng |
| telephone | restaurants.phone |
| link | restaurants.place_url |

**처리 흐름**
1. 입력 검증: `user_number`, `label`, `address_text` 존재 확인
2. 주소 → 좌표 변환: Naver Geocoding API 호출
3. `location_profiles` upsert
- `UNIQUE(user_number, label)` 기준으로 기존 레코드 조회 후 업데이트
- 없으면 새로 생성
4. 음식점 검색
- Naver Local Search API 사용
- 반경 `radius_m` (기본 500m)
- 페이지네이션 최대 3페이지, 페이지당 15건
5. 음식점 upsert
- `UNIQUE(source, source_place_id)` 기준으로 upsert
- `source`는 `'naver'`
6. `restaurant_snapshots` 저장
- `UNIQUE(location_profile_id, restaurant_id)` 기준 중복 방지
- `distance_m` 저장
7. 결과 state 업데이트
- `location_profile_id`, `lat`, `lng`, `restaurant_ids`

**에러/예외 처리**
- Geocoding 실패 시 `errors`에 기록하고 종료
- Naver API 오류 시 재시도 정책 또는 `errors` 기록
- 검색 결과 없음: 빈 `restaurant_ids` 반환

**성능/품질 고려사항**
- 동일 입력에 대해 idempotent 동작 보장
- API rate limit 준수
- 중복 upsert 시 불필요한 업데이트 최소화

**테스트 시나리오**
1. 정상 흐름: 주소 입력 → 음식점 수집 → 스냅샷 생성
2. 주소가 잘못된 경우: geocoding 실패 처리
3. 음식점 검색 결과 없음
4. 동일 user/label 재실행 시 중복 데이터 미발생
5. 동일 음식점이 여러 location에서 검색될 때 master 중복 없음
