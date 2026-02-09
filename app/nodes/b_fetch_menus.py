"""Node B: fetch menus for restaurants.

Stub implementation to be expanded by a dedicated agent.
"""

from __future__ import annotations

from typing import Dict, Any


State = Dict[str, Any]


def node_b_fetch_menus(state: State) -> State:
    """B: 음식점별 메뉴 정보 추출 (추후 에이전트 구현)

    Expected input in state:
    - restaurant_ids: list[int]

    Expected output in state:
    - menu_item_ids: list[int]
    """
    state.setdefault("menu_item_ids", [])
    return state


__all__ = ["node_b_fetch_menus"]
