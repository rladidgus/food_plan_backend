"""Node C: fetch nutrition facts for menu items."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models import MenuItem, NutritionFacts, Restaurant
import os

from app.services.nutrition_searcher import infer_nutrition, search_nutrition

State = Dict[str, Any]


def _dedupe_preserve_order(values: Iterable[int]) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _save_nutrition(
    db: Session,
    menu_item: MenuItem,
    payload: Dict[str, Any],
    source_type: str,
    source_ref: Optional[str],
    confidence: Optional[float],
) -> NutritionFacts:
    nf = NutritionFacts(
        menu_item_id=menu_item.menu_id,
        calories_kcal=payload.get("calories_kcal"),
        carbs_g=payload.get("carbs_g"),
        protein_g=payload.get("protein_g"),
        fat_g=payload.get("fat_g"),
        sodium_mg=payload.get("sodium_mg"),
        sugar_g=payload.get("sugar_g"),
        fiber_g=payload.get("fiber_g"),
        source_type=source_type,
        source_ref=source_ref,
        confidence=confidence,
    )
    db.add(nf)
    db.flush()
    return nf


def _collect_nutrition_for_items(
    db: Session,
    menu_item_ids: List[int],
    delay_s: float,
) -> List[int]:
    if not menu_item_ids:
        return []

    unique_ids = _dedupe_preserve_order(menu_item_ids)

    items = (
        db.query(MenuItem)
        .options(joinedload(MenuItem.restaurant), joinedload(MenuItem.nutrition_facts))
        .filter(MenuItem.menu_id.in_(unique_ids))
        .all()
    )
    id_to_item: dict[int, MenuItem] = {m.menu_id: m for m in items}

    nutrition_ids: List[int] = []

    min_conf = float(os.getenv("NUTRITION_MIN_CONFIDENCE", "0.6"))
    retry_count = int(os.getenv("NUTRITION_RETRY", "1"))

    for menu_id in unique_ids:
        menu_item = id_to_item.get(menu_id)
        if not menu_item:
            continue

        if menu_item.nutrition_facts:
            nutrition_ids.extend([nf.nutrition_id for nf in menu_item.nutrition_facts])
            continue

        restaurant: Optional[Restaurant] = getattr(menu_item, "restaurant", None)

        try:
            result = None
            for _ in range(max(1, retry_count)):
                result = search_nutrition(
                    restaurant_name=getattr(restaurant, "name", ""),
                    menu_name=menu_item.name,
                )
                if result and float(result.get("confidence") or 0) >= min_conf:
                    break
                result = None

            if result:
                source_ref = json.dumps({"refs": result.get("refs")}, ensure_ascii=False)
                nf = _save_nutrition(
                    db,
                    menu_item,
                    result,
                    source_type="search",
                    source_ref=source_ref,
                    confidence=result.get("confidence"),
                )
            else:
                inferred = None
                for _ in range(max(1, retry_count)):
                    inferred = infer_nutrition(
                        restaurant_name=getattr(restaurant, "name", ""),
                        category=getattr(restaurant, "category", ""),
                        menu_name=menu_item.name,
                        price=menu_item.price,
                    )
                    if inferred and inferred.get("confidence") is not None:
                        break
                inferred = inferred or {}
                source_ref = json.dumps({"note": "llm_infer"}, ensure_ascii=False)
                nf = _save_nutrition(
                    db,
                    menu_item,
                    inferred,
                    source_type="infer",
                    source_ref=source_ref,
                    confidence=inferred.get("confidence"),
                )
        except Exception as exc:  # noqa: BLE001
            nf = _save_nutrition(
                db,
                menu_item,
                {},
                source_type="infer",
                source_ref=json.dumps({"error": str(exc)}, ensure_ascii=False),
                confidence=0.0,
            )

        nutrition_ids.append(nf.nutrition_id)
        if delay_s > 0:
            time.sleep(delay_s)

    db.commit()
    return nutrition_ids


def node_c_fetch_nutrition(state: State) -> State:
    """C: 메뉴별 영양/칼로리 추출

    Expected input in state:
    - menu_item_ids: list[int]

    Expected output in state:
    - nutrition_ids: list[int]
    """
    menu_item_ids: List[int] = state.get("menu_item_ids", []) or []
    if not menu_item_ids:
        state.setdefault("nutrition_ids", [])
        return state

    delay_s = float(state.get("nutrition_delay_s", 0.2))

    db: Session = SessionLocal()
    try:
        nutrition_ids = _collect_nutrition_for_items(db, menu_item_ids, delay_s)
        state["nutrition_ids"] = nutrition_ids
    finally:
        db.close()
    return state


__all__ = ["node_c_fetch_nutrition"]
