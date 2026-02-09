"""
ChromaDB 벡터 스토어 모듈
- 사용자 식단 기록을 상황 태그 기반으로 벡터화하여 저장/검색
- 식단 생성 시 meal_type별 사용자 선호 음식을 검색하여 GPT 프롬프트에 반영
"""

import os
import logging
from typing import List, Optional, Dict, Any

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

# ── ChromaDB 클라이언트 (persistent 모드) ──────────────────────────
CHROMA_DATA_DIR = os.getenv("CHROMA_DATA_DIR", "./chroma_data")

_client: Optional[chromadb.ClientAPI] = None
_collection: Optional[chromadb.Collection] = None


def _get_embedding_function():
    """OpenAI 임베딩 함수 반환 (text-embedding-3-small)"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
    return embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )


def get_collection() -> chromadb.Collection:
    """싱글톤 패턴으로 ChromaDB 컬렉션 반환"""
    global _client, _collection
    if _collection is not None:
        return _collection

    _client = chromadb.PersistentClient(path=CHROMA_DATA_DIR)
    _collection = _client.get_or_create_collection(
        name="user_meal_records",
        embedding_function=_get_embedding_function(),
        metadata={"hnsw:space": "cosine"},
    )
    logger.info("ChromaDB collection 'user_meal_records' 초기화 완료 (count=%s)", _collection.count())
    return _collection


# ── 상황 태그 생성 ─────────────────────────────────────────────────

def generate_meal_tags(
    food_name: str,
    meal_type: str,
    calories: float,
    protein: float,
    carb: float,
    fat: float,
) -> List[str]:
    """음식의 영양 정보와 이름을 기반으로 상황 태그를 자동 생성"""
    tags = []

    # 식사 시간대 태그
    time_tags = {
        "breakfast": ["아침", "아침식사"],
        "lunch": ["점심", "점심식사"],
        "dinner": ["저녁", "저녁식사"],
        "snack": ["간식"],
    }
    tags.extend(time_tags.get(meal_type, []))

    # 칼로리 구간 태그
    if calories < 300:
        tags.extend(["저칼로리", "가벼운식사", "간단한"])
    elif calories < 500:
        tags.extend(["적당한칼로리", "균형잡힌"])
    else:
        tags.extend(["고칼로리", "든든한", "푸짐한"])

    # 영양 비율 태그
    total = protein + carb + fat
    if total > 0:
        protein_ratio = protein / total
        carb_ratio = carb / total
        fat_ratio = fat / total
        if protein_ratio > 0.35:
            tags.extend(["고단백", "단백질위주"])
        if carb_ratio > 0.6:
            tags.extend(["탄수화물위주"])
        if fat_ratio > 0.4:
            tags.extend(["고지방"])

    # 음식명 기반 상황 태그 (키워드 매칭)
    name = food_name
    quick_keywords = ["삼각김밥", "편의점", "샌드위치", "토스트", "시리얼", "프로틴바",
                       "바나나", "요거트", "요거르트", "그래놀라", "컵밥", "도시락",
                       "주먹밥", "에너지바", "프로틴", "쉐이크"]
    homemade_keywords = ["밥", "찌개", "국", "볶음", "구이", "조림", "나물", "비빔",
                          "김치", "된장", "제육", "불고기", "잡채", "떡볶이"]
    fancy_keywords = ["스테이크", "파스타", "리조또", "오마카세", "코스", "와인",
                       "랍스터", "트러플", "크림", "까르보나라", "뇨끼"]
    salad_keywords = ["샐러드", "포케", "보울", "그린", "닭가슴살"]
    delivery_keywords = ["치킨", "피자", "햄버거", "버거", "족발", "보쌈", "탕수육",
                          "짜장", "짬뽕", "떡볶이", "순대"]

    if any(k in name for k in quick_keywords):
        tags.extend(["간편식", "빠른식사"])
    if any(k in name for k in homemade_keywords):
        tags.extend(["한식", "집밥스타일"])
    if any(k in name for k in fancy_keywords):
        tags.extend(["외식", "특별한식사", "분위기"])
    if any(k in name for k in salad_keywords):
        tags.extend(["건강식", "가벼운"])
    if any(k in name for k in delivery_keywords):
        tags.extend(["배달", "외식"])

    return tags


def _build_document(food_name: str, tags: List[str]) -> str:
    """음식명 + 상황 태그를 임베딩용 document 텍스트로 변환"""
    unique_tags = list(dict.fromkeys(tags))  # 중복 제거, 순서 유지
    return f"{food_name} {' '.join(unique_tags)}"


# ── 식단 기록 → 벡터 저장 ──────────────────────────────────────────

def upsert_meal_record(
    record_id: int,
    user_number: int,
    food_name: str,
    meal_type: str,
    calories: float,
    protein: float,
    carb: float,
    fat: float,
) -> None:
    """식단 기록 1건을 ChromaDB에 저장 (upsert)"""
    collection = get_collection()

    tags = generate_meal_tags(food_name, meal_type, calories, protein, carb, fat)
    document = _build_document(food_name, tags)

    collection.upsert(
        ids=[f"record_{record_id}"],
        documents=[document],
        metadatas=[{
            "user_number": user_number,
            "food_name": food_name,
            "meal_type": meal_type,
            "calories": calories,
            "protein": protein,
            "carb": carb,
            "fat": fat,
        }],
    )


def delete_meal_record(record_id: int) -> None:
    """식단 기록 1건을 ChromaDB에서 삭제"""
    collection = get_collection()
    try:
        collection.delete(ids=[f"record_{record_id}"])
    except Exception:
        logger.warning("ChromaDB 삭제 실패: record_%s (존재하지 않을 수 있음)", record_id)


def delete_meal_records_bulk(record_ids: List[int]) -> None:
    """여러 식단 기록을 한 번에 ChromaDB에서 삭제"""
    if not record_ids:
        return
    collection = get_collection()
    ids = [f"record_{rid}" for rid in record_ids]
    try:
        collection.delete(ids=ids)
    except Exception:
        logger.warning("ChromaDB 벌크 삭제 실패: %s", ids)


# ── 유사 음식 검색 (meal_type별) ──────────────────────────────────

def _build_search_query(meal_type: str, goal_type: str, context: Optional[str] = None) -> str:
    """meal_type + goal_type + 사용자 컨텍스트를 검색 쿼리 텍스트로 변환"""
    time_tags = {
        "breakfast": "아침 아침식사",
        "lunch": "점심 점심식사",
        "dinner": "저녁 저녁식사",
        "snack": "간식",
    }
    goal_tags = {
        "diet": "저칼로리 가벼운식사 다이어트 건강식",
        "maintain": "균형잡힌 적당한칼로리 한식",
        "bulk": "고칼로리 든든한 고단백 푸짐한",
    }

    parts = [
        time_tags.get(meal_type, "식사"),
        goal_tags.get(goal_type, "균형잡힌"),
    ]

    # 사용자가 직접 입력한 상황 컨텍스트가 있으면 추가
    if context and context.strip():
        parts.append(context.strip())

    return " ".join(parts)


def search_user_preferred_meals(
    user_number: int,
    goal_type: str,
    target_calorie: float,
    meal_type: Optional[str] = None,
    context: Optional[str] = None,
    n_results: int = 5,
) -> List[Dict[str, Any]]:
    """
    사용자의 과거 식단 기록에서 상황에 맞는 음식을 검색.
    meal_type별로 호출하여 아침/점심/저녁 각각의 선호를 따로 파악.
    context: 사용자가 직접 입력한 상황 (예: "바빠서 빠르게 먹고 싶어")
    """
    collection = get_collection()

    total = collection.count()
    if total == 0:
        return []

    query_text = _build_search_query(meal_type or "lunch", goal_type, context)

    # 사용자 필터
    where_filter: Dict[str, Any] = {"user_number": user_number}
    if meal_type:
        where_filter = {
            "$and": [
                {"user_number": user_number},
                {"meal_type": meal_type},
            ]
        }

    try:
        results = collection.query(
            query_texts=[query_text],
            n_results=min(n_results, total),
            where=where_filter,
        )
    except Exception as e:
        logger.warning("ChromaDB 검색 실패: %s", e)
        return []

    if not results or not results.get("metadatas") or not results["metadatas"][0]:
        return []

    return results["metadatas"][0]


def search_user_preferred_meals_by_meal(
    user_number: int,
    goal_type: str,
    target_calorie: float,
    meal_contexts: Optional[Dict[str, str]] = None,
    n_results: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    아침/점심/저녁 각각에 대해 벡터 검색을 수행.
    meal_contexts: {"breakfast": "바빠서 간단히", "dinner": "분위기 있는 외식"}
    반환: {"breakfast": [...], "lunch": [...], "dinner": [...]}
    """
    meal_contexts = meal_contexts or {}
    result = {}

    for mt in ("breakfast", "lunch", "dinner"):
        ctx = meal_contexts.get(mt)
        meals = search_user_preferred_meals(
            user_number=user_number,
            goal_type=goal_type,
            target_calorie=target_calorie,
            meal_type=mt,
            context=ctx,
            n_results=n_results,
        )
        result[mt] = meals

    return result


# ── 기존 record 데이터를 ChromaDB로 마이그레이션 ─────────────────

def migrate_existing_records(db_session) -> int:
    """
    PostgreSQL의 record 테이블에 있는 기존 데이터를
    ChromaDB로 일괄 마이그레이션하는 유틸리티 함수.
    반환: 마이그레이션된 레코드 수
    """
    from app.models import Record

    collection = get_collection()
    records = db_session.query(Record).all()

    if not records:
        return 0

    ids = []
    documents = []
    metadatas = []

    for rec in records:
        tags = generate_meal_tags(
            rec.food_name,
            rec.meal_type or "unknown",
            float(rec.food_calories),
            float(rec.food_protein),
            float(rec.food_carb),
            float(rec.food_fat),
        )
        doc = _build_document(rec.food_name, tags)

        ids.append(f"record_{rec.record_id}")
        documents.append(doc)
        metadatas.append({
            "user_number": rec.user_number,
            "food_name": rec.food_name,
            "meal_type": rec.meal_type or "unknown",
            "calories": float(rec.food_calories),
            "protein": float(rec.food_protein),
            "carb": float(rec.food_carb),
            "fat": float(rec.food_fat),
        })

    # 배치 처리
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i:i + batch_size],
            documents=documents[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
        )

    logger.info("ChromaDB 마이그레이션 완료: %d건", len(ids))
    return len(ids)
