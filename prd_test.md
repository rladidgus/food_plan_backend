# [PRD] 스냅샷 기반 식단 데이터 수집 Agent

## 1. 개요 및 핵심 원칙
사용자의 **집/회사 주소**를 기반으로 주변 음식점 정보를 **스냅샷(Snapshot)** 형태로 수집하여 개인화된 식단 추천 DB를 구축합니다.

### 1.1 핵심 수집 전략
* **스냅샷 관리**: 동일 음식점이 여러 위치에서 발견되어도 마스터 정보는 하나만 유지하고, `restaurant_snapshots` 테이블을 통해 관계와 거리를 개별 관리합니다.
* **하이브리드 정보 수집**: 위치는 **지도 API(Kakao)**로, 메뉴와 영양 정보는 **Tavily API + LLM** 조합으로 수집하여 크롤링의 한계를 극복합니다.
* **데이터 분리**: 사용자별, 위치별(home/work)로 데이터를 독립적으로 조회할 수 있도록 설계합니다.

---

## 2. 시스템 아키텍처

### 2.1 LangGraph 파이프라인
모든 수집 과정은 **LangGraph**를 통해 상태(State)를 공유하며 제어됩니다.



1. **A_fetch_restaurants**: 지도 API 기반 식당 목록 수집 및 스냅샷 생성
2. **B_fetch_menus**: Tavily 검색 및 LLM 파싱을 통한 메뉴 리스트 구축
3. **C_fetch_nutrition**: 실시간 검색(Search-on-Demand) 기반 영양 성분 확충

---

## 3. 노드별 상세 스펙

### 3.1 Node A: `A_fetch_restaurants`
* **목적**: 주소지 반경 500m 내 식당 마스터 등록 및 위치 스냅샷 생성
* **주요 처리**:
    * 주소 → 좌표 변환 및 지도 API 호출
    * `restaurants` 테이블 **Upsert** (중복 방지)
    * `restaurant_snapshots` 테이블에 `location_profile_id`와의 관계 및 거리 저장

### 3.2 Node B: `B_fetch_menus`
* **목적**: 식당별 메뉴 리스트 구축
* **주요 처리**:
    * Tavily API를 활용해 웹상의 메뉴판 텍스트 정보 수집
    * LLM을 통해 비정형 텍스트를 `menu_items` 규격에 맞는 JSON으로 변환

### 3.3 Node C: `C_fetch_nutrition`
* **목적**: 메뉴별 영양 성분 데이터 보강
* **주요 처리**:
    * **실시간 검색(Search-on-Demand)**: `"{식당명} {메뉴명} 영양성분"` 검색
    * **LLM 파싱 및 추론**: 검색 결과가 있으면 `search`, 없으면 카테고리 기반 `infer`로 영양소 산출
    * **단위 보정**: 에너지 단위가 kJ일 경우 $1 \text{ kcal} = 4.184 \text{ kJ}$ 수식을 적용하여 변환

---

## 4. DB 스키마 정의 (PostgreSQL)

| 테이블명 | 설명 | 주요 제약사항 |
| :--- | :--- | :--- |
| **`location_profiles`** | 사용자별 주소(집/회사) 프로필 | `UNIQUE(user_number, label)` |
| **`restaurants`** | 음식점 마스터 정보 | `UNIQUE(source, source_place_id)` |
| **`restaurant_snapshots`** | 특정 위치와 음식점의 관계 기록 | `UNIQUE(location_profile_id, restaurant_id)` |
| **`menu_items`** | 음식점별 메뉴 리스트 | `UNIQUE(restaurant_id, name)` |
| **`nutrition_facts`** | 메뉴별 영양 정보 및 신뢰도 | `INDEX(menu_item_id)` |

---

## 5. 기술 스택

* **프레임워크**: LangGraph (에이전트 제어)
* **API**: Kakao Maps (위치), Tavily (AI 검색), OpenAI (데이터 파싱)
* **데이터베이스**: PostgreSQL (SQLAlchemy ORM)
* **환경**: Docker / WSL (Ubuntu-20.04)

---

## 6. 향후 계획 (Out of Scope)
* 수집된 DB를 활용한 **추천 Agent** 개발 (아침/점심/저녁 식단 추천)
* 사용자 식단 이미지(Vision) 분석 결과와 DB 데이터의 교차 검증