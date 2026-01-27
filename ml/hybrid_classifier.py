"""
하이브리드 인바디 체형 분류 시스템
규칙 기반 + ML 통합

1차 분류: 4가지 체형 (마른형, 표준형, 과체중형, 근육형)
2차 태그: 세부 특성 (복수 가능)
"""
import pandas as pd
import numpy as np
from joblib import load
from pathlib import Path
from typing import List, Tuple

from ml.predict_cluster import build_features, coerce_numeric


# ============= 1차 분류: 4가지 체형 =============
PRIMARY_TYPES = {
    "마른형": "SLIM",
    "표준형": "STANDARD", 
    "과체중형": "OVERWEIGHT",
    "근육형": "MUSCULAR"
}

# ============= 2차 태그: 세부 특성 =============
SECONDARY_TAGS = {
    "건강": "HEALTHY",
    "근육질": "MUSCULAR_BUILD",
    "근육부족": "LOW_MUSCLE",
    "비만위험": "OBESITY_RISK",
    "저체지방": "LOW_BODY_FAT",
    "마른비만": "SKINNY_FAT",
    "대형체격": "LARGE_FRAME",
    "고내장지방": "HIGH_VISCERAL_FAT"
}

# ============= K=4 군집 매핑 =============
MALE_CLUSTER_MAP = {
    0: "과체중형",  # 체지방·내장지방 높음
    1: "마른형",    # 체중/근육 낮고 체지방 낮음
    2: "근육형",    # 골격근량 높고 체지방 낮음
    3: "표준형"     # 체지방 중간, 균형형
}

FEMALE_CLUSTER_MAP = {
    0: "표준형",
    1: "마른형",
    2: "표준형",  # 체지방 높은 편(마른비만 태그 가능)
    3: "과체중형"
}


def calculate_bmi(height_cm: float, weight_kg: float) -> float:
    """BMI 계산"""
    height_m = height_cm / 100.0
    return weight_kg / (height_m ** 2)


def calculate_standard_weight(height_cm: float, gender: str) -> float:
    """표준 체중 계산"""
    if gender == "M":
        return (height_cm - 100) * 0.9
    else:
        return (height_cm - 100) * 0.85


def calculate_muscle_ratio(skeletal_muscle_kg: float, weight_kg: float) -> float:
    """골격근 비율 계산"""
    return (skeletal_muscle_kg / weight_kg) * 100


def classify_primary_type(
    gender: str,
    height: float,
    weight: float,
    body_fat_pct: float,
    skeletal_muscle_mass: float,
    bmi: float = None
) -> str:
    """
    규칙 기반 1차 체형 분류
    
    Returns:
        "마른형", "표준형", "과체중형", "근육형" 중 하나
    """
    if bmi is None:
        bmi = calculate_bmi(height, weight)
    
    standard_weight = calculate_standard_weight(height, gender)
    muscle_ratio = calculate_muscle_ratio(skeletal_muscle_mass, weight)
    
    # 규칙 1: 명확한 마른형
    if bmi < 18.5:
        return "마른형"
    
    if gender == "M":
        # 남성 기준
        if weight < standard_weight * 0.9 and muscle_ratio < 40:
            return "마른형"
    else:
        # 여성 기준
        if weight < standard_weight * 0.9 and muscle_ratio < 30:
            return "마른형"
    
    # 규칙 2: 명확한 근육형
    if gender == "M":
        if body_fat_pct < 15 and muscle_ratio > 45:
            return "근육형"
    else:
        if body_fat_pct < 20 and muscle_ratio > 35:
            return "근육형"
    
    # 규칙 3: 명확한 과체중형
    if bmi >= 25:
        if gender == "M":
            if body_fat_pct > 25:
                return "과체중형"
        else:
            if body_fat_pct > 33:
                return "과체중형"
    
    # 규칙 4: 애매한 케이스 → ML 모델 사용 필요
    return None  # ML 모델로 판단


def generate_secondary_tags(
    gender: str,
    height: float,
    weight: float,
    body_fat_pct: float,
    skeletal_muscle_mass: float,
    visceral_fat_level: int,
    bmi: float = None,
    primary_type: str = None
) -> List[str]:
    """
    2차 태그 생성 (복수 가능)
    
    Returns:
        태그 리스트 (예: ["건강", "근육질"])
    """
    if bmi is None:
        bmi = calculate_bmi(height, weight)
    
    muscle_ratio = calculate_muscle_ratio(skeletal_muscle_mass, weight)
    tags = []
    
    # 태그 1: 저체지방
    if gender == "M":
        if body_fat_pct < 15:
            tags.append("저체지방")
    else:
        if body_fat_pct < 20:
            tags.append("저체지방")
    
    # 태그 2: 근육질
    if gender == "M":
        if muscle_ratio > 45 and body_fat_pct < 20:
            tags.append("근육질")
    else:
        if muscle_ratio > 35 and body_fat_pct < 28:
            tags.append("근육질")
    
    # 태그 3: 근육부족
    if gender == "M":
        if muscle_ratio < 40:
            tags.append("근육부족")
    else:
        if muscle_ratio < 30:
            tags.append("근육부족")
    
    # 태그 4: 비만위험
    if visceral_fat_level >= 10:
        tags.append("비만위험")
        tags.append("고내장지방")
    elif gender == "M":
        if body_fat_pct > 30:
            tags.append("비만위험")
    else:
        if body_fat_pct > 38:
            tags.append("비만위험")
    
    # 태그 5: 마른비만 (정상 체중이지만 체지방 높음)
    if bmi < 25:
        if gender == "M":
            if body_fat_pct > 23:
                tags.append("마른비만")
        else:
            if body_fat_pct > 30:
                tags.append("마른비만")
    
    # 태그 6: 대형체격
    if gender == "M":
        if height > 180 and weight > 80:
            tags.append("대형체격")
    else:
        if height > 170 and weight > 65:
            tags.append("대형체격")
    
    # 태그 7: 건강 (다른 부정적 태그 없고 정상 범위)
    negative_tags = {"비만위험", "고내장지방", "근육부족", "마른비만"}
    if not any(tag in negative_tags for tag in tags):
        if visceral_fat_level < 10:
            if gender == "M":
                if 18.5 <= bmi < 25 and 15 <= body_fat_pct <= 23:
                    tags.append("건강")
            else:
                if 18.5 <= bmi < 25 and 20 <= body_fat_pct <= 28:
                    tags.append("건강")
    
    return tags


class HybridBodyTypeClassifier:
    """ML 전용 체형 분류기 (성별 모델 기반)"""
    
    def __init__(
        self,
        male_model_path: str = "models/inbody_male_k4_model.joblib",
        female_model_path: str = "models/inbody_female_k4_model.joblib"
    ):
        """ML 모델 로드 (ML 전용)"""
        if not Path(male_model_path).exists():
            raise FileNotFoundError(f"Male model file not found: {male_model_path}")
        if not Path(female_model_path).exists():
            raise FileNotFoundError(f"Female model file not found: {female_model_path}")

        self.male_model = load(male_model_path)
        self.female_model = load(female_model_path)

    def _prepare_features(self, inbody_data: dict) -> pd.DataFrame:
        """모델 입력용 피처 생성 (train_gender_specific.py와 동일)"""
        df = pd.DataFrame([inbody_data])

        optional_columns = {
            "체지방량": np.nan,
            "복부지방률": np.nan,
            "인바디점수": np.nan,
            "측정일자": pd.NaT,
            "측정시간": np.nan,
            "사용자 출생년도": np.nan,
            "행정동명": np.nan
        }

        for col, default_val in optional_columns.items():
            if col not in df.columns:
                df[col] = default_val

        df = build_features(df)

        drop_cols = [c for c in ["사용자 고유번호", "측정일자", "성별"] if c in df.columns]
        X = df.drop(columns=drop_cols, errors="ignore")

        cat_cols = [c for c in ["행정동명"] if c in X.columns]
        num_cols = [c for c in X.columns if c not in cat_cols]
        X = coerce_numeric(X, num_cols)
        return X
    
    def _ml_predict_cluster(self, gender: str, inbody_data: dict) -> int:
        """ML 모델로 군집 예측"""
        model = self.male_model if gender == "M" else self.female_model
        X = self._prepare_features(inbody_data)
        cluster = model.predict(X)[0]
        return int(cluster)
    
    def classify(
        self,
        gender: str,
        height: float,
        weight: float,
        body_fat_pct: float,
        skeletal_muscle_mass: float,
        bmr: float,
        visceral_fat_level: int,
        age: int = None,
        birth_year: int = None
    ) -> dict:
        """
        ML 전용 분류 실행
        
        Returns:
            {
                "primary_type": "표준형",
                "secondary_tags": ["건강"],
                "display_name": "표준형 (건강)",
                "classification_method": "ml"
            }
        """
        bmi = calculate_bmi(height, weight)

        cluster_id = self._ml_predict_cluster(gender, {
            "키": height,
            "체중": weight,
            "체지방률": body_fat_pct,
            "골격근량": skeletal_muscle_mass,
            "기초대사량": bmr,
            "내장지방레벨": visceral_fat_level,
            "사용자 출생년도": birth_year if birth_year else np.nan
        })

        cluster_map = MALE_CLUSTER_MAP if gender == "M" else FEMALE_CLUSTER_MAP
        primary_type = cluster_map.get(cluster_id, "표준형")
        classification_method = "ml"
        
        # 2단계: 2차 태그 생성
        secondary_tags = generate_secondary_tags(
            gender, height, weight, body_fat_pct, skeletal_muscle_mass,
            visceral_fat_level, bmi, primary_type
        )
        
        # 표시명 생성
        if secondary_tags:
            # 가장 중요한 태그만 표시 (우선순위)
            priority = ["비만위험", "마른비만", "근육질", "저체지방", "건강", "대형체격", "근육부족"]
            main_tag = next((tag for tag in priority if tag in secondary_tags), secondary_tags[0])
            display_name = f"{primary_type} ({main_tag})"
        else:
            display_name = primary_type
        
        return {
            "primary_type": primary_type,
            "secondary_tags": secondary_tags,
            "display_name": display_name,
            "classification_method": classification_method,
            "bmi": round(bmi, 1),
            "muscle_ratio": round(calculate_muscle_ratio(skeletal_muscle_mass, weight), 1)
        }


# 간편 함수
def classify_body_type_hybrid(
    gender: str,
    height: float,
    weight: float,
    body_fat_pct: float,
    skeletal_muscle_mass: float,
    bmr: float,
    visceral_fat_level: int,
    **kwargs
) -> dict:
    """간편 분류 함수"""
    classifier = HybridBodyTypeClassifier()
    return classifier.classify(
        gender, height, weight, body_fat_pct, skeletal_muscle_mass,
        bmr, visceral_fat_level, **kwargs
    )
