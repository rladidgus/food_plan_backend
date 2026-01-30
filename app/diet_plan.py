from __future__ import annotations

import json
from typing import Optional, Dict, Any

from app.models import UserDietPlan


def generate_diet_plan(goal_type: str, target_calorie: Optional[float]) -> Dict[str, Any]:
    """룰 기반 목표 식단 생성."""
    daily_kcal = float(target_calorie) if target_calorie is not None else 2000.0

    if goal_type == "diet":
        ratios = {"carb": 0.40, "protein": 0.30, "fat": 0.30}
        food_focus = ["채소", "살코기", "통곡물", "저지방 유제품"]
    elif goal_type == "bulk":
        ratios = {"carb": 0.55, "protein": 0.25, "fat": 0.20}
        food_focus = ["탄수화물", "단백질", "견과", "건강한 지방"]
    else:
        ratios = {"carb": 0.50, "protein": 0.25, "fat": 0.25}
        food_focus = ["균형식", "채소", "통곡물", "적당한 지방"]

    macros = {
        "carb_g": round(daily_kcal * ratios["carb"] / 4.0),
        "protein_g": round(daily_kcal * ratios["protein"] / 4.0),
        "fat_g": round(daily_kcal * ratios["fat"] / 9.0),
    }

    meals = {
        "breakfast_kcal": round(daily_kcal * 0.30),
        "lunch_kcal": round(daily_kcal * 0.40),
        "dinner_kcal": round(daily_kcal * 0.30),
    }

    return {
        "goal_type": goal_type,
        "daily_kcal": round(daily_kcal),
        "macros": macros,
        "meals": meals,
        "food_focus": food_focus,
    }


def create_diet_plan_record(
    db,
    user_number: int,
    goal_type: str,
    target_calorie: Optional[float],
) -> UserDietPlan:
    plan = generate_diet_plan(goal_type, target_calorie)
    record = UserDietPlan(
        user_number=user_number,
        goal_type=goal_type,
        target_calorie=target_calorie,
        plan_json=json.dumps(plan, ensure_ascii=False),
    )
    db.add(record)
    return record
