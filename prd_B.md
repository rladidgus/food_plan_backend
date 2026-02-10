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
