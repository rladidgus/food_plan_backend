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
