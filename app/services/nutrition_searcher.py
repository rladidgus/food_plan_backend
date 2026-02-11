"""Nutrition search and LLM fallback service."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field


class NutritionSearchResult(BaseModel):
    calories_kcal: float = Field(description="칼로리(kcal)")
    carbs_g: float = Field(description="탄수화물(g)")
    protein_g: float = Field(description="단백질(g)")
    fat_g: float = Field(description="지방(g)")
    sodium_mg: Optional[float] = Field(default=None, description="나트륨(mg)")
    sugar_g: Optional[float] = Field(default=None, description="당(g)")
    fiber_g: Optional[float] = Field(default=None, description="식이섬유(g)")
    source_type: str = Field(description="search 또는 infer")
    source_refs: List[str] = Field(default_factory=list, description="참고 URL")
    confidence: float = Field(ge=0.0, le=1.0, description="신뢰도")
    note: str = Field(default="", description="근거 요약")


def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
    return OpenAI(api_key=api_key)


def _build_prompt(
    restaurant_name: str,
    category: str,
    menu_name: str,
    price: Optional[float],
) -> tuple[str, str]:
    instruction = {
        "restaurant": {"name": restaurant_name, "category": category},
        "menu": {"name": menu_name, "price_won": price},
        "rules": [
            "가능하면 웹서치 근거로 수치를 제시하고 source_type=search로 표시한다.",
            "근거가 불충분하면 1인분 기준으로 보수적으로 추정하고 source_type=infer로 표시한다.",
            "source_refs에는 실제 참고한 URL만 넣는다.",
            "수치는 1인분 기준의 대략적인 값이다.",
            "반드시 지정된 JSON 스키마만 출력한다.",
        ],
    }
    system = (
        "너는 음식 영양정보 정리 도우미다. "
        "항상 한국어로만 답하고, 반드시 지정된 JSON 스키마로만 출력한다."
    )
    user = (
        "다음 메뉴의 영양정보를 정리해줘.\n"
        f"{json.dumps(instruction, ensure_ascii=False)}"
    )
    return system, user


def search_nutrition(restaurant_name: str, menu_name: str) -> Dict[str, Any] | None:
    """웹서치 기반 영양 정보 조회."""
    client = _get_openai_client()
    system, user = _build_prompt(restaurant_name, "", menu_name, None)

    try:
        resp = client.responses.parse(
            model=os.getenv("NUTRITION_SEARCH_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            tools=[{"type": "web_search_preview", "search_context_size": "medium"}],
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=NutritionSearchResult,
        )
        parsed: NutritionSearchResult = resp.output_parsed
    except Exception:
        return None

    if parsed.source_type != "search":
        return None

    return parsed.model_dump()


def infer_nutrition(
    restaurant_name: str,
    category: str,
    menu_name: str,
    price: float | None = None,
) -> Dict[str, Any]:
    """LLM 기반 영양 정보 추론."""
    client = _get_openai_client()
    system, user = _build_prompt(restaurant_name, category, menu_name, price)

    resp = client.responses.parse(
        model=os.getenv("NUTRITION_INFER_MODEL", "gpt-4o-mini"),
        temperature=0.2,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        text_format=NutritionSearchResult,
    )
    parsed: NutritionSearchResult = resp.output_parsed
    if parsed.source_type != "infer":
        parsed.source_type = "infer"
        parsed.source_refs = []
    return parsed.model_dump()


__all__ = ["search_nutrition", "infer_nutrition"]
