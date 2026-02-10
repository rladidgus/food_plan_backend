import json
import os
from typing import List, Literal, Optional

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

    source_type: Literal["search", "infer"] = Field(
        description="web search 근거 기반이면 search, 그 외는 infer"
    )
    source_refs: List[str] = Field(
        default_factory=list, description="참고한 URL 목록 (가능한 경우)"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="추정 신뢰도 (0~1)")
    note: str = Field(
        default="", description="오차 원인/근거 요약 (양/조리법/소스 등)"
    )


def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
    return OpenAI(api_key=api_key)


class NutritionSearcher:
    def __init__(self, *, model: str = "gpt-4.1-mini"):
        self.model = model
        self.client = _get_openai_client()

        # 나중에 B로 부터 받은 정보를 여기에 저장합니다.
        self._b_node_menu_normalization_hints: dict[str, str] = {}

    def search_or_infer(
        self,
        *,
        restaurant_name: Optional[str],
        restaurant_category: Optional[str],
        menu_name: str,
        price_won: Optional[int] = None,
        menu_description: Optional[str] = None,
        menu_source_url: Optional[str] = None,
        search_context_size: Literal["low", "medium", "high"] = "medium",
    ) -> NutritionSearchResult:
        restaurant_text = restaurant_name or "알 수 없음"
        category_text = restaurant_category or "알 수 없음"

        q_parts = []
        if restaurant_name:
            q_parts.append(restaurant_name)
        q_parts.append(menu_name)
        q_parts.extend(["영양성분", "칼로리"])
        query = " ".join([p for p in q_parts if p])

        instruction = {
            "restaurant": {"name": restaurant_text, "category": category_text},
            "menu": {
                "name": menu_name,
                "price_won": price_won,
                "description": menu_description,
                "source_url": menu_source_url,
            },
            "web_search_query": query,
            "rules": [
                "가능하면 웹서치 근거로 수치를 제시하고 source_type=search로 표시한다.",
                "근거가 불충분하면 일반적인 1인분 기준으로 보수적으로 추정하고 source_type=infer로 표시한다.",
                "source_refs에는 실제 참고한 URL만 넣는다. 확실한 URL이 없으면 빈 리스트로 둔다.",
                "수치는 1인분 기준의 대략적인 값이며, 모르면 null 대신 합리적 추정값을 제시한다.",
                "반드시 지정된 JSON 스키마만 출력한다.",
            ],
        }

        system = (
            "너는 음식 영양정보 정리 도우미다. "
            "항상 한국어로만 답하고, 반드시 지정된 JSON 스키마로만 출력한다."
        )

        user = (
            "다음 메뉴의 영양정보를 추정/정리해줘.\n"
            "우선 웹서치를 활용해 근거를 찾고, 없으면 추론해.\n"
            f"{json.dumps(instruction, ensure_ascii=False)}"
        )

        try:
            resp = self.client.responses.parse(
                model=self.model,
                temperature=0.2,
                tools=[
                    {
                        "type": "web_search_preview",
                        "search_context_size": search_context_size,
                    }
                ],
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text_format=NutritionSearchResult,
            )
            return resp.output_parsed
        except Exception:
            # 모델/계정 정책 등으로 web_search tool 사용이 불가한 경우 inference-only로 fallback
            resp = self.client.responses.parse(
                model=self.model,
                temperature=0.2,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text_format=NutritionSearchResult,
            )
            inferred: NutritionSearchResult = resp.output_parsed
            if inferred.source_type == "search":
                inferred.source_type = "infer"
                inferred.source_refs = []
            return inferred

"""Nutrition search/LLM fallback service (stub)."""

from __future__ import annotations

from typing import Dict, Any


def search_nutrition(restaurant_name: str, menu_name: str) -> Dict[str, Any] | None:
    """웹서치 기반 영양 정보 조회 (stub)."""
    return None


def infer_nutrition(restaurant_name: str, category: str, menu_name: str, price: float | None = None) -> Dict[str, Any]:
    """LLM 기반 영양 정보 추론 (stub)."""
    return {
        "calories_kcal": None,
        "carbs_g": None,
        "protein_g": None,
        "fat_g": None,
        "sodium_mg": None,
        "sugar_g": None,
        "fiber_g": None,
        "confidence": None,
    }


__all__ = ["search_nutrition", "infer_nutrition"]
