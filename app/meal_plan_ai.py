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


def _build_preference_text(preferred_foods: List[str]) -> str:
    """사용자가 직접 입력한 선호 음식을 GPT 프롬프트용 텍스트로 변환"""
    if not preferred_foods:
        return ""

    # 중복 제거 및 리스트 정리
    unique_foods = list(dict.fromkeys([f.strip() for f in preferred_foods if f.strip()]))
    
    if not unique_foods:
        return ""

    return (
        "\n\n## 사용자 선호 음식 및 요청 사항\n"
        f"사용자가 다음과 같은 음식을 식단에 포함시키길 원함: {', '.join(unique_foods)}\n"
        "- 위 음식들을 최대한 활용하여 식단을 구성해라.\n"
        "- 다만, 목표 칼로리와 영양 밸런스를 해치지 않는 선에서 조리법이나 양을 조절해서 포함시켜라.\n"
        "- (예: '치킨'을 원하면 '튀긴 치킨' 대신 '오븐 구이 치킨'이나 '닭가슴살 샐러드' 등으로 건강하게 변형 가능)\n"
    )


def generate_one_day_plan(
    prompt: dict,
    preferred_foods: Optional[List[str]] = None,
) -> OneDayMealPlan:
    """
    1일 식단 생성.
    preferred_foods: 사용자가 직접 입력한 선호 음식 목록 (문자열 리스트)
    """
    preference_text = _build_preference_text(preferred_foods or [])

    user_content = (
        "다음 정보를 바탕으로 1일치 식단을 추천해줘. "
        "지정된 JSON 스키마만 출력해.\n"
        f"{json.dumps(prompt, ensure_ascii=False)}"
        f"{preference_text}"
    )

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
                "content": user_content,
            },
        ],
        text_format=OneDayMealPlan,
    )

    plan: OneDayMealPlan = resp.output_parsed
    if not plan or len(plan.days) != 1:
        raise RuntimeError("식단 생성 결과가 올바르지 않습니다.")

    return plan
