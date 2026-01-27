"""
인바디 점수 계산 및 체형 평가 모듈
"""


def calculate_bmi(height_cm: float, weight_kg: float) -> float:
    """BMI 계산
    
    Args:
        height_cm: 키 (cm)
        weight_kg: 체중 (kg)
    
    Returns:
        BMI 값
    """
    height_m = height_cm / 100.0
    return weight_kg / (height_m ** 2)


def evaluate_bmi_category(bmi: float) -> str:
    """BMI 기반 체형 분류
    
    한국 기준:
    - 저체중: < 18.5
    - 정상: 18.5 ~ 23
    - 과체중: 23 ~ 25
    - 비만: >= 25
    """
    if bmi < 18.5:
        return "저체중"
    elif bmi < 23:
        return "정상"
    elif bmi < 25:
        return "과체중"
    else:
        return "비만"


def calculate_standard_weight(height_cm: float, gender: str) -> float:
    """표준 체중 계산
    
    Args:
        height_cm: 키 (cm)
        gender: 성별 ("M" or "F")
    
    Returns:
        표준 체중 (kg)
    """
    if gender == "M":
        # 남성: (키 - 100) × 0.9
        return (height_cm - 100) * 0.9
    else:
        # 여성: (키 - 100) × 0.85
        return (height_cm - 100) * 0.85


def evaluate_body_fat(body_fat_pct: float, gender: str) -> dict:
    """체지방률 평가
    
    Args:
        body_fat_pct: 체지방률 (%)
        gender: 성별 ("M" or "F")
    
    Returns:
        평가 결과 {"level": str, "description": str}
    """
    if gender == "M":
        # 남성 기준
        if body_fat_pct < 10:
            level = "매우 낮음"
            desc = "운동선수 수준의 낮은 체지방률입니다."
        elif body_fat_pct < 15:
            level = "낮음"
            desc = "건강한 체지방률입니다."
        elif body_fat_pct < 20:
            level = "정상"
            desc = "표준 범위의 체지방률입니다."
        elif body_fat_pct < 25:
            level = "약간 높음"
            desc = "체지방 관리가 필요합니다."
        else:
            level = "높음"
            desc = "체지방 감소가 필요합니다."
    else:
        # 여성 기준
        if body_fat_pct < 15:
            level = "매우 낮음"
            desc = "운동선수 수준의 낮은 체지방률입니다."
        elif body_fat_pct < 20:
            level = "낮음"
            desc = "건강한 체지방률입니다."
        elif body_fat_pct < 28:
            level = "정상"
            desc = "표준 범위의 체지방률입니다."
        elif body_fat_pct < 35:
            level = "약간 높음"
            desc = "체지방 관리가 필요합니다."
        else:
            level = "높음"
            desc = "체지방 감소가 필요합니다."
    
    return {"level": level, "description": desc}


def evaluate_visceral_fat(visceral_fat_level: int) -> dict:
    """내장지방 레벨 평가
    
    Args:
        visceral_fat_level: 내장지방 레벨 (1~20 범위)
    
    Returns:
        평가 결과 {"level": str, "description": str, "risk": str}
    """
    if visceral_fat_level < 10:
        level = "정상"
        desc = "건강한 내장지방 수준입니다."
        risk = "낮음"
    elif visceral_fat_level < 15:
        level = "약간 높음"
        desc = "내장지방 관리가 권장됩니다."
        risk = "중간"
    else:
        level = "높음"
        desc = "내장지방 감소가 필요합니다. 대사증후군 위험이 있습니다."
        risk = "높음"
    
    return {"level": level, "description": desc, "risk": risk}


def evaluate_skeletal_muscle(skeletal_muscle_mass: float, weight: float, gender: str) -> dict:
    """골격근량 평가
    
    Args:
        skeletal_muscle_mass: 골격근량 (kg)
        weight: 체중 (kg)
        gender: 성별 ("M" or "F")
    
    Returns:
        평가 결과 {"level": str, "description": str, "percentage": float}
    """
    muscle_percentage = (skeletal_muscle_mass / weight) * 100
    
    if gender == "M":
        # 남성 기준: 골격근량 비율
        if muscle_percentage < 40:
            level = "부족"
            desc = "근육량 증가가 필요합니다."
        elif muscle_percentage < 45:
            level = "정상"
            desc = "적정 수준의 근육량입니다."
        else:
            level = "우수"
            desc = "매우 좋은 근육량입니다."
    else:
        # 여성 기준
        if muscle_percentage < 30:
            level = "부족"
            desc = "근육량 증가가 필요합니다."
        elif muscle_percentage < 35:
            level = "정상"
            desc = "적정 수준의 근육량입니다."
        else:
            level = "우수"
            desc = "매우 좋은 근육량입니다."
    
    return {"level": level, "description": desc, "percentage": round(muscle_percentage, 1)}


def calculate_inbody_score_estimate(
    body_fat_pct: float,
    skeletal_muscle_mass: float,
    weight: float,
    visceral_fat_level: int,
    gender: str
) -> int:
    """인바디 점수 추정 (간이 계산)
    
    실제 인바디 기기의 점수 공식은 비공개이나, 주요 지표를 기반으로 근사값 계산
    
    Args:
        body_fat_pct: 체지방률 (%)
        skeletal_muscle_mass: 골격근량 (kg)
        weight: 체중 (kg)
        visceral_fat_level: 내장지방 레벨
        gender: 성별
    
    Returns:
        추정 인바디 점수 (0~100)
    """
    score = 80  # 기본 점수
    
    # 체지방률 평가 (-20 ~ +10)
    bf_eval = evaluate_body_fat(body_fat_pct, gender)
    if bf_eval["level"] == "정상":
        score += 5
    elif bf_eval["level"] in ["낮음", "약간 높음"]:
        score += 0
    else:
        score -= 10
    
    # 골격근량 평가 (-10 ~ +10)
    sm_eval = evaluate_skeletal_muscle(skeletal_muscle_mass, weight, gender)
    if sm_eval["level"] == "우수":
        score += 10
    elif sm_eval["level"] == "정상":
        score += 5
    else:
        score -= 5
    
    # 내장지방 평가 (-15 ~ +5)
    vf_eval = evaluate_visceral_fat(visceral_fat_level)
    if vf_eval["level"] == "정상":
        score += 5
    elif vf_eval["level"] == "약간 높음":
        score -= 5
    else:
        score -= 15
    
    # 점수 범위 제한
    return max(0, min(100, score))


def get_comprehensive_evaluation(
    height: float,
    weight: float,
    body_fat_pct: float,
    skeletal_muscle_mass: float,
    visceral_fat_level: int,
    bmr: float,
    gender: str,
    age: int = None
) -> dict:
    """종합 인바디 평가
    
    Returns:
        전체 평가 결과 딕셔너리
    """
    bmi = calculate_bmi(height, weight)
    standard_weight = calculate_standard_weight(height, gender)
    
    return {
        "bmi": {
            "value": round(bmi, 1),
            "category": evaluate_bmi_category(bmi)
        },
        "body_fat": evaluate_body_fat(body_fat_pct, gender),
        "visceral_fat": evaluate_visceral_fat(visceral_fat_level),
        "skeletal_muscle": evaluate_skeletal_muscle(skeletal_muscle_mass, weight, gender),
        "weight_status": {
            "current": weight,
            "standard": round(standard_weight, 1),
            "difference": round(weight - standard_weight, 1)
        },
        "estimated_score": calculate_inbody_score_estimate(
            body_fat_pct, skeletal_muscle_mass, weight, visceral_fat_level, gender
        ),
        "bmr": bmr,
        "age": age
    }
