"""Pydantic and TypedDict schemas for agent workflow."""

from __future__ import annotations

from typing import List, Optional, TypedDict
from pydantic import BaseModel, Field


class AgentState(TypedDict, total=False):
    # 입력
    user_number: int
    label: str  # home | work
    address_text: str
    radius_m: int

    # 중간 결과
    location_profile_id: int
    lat: float
    lng: float
    restaurant_ids: List[int]
    menu_item_ids: List[int]
    nutrition_ids: List[int]

    # 실행 로그
    errors: List[str]


class AgentInput(BaseModel):
    user_number: int = Field(..., ge=1)
    label: str = Field(..., pattern="^(home|work)$")
    address_text: str
    radius_m: int = Field(500, ge=50, le=5000)


class AgentOutput(BaseModel):
    location_profile_id: Optional[int] = None
    restaurant_ids: List[int] = []
    menu_item_ids: List[int] = []
    nutrition_ids: List[int] = []
    errors: List[str] = []

