#!/usr/bin/env python3
"""
성별로 데이터를 분리하여 각각 클러스터링 모델 학습
"""
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from joblib import dump
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score


def coerce_numeric(df: pd.DataFrame, cols):
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


def train_gender_specific_model(df: pd.DataFrame, gender: str, k: int = 3):
    """성별 특화 모델 학습
    
    Args:
        df: 전체 데이터프레임
        gender: "M" or "F"
        k: 군집 개수
    
    Returns:
        학습된 파이프라인
    """
    gender_name = "남성" if gender == "M" else "여성"
    print(f"\n{'='*60}")
    print(f"[{gender_name}] 모델 학습 (K={k})")
    print(f"{'='*60}")
    
    # 성별 필터링
    df_gender = df[df["성별"] == gender].copy()
    print(f"샘플 수: {len(df_gender):,}")
    
    if len(df_gender) < 50:
        raise ValueError(f"{gender_name} 데이터가 너무 적습니다.")
    
    # 학습에 사용하지 않을 컬럼 제거 (성별도 제거 - 이미 분리했으므로)
    drop_cols = [c for c in ["사용자 고유번호", "측정일자", "성별"] if c in df_gender.columns]
    X = df_gender.drop(columns=drop_cols)
    
    # 범주형/수치형 분리
    cat_cols = [c for c in ["행정동명"] if c in X.columns]
    num_cols = [c for c in X.columns if c not in cat_cols]
    
    # 수치 변환
    X = coerce_numeric(X, num_cols)
    
    # 파이프라인 구성
    numeric_pipe = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler())
    ])
    
    if cat_cols:
        categorical_pipe = Pipeline(steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore"))
        ])
        
        pre = ColumnTransformer(
            transformers=[
                ("num", numeric_pipe, num_cols),
                ("cat", categorical_pipe, cat_cols)
            ],
            remainder="drop",
            verbose_feature_names_out=False
        )
    else:
        pre = ColumnTransformer(
            transformers=[
                ("num", numeric_pipe, num_cols)
            ],
            remainder="drop",
            verbose_feature_names_out=False
        )
    
    model = KMeans(n_clusters=k, n_init="auto", random_state=42)
    
    pipe = Pipeline(steps=[
        ("preprocess", pre),
        ("kmeans", model)
    ])
    
    pipe.fit(X)
    
    # Silhouette score 계산
    Z = pipe.named_steps["preprocess"].transform(X)
    labels = pipe.named_steps["kmeans"].labels_
    sample_n = min(3000, Z.shape[0])
    idx = np.random.RandomState(42).choice(Z.shape[0], size=sample_n, replace=False)
    sil = silhouette_score(Z[idx], labels[idx])
    
    print(f"[INFO] Silhouette Score (sample={sample_n}): {sil:.4f}")
    
    # 각 군집의 특성 출력
    print(f"\n군집별 특성:")
    for cluster_id in range(k):
        cluster_mask = labels == cluster_id
        cluster_data = df_gender[cluster_mask]
        
        print(f"\n  군집 {cluster_id}: N={len(cluster_data):4d} ({len(cluster_data)/len(df_gender)*100:.1f}%)")
        print(f"    키: {cluster_data['키'].mean():.1f}cm")
        print(f"    체중: {cluster_data['체중'].mean():.1f}kg")
        print(f"    체지방률: {cluster_data['체지방률'].mean():.1f}%")
        print(f"    골격근량: {cluster_data['골격근량'].mean():.1f}kg")
        print(f"    내장지방: {cluster_data['내장지방레벨'].mean():.1f}")
        print(f"    인바디점수: {cluster_data['인바디점수'].mean():.0f}")
    
    return pipe, df_gender


def main():
    ap = argparse.ArgumentParser(description="Train gender-specific clustering models")
    ap.add_argument("--csv", type=str, required=True, help="Path to CSV data")
    ap.add_argument("--k", type=int, default=3, help="Number of clusters")
    ap.add_argument("--latest_per_user", action="store_true", help="Use latest measurement per user")
    args = ap.parse_args()
    
    # 데이터 로드
    print(f"[INFO] Loading data from {args.csv}...")
    df = pd.read_csv(args.csv)
    df = build_features(df)
    
    # 최신 측정 1건만 사용
    if args.latest_per_user and "사용자 고유번호" in df.columns and "측정일자" in df.columns:
        df = df.sort_values(["사용자 고유번호", "측정일자"]).groupby("사용자 고유번호").tail(1)
        print(f"[INFO] Using latest measurement per user: {len(df):,} samples")
    
    # 남성 모델 학습
    male_model, male_data = train_gender_specific_model(df, "M", args.k)
    male_output = f"inbody_male_k{args.k}_model.joblib"
    dump(male_model, male_output)
    print(f"\n[OK] 남성 모델 저장: {male_output}")
    
    # 여성 모델 학습
    female_model, female_data = train_gender_specific_model(df, "F", args.k)
    female_output = f"inbody_female_k{args.k}_model.joblib"
    dump(female_model, female_output)
    print(f"\n[OK] 여성 모델 저장: {female_output}")
    
    print("\n" + "="*60)
    print("✅ 모델 학습 완료!")
    print("="*60)
    print(f"\n남성 모델: {male_output}")
    print(f"여성 모델: {female_output}")
    print(f"\n다음 단계:")
    print("1. analyze_clusters.py 실행하여 각 군집 특성 분석")
    print("2. predict_cluster.py 업데이트하여 성별별 모델 사용")
    print()


if __name__ == "__main__":
    main()
