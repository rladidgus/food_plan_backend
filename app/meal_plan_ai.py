import os
import json
import logging
from typing import List, Optional, Dict, Any

from pydantic import BaseModel
from openai import OpenAI

logger = logging.getLogger(__name__)


class MealPlanItem(BaseModel):
    name: str
    description: Optional[str] = None
    calories_kcal: int
    carbs_g: float
    protein_g: float
    fat_g: float
    image_url: Optional[str] = None


class DayMealPlan(BaseModel):
    day_label: str
    date: Optional[str] = None
    breakfast: MealPlanItem
    lunch: MealPlanItem
    dinner: MealPlanItem
    total_calories_kcal: int
    total_carbs_g: float
    total_protein_g: float
    total_fat_g: float


class OneDayMealPlan(BaseModel):
    goal_type: str
    target_calorie: Optional[int] = None
    body_type_stage1: Optional[str] = None
    body_type_stage2: Optional[str] = None
    notes: List[str]
    days: List[DayMealPlan]


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def _build_preference_text(preferred_meals: List[Dict[str, Any]]) -> str:
    """벡터 검색 결과를 GPT 프롬프트용 텍스트로 변환"""
    if not preferred_meals:
        return ""

    # 중복 음식명 제거 (순서 유지)
    seen = set()
    unique_foods = []
    for m in preferred_meals:
        name = m.get("food_name", "")
        if name and name not in seen:
            seen.add(name)
            unique_foods.append(name)

    if not unique_foods:
        return ""

    return (
        "\n\n## 사용자 식단 이력 (선호 음식)\n"
        f"이 사용자가 과거에 자주 먹은 음식: {', '.join(unique_foods)}\n"
        "- 위 음식들을 참고하여 사용자 취향에 맞는 식단을 구성해라.\n"
        "- 동일한 음식을 그대로 반복하지 말고, 비슷한 계열의 다양한 음식을 추천해라.\n"
        "- 사용자가 좋아하는 맛/재료/조리 스타일을 반영해라.\n"
    )


def generate_one_day_plan(
    prompt: dict,
    preferred_meals: Optional[List[Dict[str, Any]]] = None,
) -> OneDayMealPlan:
    """
    1일 식단 생성.
    preferred_meals: 벡터 검색으로 가져온 사용자 선호 음식 목록 (없으면 무시)
    """
    preference_text = _build_preference_text(preferred_meals or [])

    # 목표 칼로리 범위 및 끼니별 권장 칼로리 계산
    target = prompt.get("target_calorie", 0) or 2000
    min_cal = int(target * 0.95)
    max_cal = int(target * 1.05)
    
    # 3끼 배분 예시 (3:4:3)
    meal_ratio = [0.3, 0.4, 0.3]
    meal_names = ["Breakfast", "Lunch", "Dinner"]
    distribution_msg = []
    for name, ratio in zip(meal_names, meal_ratio):
        cal = int(target * ratio)
        distribution_msg.append(f"- {name}: approx. {cal} kcal")
    
    distribution_text = "\n".join(distribution_msg)

    user_content = (
        "다음 정보를 바탕으로 1일치 식단을 추천해줘. "
        "지정된 JSON 스키마만 출력해.\n"
        f"{json.dumps(prompt, ensure_ascii=False)}\n"
        f"{preference_text}\n"
        "\nIMPORTANT RULES (CRITICAL):\n"
        f"1. Target Calorie: {target} kcal\n"
        f"2. Strict Total Calorie Range: {min_cal} ~ {max_cal} kcal\n"
        f"3. Suggested Meal Distribution:\n{distribution_text}\n"
        "4. Sum of (breakfast + lunch + dinner) calories MUST strictly fall within the range above.\n"
        "5. Do NOT output a lower value like 2000 kcal if the target is higher.\n"
    )

    resp = openai_client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 한국어로만 답하는 식단 코치다. "
                    "반드시 지정된 JSON 스키마만 출력한다."
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        response_format=OneDayMealPlan,
    )

    plan: OneDayMealPlan = resp.choices[0].message.parsed
    if not plan or len(plan.days) != 1:
        raise RuntimeError("식단 생성 결과가 올바르지 않습니다.")

    return plan