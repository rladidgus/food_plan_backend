"""Node A: fetch restaurants within radius for a given address.

Stub implementation to be expanded by a dedicated agent.
"""

from __future__ import annotations

from typing import Dict, Any


State = Dict[str, Any]


def node_a_fetch_restaurants(state: State) -> State:
    """A: 주소 기준 500m 음식점 리스트 추출 (추후 에이전트 구현)

    Expected input in state:
    - address_text: str
    - radius_m: int (optional, default 500)

    Expected output in state:
    - restaurant_ids: list[int]
    """
    state.setdefault("restaurant_ids", [])
    return state


__all__ = ["node_a_fetch_restaurants"]
