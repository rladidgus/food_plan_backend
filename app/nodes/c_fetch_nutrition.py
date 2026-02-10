import json
import time
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import MenuItem, NutritionFact, Restaurant
from app.services.nutrition_searcher import NutritionSearcher


def _dedupe_preserve_order(values: Iterable[int]) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def c_fetch_nutrition(
    db: Session,
    *,
    menu_item_ids: List[int],
    openai_model: str = "gpt-4.1-mini",
    delay_s: float = 0.2,
) -> List[int]:
    """
    Node C: 메뉴별 영양/칼로리 정보 수집 (웹서치 → LLM fallback)
    - 입력이 비어있으면 빈 리스트 반환
    - 이미 nutrition_facts가 존재하면 재사용
    """

    if not menu_item_ids:
        return []

    unique_ids = _dedupe_preserve_order(menu_item_ids)

    items = (
        db.query(MenuItem)
        .options(joinedload(MenuItem.restaurant), joinedload(MenuItem.nutrition_fact))
        .filter(MenuItem.menu_id.in_(unique_ids))
        .all()
    )
    id_to_item: dict[int, MenuItem] = {m.menu_id: m for m in items}

    searcher = NutritionSearcher(model=openai_model)
    nutrition_ids: List[int] = []

    for menu_id in unique_ids:
        menu_item = id_to_item.get(menu_id)
        if not menu_item:
            continue

        if menu_item.nutrition_fact:
            nutrition_ids.append(menu_item.nutrition_fact.nutrition_id)
            continue

        restaurant: Optional[Restaurant] = getattr(menu_item, "restaurant", None)

        try:
            result = searcher.search_or_infer(
                restaurant_name=getattr(restaurant, "name", None),
                restaurant_category=getattr(restaurant, "category", None),
                menu_name=menu_item.name,
                price_won=menu_item.price,
                menu_description=menu_item.description,
                menu_source_url=menu_item.source_url,
            )

            source_ref = json.dumps(
                {"refs": result.source_refs, "note": result.note},
                ensure_ascii=False,
            )

            nf = NutritionFact(
                menu_item_id=menu_item.menu_id,
                calories_kcal=result.calories_kcal,
                carbs_g=result.carbs_g,
                protein_g=result.protein_g,
                fat_g=result.fat_g,
                sodium_mg=result.sodium_mg,
                sugar_g=result.sugar_g,
                fiber_g=result.fiber_g,
                source_type=result.source_type,
                source_ref=source_ref,
                confidence=result.confidence,
            )
        except Exception as e:
            nf = NutritionFact(
                menu_item_id=menu_item.menu_id,
                source_type="infer",
                source_ref=json.dumps({"error": str(e)}, ensure_ascii=False),
                confidence=0.0,
            )

        db.add(nf)
        db.flush()
        nutrition_ids.append(nf.nutrition_id)

        if delay_s > 0:
            time.sleep(delay_s)

    db.commit()
    return nutrition_ids

=======
"""Node C: fetch nutrition facts for menu items.

Stub implementation to be expanded by a dedicated agent.
"""

from __future__ import annotations

from typing import Dict, Any


State = Dict[str, Any]


def node_c_fetch_nutrition(state: State) -> State:
    """C: 메뉴별 영양/칼로리 추출 (추후 에이전트 구현)

    Expected input in state:
    - menu_item_ids: list[int]

    Expected output in state:
    - nutrition_ids: list[int]
    """
    state.setdefault("nutrition_ids", [])
    return state


__all__ = ["node_c_fetch_nutrition"]
