"""Food master vector store utilities (CSV -> ChromaDB -> goal-based search/rerank)."""

from __future__ import annotations

import csv
import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

CHROMA_DATA_DIR = os.getenv("CHROMA_DATA_DIR", "./chroma_data")
FOOD_MASTER_COLLECTION = "food_master"

_client: Optional[chromadb.ClientAPI] = None
_food_collection: Optional[chromadb.Collection] = None


def _get_embedding_function():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
    return embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DATA_DIR)
    return _client


def get_food_master_collection() -> chromadb.Collection:
    global _food_collection
    if _food_collection is not None:
        return _food_collection

    client = _get_client()
    _food_collection = client.get_or_create_collection(
        name=FOOD_MASTER_COLLECTION,
        embedding_function=_get_embedding_function(),
        metadata={"hnsw:space": "cosine"},
    )
    return _food_collection


def _to_float(value: Any) -> Optional[float]:
    raw = str(value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _clean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in row.items():
        clean_key = str(key or "").replace("\ufeff", "").strip()
        cleaned[clean_key] = value
    return cleaned


def _goal_hint(goal_type: str) -> str:
    if goal_type == "diet":
        return "저칼로리 고단백 저지방 가벼운식사 다이어트"
    if goal_type == "bulk":
        return "고칼로리 고단백 고탄수 든든한식사 벌크업"
    return "균형잡힌 적당한칼로리 일반식 유지"


def _build_food_document(food_name: str, category: str, cuisine: str, goal_tags: List[str]) -> str:
    tags = " ".join(goal_tags)
    return f"{food_name} {category} {cuisine} {tags}".strip()


def _goal_tags_from_nutrition(calories: Optional[float], protein: Optional[float], carb: Optional[float], fat: Optional[float]) -> List[str]:
    tags: List[str] = []
    if calories is not None:
        if calories <= 300:
            tags.extend(["저칼로리", "가벼운식사"])
        elif calories >= 600:
            tags.extend(["고칼로리", "든든한"])
        else:
            tags.append("적당한칼로리")
    if protein is not None and protein >= 20:
        tags.extend(["고단백", "단백질"])
    if carb is not None and carb >= 60:
        tags.extend(["고탄수화물", "탄수화물"])
    if fat is not None and fat >= 20:
        tags.append("고지방")
    return list(dict.fromkeys(tags))


def _food_id(food_name: str, category: str, cuisine: str, serving: str) -> str:
    seed = f"{food_name}|{category}|{cuisine}|{serving}".encode("utf-8")
    return f"food_{hashlib.md5(seed).hexdigest()}"


def index_food_master_csv(csv_path: str, *, reset: bool = False, batch_size: int = 300) -> int:
    """Index CSV rows into food_master collection. Returns indexed row count."""
    client = _get_client()
    global _food_collection

    if reset:
        try:
            client.delete_collection(name=FOOD_MASTER_COLLECTION)
        except Exception:
            pass
        _food_collection = None

    collection = get_food_master_collection()
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {csv_path}")

    ids: List[str] = []
    docs: List[str] = []
    metas: List[Dict[str, Any]] = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            row = _clean_row(raw_row)
            food_name = str(row.get("식품명") or "").strip()
            if not food_name:
                continue

            category = str(row.get("식품대분류명") or "").strip()
            cuisine = str(row.get("식종") or "").strip()
            serving = str(row.get("식품중량") or "").strip()

            calories = _to_float(row.get("에너지(kcal)"))
            protein = _to_float(row.get("단백질(g)"))
            fat = _to_float(row.get("지방(g)"))
            carb = _to_float(row.get("탄수화물(g)"))
            sugar = _to_float(row.get("당류(g)"))
            sodium = _to_float(row.get("나트륨(mg)"))

            tags = _goal_tags_from_nutrition(calories, protein, carb, fat)
            doc = _build_food_document(food_name, category, cuisine, tags)

            ids.append(_food_id(food_name, category, cuisine, serving))
            docs.append(doc)
            metas.append(
                {
                    "food_name": food_name,
                    "category": category or "unknown",
                    "cuisine": cuisine or "unknown",
                    "serving": serving or "unknown",
                    "calories": float(calories) if calories is not None else -1.0,
                    "protein": float(protein) if protein is not None else -1.0,
                    "fat": float(fat) if fat is not None else -1.0,
                    "carb": float(carb) if carb is not None else -1.0,
                    "sugar": float(sugar) if sugar is not None else -1.0,
                    "sodium": float(sodium) if sodium is not None else -1.0,
                }
            )

    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i : i + batch_size],
            documents=docs[i : i + batch_size],
            metadatas=metas[i : i + batch_size],
        )

    logger.info("food_master 인덱싱 완료: %s rows", len(ids))
    return len(ids)


def _goal_nutrition_score(goal_type: str, item: Dict[str, Any]) -> float:
    cal = float(item.get("calories", -1.0))
    protein = float(item.get("protein", -1.0))
    carb = float(item.get("carb", -1.0))
    fat = float(item.get("fat", -1.0))
    sugar = float(item.get("sugar", -1.0))
    sodium = float(item.get("sodium", -1.0))

    if cal < 0:
        return -1000.0
    if protein < 0:
        protein = 0.0
    if carb < 0:
        carb = 0.0
    if fat < 0:
        fat = 0.0
    if sugar < 0:
        sugar = 0.0
    if sodium < 0:
        sodium = 0.0

    if goal_type == "diet":
        return (-cal * 0.8) + (protein * 7.0) - (fat * 1.2) - (sugar * 0.6)
    if goal_type == "bulk":
        return (cal * 0.7) + (protein * 6.5) + (carb * 0.8) - (sodium * 0.01)
    return (-abs(cal - 550.0) * 0.6) + (protein * 5.5) - (sodium * 0.006)


def search_food_master_by_goal(
    goal_type: str,
    query_text: str,
    *,
    n_results: int = 30,
) -> List[Dict[str, Any]]:
    """Vector search + goal-based rerank from food_master."""
    collection = get_food_master_collection()
    total = collection.count()
    if total == 0:
        return []

    if goal_type not in {"diet", "maintain", "bulk"}:
        goal_type = "maintain"

    full_query = f"{query_text} {_goal_hint(goal_type)}".strip()
    result = collection.query(
        query_texts=[full_query],
        n_results=min(max(1, n_results), total),
    )

    metas = (result or {}).get("metadatas") or []
    dists = (result or {}).get("distances") or []
    if not metas or not metas[0]:
        return []

    rows: List[Dict[str, Any]] = []
    dist_row = dists[0] if dists and dists[0] else []
    for idx, meta in enumerate(metas[0]):
        if not isinstance(meta, dict):
            continue
        distance = float(dist_row[idx]) if idx < len(dist_row) else 1.0
        similarity_bonus = (1.0 - distance) * 40.0
        nutrition_score = _goal_nutrition_score(goal_type, meta)
        rows.append(
            {
                **meta,
                "vector_distance": distance,
                "goal_score": nutrition_score,
                "final_score": nutrition_score + similarity_bonus,
            }
        )

    rows.sort(key=lambda x: float(x.get("final_score", -1e9)), reverse=True)
    return rows


def search_goal_foods(goal_type: str, query_text: str, *, n_results: int = 10) -> List[Dict[str, Any]]:
    ranked = search_food_master_by_goal(goal_type=goal_type, query_text=query_text, n_results=max(20, n_results * 3))
    return ranked[:n_results]


def ensure_food_master_indexed(csv_path: str, *, min_count: int = 100) -> int:
    """Index CSV once when collection is empty or too small."""
    collection = get_food_master_collection()
    count = collection.count()
    if count >= min_count:
        return count
    return index_food_master_csv(csv_path, reset=False)

