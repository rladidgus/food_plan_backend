"""Node B: fetch menus for restaurants."""

from __future__ import annotations

import os
import time
from typing import Dict, Any, List

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.menu_collection import collect_menus_for_restaurants


State = Dict[str, Any]


def node_b_fetch_menus(state: State) -> State:
    """B: 음식점별 메뉴 정보 추출 (추후 에이전트 구현)

    Expected input in state:
    - restaurant_ids: list[int]

    Expected output in state:
    - menu_item_ids: list[int]
    """
    restaurant_ids: List[int] = state.get("restaurant_ids", []) or []
    if not restaurant_ids:
        state.setdefault("menu_item_ids", [])
        return state

    method_priority = os.getenv("MENU_COLLECTION_PRIORITY", "SCRAPING,LLM,API")
    method_priority_list = [item.strip() for item in method_priority.split(",") if item.strip()]
    llm_fallback = os.getenv("MENU_LLM_FALLBACK", "true").lower() == "true"
    naver_validation = os.getenv("MENU_NAVER_VALIDATION", "true").lower() == "true"
    rate_limit_ms = int(os.getenv("MENU_RATE_LIMIT_MS", "800"))
    retry_count = int(os.getenv("MENU_COLLECTION_RETRY", "1"))
    min_items = int(os.getenv("MENU_MIN_ITEMS", "1"))

    db: Session = SessionLocal()
    try:
        collected_ids: List[int] = []
        failures_all: List[dict] = []

        for restaurant_id in restaurant_ids:
            menu_item_ids, failures = collect_menus_for_restaurants(
                db,
                [restaurant_id],
                method_priority_list,
                llm_fallback,
                naver_validation,
                retry_count=retry_count,
                min_items=min_items,
            )
            db.commit()
            collected_ids.extend(menu_item_ids)
            failures_all.extend(failures)

            if rate_limit_ms:
                time.sleep(rate_limit_ms / 1000.0)

        state["menu_item_ids"] = collected_ids
        if failures_all:
            state.setdefault("errors", [])
            for failure in failures_all:
                state["errors"].append(
                    f"restaurant_id={failure.get('restaurant_id')} reason={failure.get('reason')}"
                )
    finally:
        db.close()
    return state


__all__ = ["node_b_fetch_menus"]
