"""
성별 특화 인바디 데이터 기반 군집 예측 모듈
"""
import pandas as pd
import numpy as np
from joblib import load
from pathlib import Path
import json


# 성별별 군집 명명
MALE_CLUSTER_NAMES = {
    0: "남성 표준형",
    1: "남성 과체중형", 
    2: "남성 근육질형"
}

FEMALE_CLUSTER_NAMES = {
    0: "여성 표준형",
    1: "여성 마른형",
    2: "여성 과체중형"
}

MALE_CLUSTER_DESCRIPTIONS = {
    0: "체지방률과 근육량이 표준 범위에 있는 남성 체형입니다. 키가 다소 작고 전반적으로 균형잡힌 체성분입니다.",
    1: "체중이 많이 나가며 체지방률과 내장지방이 높은 남성 체형입니다. 골격근량도 많지만 체지방 관리가 필요합니다.",
    2: "체지방률이 낮고 골격근량이 매우 우수한 남성 체형입니다. 운동을 꾸준히 하는 건강하고 이상적인 체형입니다."
}

FEMALE_CLUSTER_DESCRIPTIONS = {
    0: "체지방률과 근육량이 표준 범위에 있는 여성 체형입니다. 키와 체중이 평균적이며 건강한 체성분을 유지하고 있습니다.",
    1: "체중이 적게 나가며 골격근량이 부족한 여성 체형입니다. 키가 작고 마른 편이지만 체지방률은 보통 수준입니다.",
    2: "체지방률이 높고 내장지방이 증가한 여성 체형입니다. 체중 감량과 체지방 관리가 필요합니다."
}


def coerce_numeric(df: pd.DataFrame, cols):
    """숫자 컬럼을 numeric으로 강제 변환"""
    for c in cols:
        if c in df.columns:
            df[c] = (
                df[c].astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
                .replace({"nan": np.nan, "None": np.nan, "": np.nan})
            )
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """train_gender_specific.py와 동일한 피처 엔지니어링"""
    if "측정일자" in df.columns:
        df["측정일자"] = pd.to_datetime(df["측정일자"], errors="coerce")
        df["측정_월"] = df["측정일자"].dt.month
        df["측정_요일"] = df["측정일자"].dt.dayofweek
        df["측정_연도"] = df["측정일자"].dt.year

    if "측정시간" in df.columns:
        t = df["측정시간"].astype(str).str.replace(":", "", regex=False)
        df["측정_시"] = pd.to_numeric(t.str.slice(0, 2), errors="coerce")

    if "사용자 출생년도" in df.columns:
        df["사용자 출생년도"] = pd.to_numeric(df["사용자 출생년도"], errors="coerce")
        if "측정_연도" in df.columns:
            df["나이_대략"] = df["측정_연도"] - df["사용자 출생년도"]
        else:
            df["나이_대략"] = 2026 - df["사용자 출생년도"]

    return df


class GenderSpecificPredictor:
    """성별 특화 인바디 데이터 기반 군집 예측 클래스"""
    
    def __init__(self, 
                 male_model_path: str = "models/inbody_male_k3_model.joblib",
                 female_model_path: str = "models/inbody_female_k3_model.joblib"):
        """
        Args:
            male_model_path: 남성 모델 파일 경로
            female_model_path: 여성 모델 파일 경로
        """
        # 남성 모델 로드
        if not Path(male_model_path).exists():
            raise FileNotFoundError(f"Male model file not found: {male_model_path}")
        self.male_model = load(male_model_path)
        
        # 여성 모델 로드
        if not Path(female_model_path).exists():
            raise FileNotFoundError(f"Female model file not found: {female_model_path}")
        self.female_model = load(female_model_path)
        
        self.male_model_path = male_model_path
        self.female_model_path = female_model_path
    
    def predict(self, inbody_data: dict, gender: str) -> int:
        """새로운 인바디 데이터의 군집 예측
        
        Args:
            inbody_data: 인바디 측정 데이터 딕셔너리
                필수 키: 키, 체중, 체지방률, 골격근량, 기초대사량, 내장지방레벨
                선택 키: 측정일자, 사용자 출생년도, 체지방량, 복부지방률, 인바디점수
            gender: 성별 ("M" or "F")
        
        Returns:
            예측된 군집 ID (0~2)
        """
        # 성별에 따라 모델 선택
        model = self.male_model if gender == "M" else self.female_model
        
        # DataFrame으로 변환
        df = pd.DataFrame([inbody_data])
        
        # 선택적 컬럼에 대한 기본값 설정
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
        
        # 피처 엔지니어링
        df = build_features(df)
        
        # 학습에 사용하지 않은 컬럼 제거 (성별 포함)
        drop_cols = [c for c in ["사용자 고유번호", "측정일자", "성별"] if c in df.columns]
        X = df.drop(columns=drop_cols, errors="ignore")
        
        # 숫자 변환
        cat_cols = [c for c in ["행정동명"] if c in X.columns]
        num_cols = [c for c in X.columns if c not in cat_cols]
        X = coerce_numeric(X, num_cols)
        
        # 예측
        cluster = model.predict(X)[0]
        return int(cluster)
    
    def predict_with_info(self, inbody_data: dict, gender: str) -> dict:
        """군집 예측 + 상세 정보 반환
        
        Args:
            inbody_data: 인바디 데이터
            gender: "M" or "F"
        
        Returns:
            {
                "cluster_id": int,
                "cluster_name": str,
                "description": str,
                "gender": str
            }
        """
        cluster_id = self.predict(inbody_data, gender)
        
        # 성별에 따라 이름과 설명 선택
        if gender == "M":
            cluster_name = MALE_CLUSTER_NAMES.get(cluster_id, f"남성 군집 {cluster_id}")
            description = MALE_CLUSTER_DESCRIPTIONS.get(cluster_id, "")
        else:
            cluster_name = FEMALE_CLUSTER_NAMES.get(cluster_id, f"여성 군집 {cluster_id}")
            description = FEMALE_CLUSTER_DESCRIPTIONS.get(cluster_id, "")
        
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "description": description,
            "gender": gender
        }
    
    def get_cluster_info(self, cluster_id: int, gender: str) -> dict:
        """특정 군집의 정보 반환
        
        Args:
            cluster_id: 군집 ID (0~2)
            gender: "M" or "F"
        
        Returns:
            군집 정보 딕셔너리
        """
        if gender == "M":
            cluster_name = MALE_CLUSTER_NAMES.get(cluster_id, f"남성 군집 {cluster_id}")
            description = MALE_CLUSTER_DESCRIPTIONS.get(cluster_id, "")
        else:
            cluster_name = FEMALE_CLUSTER_NAMES.get(cluster_id, f"여성 군집 {cluster_id}")
            description = FEMALE_CLUSTER_DESCRIPTIONS.get(cluster_id, "")
        
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "description": description,
            "gender": gender
        }


def predict_simple(
    gender: str,
    height: float,
    weight: float,
    body_fat_pct: float,
    skeletal_muscle_mass: float,
    bmr: float,
    visceral_fat_level: int,
    birth_year: int = None,
    male_model_path: str = "inbody_male_k3_model.joblib",
    female_model_path: str = "inbody_female_k3_model.joblib"
) -> dict:
    """간편 예측 함수
    
    개별 파라미터로 예측 수행 (딕셔너리 변환 불필요)
    
    Returns:
        예측 결과 딕셔너리
    """
    predictor = GenderSpecificPredictor(male_model_path, female_model_path)
    
    inbody_data = {
        "키": height,
        "체중": weight,
        "체지방률": body_fat_pct,
        "골격근량": skeletal_muscle_mass,
        "기초대사량": bmr,
        "내장지방레벨": visceral_fat_level
    }
    
    if birth_year:
        inbody_data["사용자 출생년도"] = birth_year
    
    return predictor.predict_with_info(inbody_data, gender)
