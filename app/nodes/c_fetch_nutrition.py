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
