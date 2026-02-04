import os
import json
from typing import List, Optional

from pydantic import BaseModel
from openai import OpenAI


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


def generate_one_day_plan(prompt: dict) -> OneDayMealPlan:
    resp = openai_client.responses.parse(
        model="gpt-4.1-mini",
        temperature=0.2,
        input=[
            {
                "role": "system",
                "content": (
                    "너는 한국어로만 답하는 식단 코치다. "
                    "반드시 지정된 JSON 스키마만 출력한다."
                ),
            },
            {
                "role": "user",
                "content": (
                    "다음 정보를 바탕으로 1일치 식단을 추천해줘. "
                    "지정된 JSON 스키마만 출력해.\n"
                    f"{json.dumps(prompt, ensure_ascii=False)}"
                ),
            },
        ],
        text_format=OneDayMealPlan,
    )

    plan: OneDayMealPlan = resp.output_parsed
    if not plan or len(plan.days) != 1:
        raise RuntimeError("식단 생성 결과가 올바르지 않습니다.")

    return plan
