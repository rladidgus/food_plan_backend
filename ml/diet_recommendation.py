"""
통일된 4가지 체형별 맞춤 식단 추천 모듈
남녀 공통: 마른형, 표준형, 과체중형, 근육형
"""

# 4가지 통일 체형별 식단 전략
UNIFIED_DIET_MAP = {
    "마른형": {
        "goal": "건강한 체중 증가 및 근육량 증진",
        "calorie_adjustment": 1.15,  # 15% 증가
        "activity_multiplier_adjust": 0,  # 기본 활동량 유지
        "macro_ratio": {
            "protein": 0.30,  # 30% 단백질 (근육 합성)
            "carbs": 0.50,    # 50% 탄수화물 (에너지)
            "fat": 0.20       # 20% 지방
        },
        "recommended_foods": [
            "닭가슴살/생선",
            "현미밥/고구마",
            "삶은 계란",
            "아보카도",
            "견과류",
            "단백질 쉐이크",
            "바나나"
        ],
        "avoid_foods": [
            "저칼로리 다이어트 식품",
            "과도한 유산소 운동"
        ],
        "tips": [
            "칼로리를 충분히 섭취하여 체중을 늘리세요.",
            "근력 운동(주 3-4회)으로 근육량을 늘리세요.",
            "하루 5-6끼 소량씩 자주 먹는 것을 권장합니다.",
            "충분한 단백질 섭취가 중요합니다.",
            "숙면이 근육 회복에 중요합니다(7-8시간)."
        ],
        "male_specific": {
            "tips": ["체중 증가를 위해 하루 300-500kcal 추가 섭취를 목표로 하세요."]
        },
        "female_specific": {
            "tips": ["철분과 칼슘 섭취에 신경쓰세요."]
        }
    },
    
    "표준형": {
        "goal": "현재 체형 유지 및 건강한 균형",
        "calorie_adjustment": 1.0,   # 유지
        "activity_multiplier_adjust": 0,
        "macro_ratio": {
            "protein": 0.25,  # 25% 단백질
            "carbs": 0.50,    # 50% 탄수화물
            "fat": 0.25       # 25% 지방
        },
        "recommended_foods": [
            "닭가슴살 샐러드",
            "현미밥과 구운 생선",
            "퀴노아 볼",
            "그릭 요거트",
            "채소 듬뿍",
            "견과류 한 줌",
            "신선한 과일"
        ],
        "avoid_foods": [
            "고지방 패스트푸드",
            "과도한 단 음식",
            "과식"
        ],
        "tips": [
            "현재 체형이 건강하므로 균형잡힌 식사를 유지하세요.",
            "주 3-4회 유산소 및 근력 운동을 권장합니다.",
            "충분한 수분 섭취(1.5-2L/일)를 하세요.",
            "규칙적인 식사 시간을 지키세요.",
            "가공식품보다 자연식품을 선택하세요."
        ],
        "male_specific": {
            "tips": ["근육량 유지를 위해 단백질 섭취를 소홀히 하지 마세요."]
        },
        "female_specific": {
            "tips": ["생리 주기에 따라 철분 보충에 신경쓰세요."]
        }
    },
    
    "과체중형": {
        "goal": "건강한 체중 감량 및 체지방 감소",
        "calorie_adjustment": 0.85,  # 15% 감소
        "activity_multiplier_adjust": 0,
        "macro_ratio": {
            "protein": 0.35,  # 35% 단백질 (포만감 + 근육 보호)
            "carbs": 0.35,    # 35% 탄수화물 (저탄수)
            "fat": 0.30       # 30% 건강한 지방
        },
        "recommended_foods": [
            "삶은 계란",
            "닭가슴살",
            "채소 샐러드 (드레싱 최소)",
            "두부",
            "저지방 우유",
            "브로콜리, 시금치",
            "통곡물 소량"
        ],
        "avoid_foods": [
            "튀김류",
            "탄산음료, 주스",
            "백미, 흰 빵, 면류",
            "과자, 케이크",
            "패스트푸드",
            "술"
        ],
        "tips": [
            "간헐적 단식(16:8)을 고려해보세요.",
            "하루 3끼 규칙적으로, 간식은 최소화하세요.",
            "주 5회 30분 이상 유산소 운동을 권장합니다.",
            "충분한 수면(7~8시간)이 체중 관리에 중요합니다.",
            "식사를 천천히, 꼭꼭 씹어 먹으세요.",
            "물을 충분히 마시세요(하루 2L 이상)."
        ],
        "male_specific": {
            "tips": ["내장지방 감소를 위해 정제 탄수화물을 줄이세요."]
        },
        "female_specific": {
            "tips": ["급격한 체중 감량은 생리 불순을 유발할 수 있으니 천천히 감량하세요."]
        }
    },
    
    "근육형": {
        "goal": "근육량 유지 및 최적 퍼포먼스",
        "calorie_adjustment": 1.10,  # 10% 증가
        "activity_multiplier_adjust": 0.1,  # 활동량 높음
        "macro_ratio": {
            "protein": 0.35,  # 35% 고단백
            "carbs": 0.45,    # 45% 탄수화물
            "fat": 0.20       # 20% 저지방
        },
        "recommended_foods": [
            "스테이크 (저지방 부위)",
            "닭가슴살",
            "연어, 참치",
            "현미밥/고구마",
            "단백질 쉐이크",
            "바나나, 베리류",
            "퀴노아",
            "계란"
        ],
        "avoid_foods": [
            "과도한 지방",
            "알코올",
            "가공육"
        ],
        "tips": [
            "운동 전후 충분한 단백질과 탄수화물을 섭취하세요.",
            "근력 운동 후 30분 내 단백질 보충을 권장합니다.",
            "현재 체형이 매우 좋으니 유지하세요!",
            "충분한 수면(7-8시간)이 근육 회복에 중요합니다.",
            "규칙적인 근력 운동을 지속하세요(주 4-5회)."
        ],
        "male_specific": {
            "tips": ["체중 1kg당 2g 이상의 단백질 섭취를 목표로 하세요."]
        },
        "female_specific": {
            "tips": ["근육량이 많으면 기초대사량이 높으니 충분한 칼로리 섭취가 중요합니다."]
        }
    }
}


def calculate_target_calories(bmr: float, activity_level: str = "moderate") -> float:
    """목표 칼로리 계산
    
    Args:
        bmr: 기초대사량
        activity_level: 활동 수준
    
    Returns:
        하루 목표 칼로리
    """
    activity_multipliers = {
        "sedentary": 1.2,      # 거의 운동 안함
        "light": 1.375,        # 주 1-3회 가벼운 운동
        "moderate": 1.55,      # 주 3-5회 중간 운동
        "active": 1.725,       # 주 6-7회 강한 운동
        "very_active": 1.9     # 매일 강한 운동 + 육체노동
    }
    
    multiplier = activity_multipliers.get(activity_level, 1.55)
    return bmr * multiplier


def recommend_diet_unified(
    primary_type: str,
    gender: str,
    bmr: float,
    activity_level: str = "moderate",
    secondary_tags: list = None
) -> dict:
    """통일된 4가지 체형에 맞는 식단 추천
    
    Args:
        primary_type: 1차 분류 ("마른형", "표준형", "과체중형", "근육형")
        gender: 성별 ("M" or "F")
        bmr: 기초대사량
        activity_level: 활동 수준
        secondary_tags: 2차 태그 (선택)
    
    Returns:
        식단 추천 정보 딕셔너리
    """
    if primary_type not in UNIFIED_DIET_MAP:
        raise ValueError(f"Invalid primary_type: {primary_type}")
    
    diet_info = UNIFIED_DIET_MAP[primary_type]
    
    # 기본 칼로리 계산
    base_calories = calculate_target_calories(bmr, activity_level)
    
    # 체형별 칼로리 조정
    target_calories = base_calories * diet_info["calorie_adjustment"]
    
    # 활동량 조정 (근육형은 활동량 높음)
    if "activity_multiplier_adjust" in diet_info:
        target_calories *= (1 + diet_info["activity_multiplier_adjust"])
    
    # 다량영양소 계산 (g)
    macros = {}
    macros["protein_g"] = round((target_calories * diet_info["macro_ratio"]["protein"]) / 4, 1)
    macros["carbs_g"] = round((target_calories * diet_info["macro_ratio"]["carbs"]) / 4, 1)
    macros["fat_g"] = round((target_calories * diet_info["macro_ratio"]["fat"]) / 9, 1)
    
    # 성별 특화 팁 추가
    tips = diet_info["tips"].copy()
    if gender == "M" and "male_specific" in diet_info:
        tips.extend(diet_info["male_specific"]["tips"])
    elif gender == "F" and "female_specific" in diet_info:
        tips.extend(diet_info["female_specific"]["tips"])
    
    # 2차 태그 기반 추가 조언 (선택)
    if secondary_tags:
        if "비만위험" in secondary_tags:
            tips.append("⚠️ 비만 위험이 있으니 식단 관리를 더욱 철저히 하세요.")
        if "마른비만" in secondary_tags:
            tips.append("⚠️ 마른비만은 근육 부족이 원인입니다. 근력 운동을 시작하세요.")
        if "고내장지방" in secondary_tags:
            tips.append("⚠️ 내장지방이 높습니다. 유산소 운동과 저탄수 식단을 권장합니다.")
    
    return {
        "primary_type": primary_type,
        "goal": diet_info["goal"],
        "target_calories": round(target_calories, 0),
        "macro_ratio": diet_info["macro_ratio"],
        "macros": macros,
        "recommended_foods": diet_info["recommended_foods"],
        "avoid_foods": diet_info["avoid_foods"],
        "tips": tips,
        "gender": gender
    }


def get_meal_plan_example(primary_type: str, target_calories: float) -> dict:
    """하루 식단 예시 제공 (4가지 체형)
    
    Args:
        primary_type: 체형 ("마른형", "표준형", "과체중형", "근육형")
        target_calories: 목표 칼로리
    
    Returns:
        아침/점심/저녁/간식 예시
    """
    breakfast_cal = target_calories * 0.25
    lunch_cal = target_calories * 0.35
    dinner_cal = target_calories * 0.30
    snack_cal = target_calories * 0.10
    
    meal_examples = {
        "마른형": {
            "breakfast": f"스크램블 에그 + 통곡물 빵 + 아보카도 ({breakfast_cal:.0f}kcal)",
            "lunch": f"현미밥 + 닭가슴살 + 고구마 ({lunch_cal:.0f}kcal)",
            "dinner": f"연어 + 퀴노아 + 채소 ({dinner_cal:.0f}kcal)",
            "snack": f"단백질 쉐이크 + 견과류 ({snack_cal:.0f}kcal)"
        },
        "표준형": {
            "breakfast": f"그릭 요거트 + 그래놀라 + 베리류 ({breakfast_cal:.0f}kcal)",
            "lunch": f"현미밥 + 구운 생선 + 채소 샐러드 ({lunch_cal:.0f}kcal)",
            "dinner": f"닭가슴살 샐러드 + 퀴노아 ({dinner_cal:.0f}kcal)",
            "snack": f"견과류 + 과일 ({snack_cal:.0f}kcal)"
        },
        "과체중형": {
            "breakfast": f"삶은 계란 2개 + 토마토 ({breakfast_cal:.0f}kcal)",
            "lunch": f"닭가슴살 샐러드 (드레싱 최소) ({lunch_cal:.0f}kcal)",
            "dinner": f"두부 스테이크 + 채소 볶음 ({dinner_cal:.0f}kcal)",
            "snack": f"저지방 그릭 요거트 ({snack_cal:.0f}kcal)"
        },
        "근육형": {
            "breakfast": f"오트밀 + 단백질 파우더 + 바나나 ({breakfast_cal:.0f}kcal)",
            "lunch": f"현미밥 + 스테이크 + 고구마 ({lunch_cal:.0f}kcal)",
            "dinner": f"닭가슴살 + 퀴노아 + 브로콜리 ({dinner_cal:.0f}kcal)",
            "snack": f"단백질 쉐이크 + 바나나 ({snack_cal:.0f}kcal)"
        }
    }
    
    return meal_examples.get(primary_type, meal_examples["표준형"])
