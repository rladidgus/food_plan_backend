from __future__ import annotations

from typing import Optional


ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}


def normalize_activity_level(level: Optional[str]) -> Optional[str]:
    if not level:
        return None
    value = str(level).strip().lower()
    alias = {
        "저활동": "sedentary",
        "가벼운활동": "light",
        "보통": "moderate",
        "중간활동": "moderate",
        "높은활동": "active",
        "선수급": "very_active",
    }
    return alias.get(value, value)


def infer_goal_type(
    stage1: str,
    stage2: str,
    ffmi: Optional[float] = None,
    ffmi_low: Optional[float] = None,
    ffmi_muscular: Optional[float] = None,
) -> str:
    """룰 기반 목표 타입(diet/maintain/bulk) 결정."""
    if stage2 in {"마른비만", "근감소성비만", "비만(지방형)", "근육형비만"}:
        return "diet"
    if stage2 in {"초저체중(위험)", "표준저근육"}:
        return "bulk"

    if ffmi is not None and ffmi_low is not None and ffmi < ffmi_low:
        return "bulk"

    if stage1 == "마름":
        return "bulk"
    if stage1 == "비만":
        return "diet"
    return "maintain"


def estimate_target_calorie(
    goal_type: str,
    bmr_kcal: Optional[float],
    weight_kg: Optional[float],
    activity_level: Optional[str] = None,
) -> Optional[float]:
    """간단한 룰 기반 목표 칼로리 추정."""
    base = None
    if bmr_kcal is not None and bmr_kcal > 0:
        factor = ACTIVITY_FACTORS.get(normalize_activity_level(activity_level), 1.2)
        base = bmr_kcal * factor
    elif weight_kg is not None and weight_kg > 0:
        base = weight_kg * 30.0

    if base is None:
        return None

    if goal_type == "diet":
        target = base * 0.85
    elif goal_type == "bulk":
        target = base * 1.1
    else:
        target = base

    return float(round(target))
