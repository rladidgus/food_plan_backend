"""Pydantic and TypedDict schemas for agent workflow."""

from __future__ import annotations

from typing import List, Optional, TypedDict
from pydantic import BaseModel, Field


class AgentState(TypedDict, total=False):
    # 입력
    user_number: int
    label: str  # home | work
    address_text: str
    lat: float
    lng: float
    radius_m: int

    # 중간 결과
    location_profile_id: int
    restaurant_ids: List[int]
    menu_item_ids: List[int]
    nutrition_ids: List[int]

    # 실행 로그
    errors: List[str]


class AgentInput(BaseModel):
    user_number: int = Field(..., ge=1)
    label: str = Field("current", pattern="^(home|work|current)$")
    address_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_m: int = Field(500, ge=50, le=5000)


class AgentOutput(BaseModel):
    location_profile_id: Optional[int] = None
    restaurant_ids: List[int] = Field(default_factory=list)
    menu_item_ids: List[int] = Field(default_factory=list)
    nutrition_ids: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class MenuCandidate(TypedDict, total=False):
    name: str
    price: Optional[float]
    description: Optional[str]
    source_url: Optional[str]


class NutritionCandidate(TypedDict, total=False):
    calories_kcal: Optional[float]
    carbs_g: Optional[float]
    protein_g: Optional[float]
    fat_g: Optional[float]
    sodium_mg: Optional[float]
    sugar_g: Optional[float]
    fiber_g: Optional[float]
    source_type: str
    source_ref: Optional[str]
    confidence: Optional[float]


class FetchNutritionRequest(BaseModel):
    menu_item_ids: List[int]
    openai_model: Optional[str] = "gpt-4.1-mini"
    delay_s: float = 0.2


class FetchNutritionResponse(BaseModel):
    nutrition_ids: List[int]


class NodeRunRequest(BaseModel):
    address_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_m: Optional[int] = 500


class NodeRunResponse(BaseModel):
    user_number: int
    label: str
    location_profile_id: Optional[int] = None
    restaurant_ids: List[int] = Field(default_factory=list)
    menu_item_ids: List[int] = Field(default_factory=list)
    nutrition_ids: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
