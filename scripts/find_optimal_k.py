#!/usr/bin/env python3
"""
최적의 군집 개수(K) 찾기

Elbow Method와 Silhouette Score를 사용하여
최적의 K를 찾고 시각화합니다.
"""
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer


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


def find_optimal_k(csv_path: str, max_k: int = 15, by_gender: bool = False):
    """최적의 K 찾기
    
    Args:
        csv_path: 데이터 파일 경로
        max_k: 테스트할 최대 K 값
        by_gender: 성별로 분리하여 클러스터링 여부
    """
    print(f"[INFO] Loading data from {csv_path}...")
    df = pd.read_csv(csv_path)
    df = build_features(df)
    
    # 최신 측정 1건만 사용
    if "사용자 고유번호" in df.columns and "측정일자" in df.columns:
        df = df.sort_values(["사용자 고유번호", "측정일자"]).groupby("사용자 고유번호").tail(1)
    
    if by_gender:
        print("\n[MODE] 성별로 분리하여 클러스터링")
        analyze_by_gender(df, max_k)
    else:
        print("\n[MODE] 전체 데이터 클러스터링")
        analyze_all(df, max_k)


def analyze_all(df: pd.DataFrame, max_k: int):
    """전체 데이터에 대해 K 테스트"""
    
    drop_cols = [c for c in ["사용자 고유번호", "측정일자"] if c in df.columns]
    X = df.drop(columns=drop_cols)
    
    cat_cols = [c for c in ["성별", "행정동명"] if c in X.columns]
    num_cols = [c for c in X.columns if c not in cat_cols]
    X = coerce_numeric(X, num_cols)
    
    numeric_pipe = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler())
    ])
    
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
    
    # 데이터 전처리
    X_transformed = pre.fit_transform(X)
    
    # K 범위 테스트
    k_range = range(2, max_k + 1)
    inertias = []
    silhouette_scores = []
    
    print("\n[INFO] Testing different K values...")
    for k in k_range:
        kmeans = KMeans(n_clusters=k, n_init="auto", random_state=42)
        labels = kmeans.fit_predict(X_transformed)
        
        inertias.append(kmeans.inertia_)
        
        # Silhouette score (샘플링)
        sample_n = min(5000, X_transformed.shape[0])
        idx = np.random.RandomState(42).choice(X_transformed.shape[0], size=sample_n, replace=False)
        sil = silhouette_score(X_transformed[idx], labels[idx])
        silhouette_scores.append(sil)
        
        print(f"  K={k:2d}: Inertia={kmeans.inertia_:10.2f}, Silhouette={sil:.4f}")
    
    # 시각화
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    # Elbow plot
    ax1.plot(k_range, inertias, 'bo-')
    ax1.set_xlabel('Number of clusters (K)')
    ax1.set_ylabel('Inertia (Within-cluster sum of squares)')
    ax1.set_title('Elbow Method')
    ax1.grid(True, alpha=0.3)
    
    # Silhouette plot
    ax2.plot(k_range, silhouette_scores, 'ro-')
    ax2.set_xlabel('Number of clusters (K)')
    ax2.set_ylabel('Silhouette Score')
    ax2.set_title('Silhouette Score vs K')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=max(silhouette_scores), color='g', linestyle='--', alpha=0.5, 
                label=f'Max: K={k_range[silhouette_scores.index(max(silhouette_scores))]}')
    ax2.legend()
    
    plt.tight_layout()
    plt.savefig('optimal_k_analysis.png', dpi=150, bbox_inches='tight')
    print(f"\n[OK] Plot saved to optimal_k_analysis.png")
    
    # 추천 K
    best_k = k_range[silhouette_scores.index(max(silhouette_scores))]
    print(f"\n[RECOMMENDATION] Silhouette Score 기준 최적 K = {best_k}")


def analyze_by_gender(df: pd.DataFrame, max_k: int):
    """성별로 분리하여 각각 클러스터링"""
    
    if "성별" not in df.columns:
        print("[ERROR] '성별' 컬럼이 없습니다.")
        return
    
    for gender in ["M", "F"]:
        gender_name = "남성" if gender == "M" else "여성"
        print(f"\n{'='*60}")
        print(f"[{gender_name}] 데이터 클러스터링")
        print(f"{'='*60}")
        
        df_gender = df[df["성별"] == gender].copy()
        print(f"샘플 수: {len(df_gender)}")
        
        if len(df_gender) < 50:
            print(f"[SKIP] 샘플 수가 너무 적습니다.")
            continue
        
        drop_cols = [c for c in ["사용자 고유번호", "측정일자", "성별"] if c in df_gender.columns]
        X = df_gender.drop(columns=drop_cols)
        
        cat_cols = [c for c in ["행정동명"] if c in X.columns]
        num_cols = [c for c in X.columns if c not in cat_cols]
        X = coerce_numeric(X, num_cols)
        
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
                remainder="drop"
            )
        else:
            pre = ColumnTransformer(
                transformers=[
                    ("num", numeric_pipe, num_cols)
                ],
                remainder="drop"
            )
        
        X_transformed = pre.fit_transform(X)
        
        # K=3으로 고정 테스트 (마른형/표준형/과체중형)
        k_test = min(5, len(df_gender) // 100)  # 샘플 수에 따라 조정
        k_range = range(2, k_test + 1)
        
        print(f"\n[INFO] Testing K from 2 to {k_test}...")
        for k in k_range:
            kmeans = KMeans(n_clusters=k, n_init="auto", random_state=42)
            labels = kmeans.fit_predict(X_transformed)
            
            sample_n = min(3000, X_transformed.shape[0])
            idx = np.random.RandomState(42).choice(X_transformed.shape[0], size=sample_n, replace=False)
            sil = silhouette_score(X_transformed[idx], labels[idx])
            
            print(f"  K={k}: Silhouette={sil:.4f}")
            
            # K=3일 때 각 군집의 평균 출력
            if k == 3:
                print(f"\n  K=3일 때 군집별 평균:")
                for cluster_id in range(3):
                    cluster_mask = labels == cluster_id
                    cluster_data = df_gender[cluster_mask]
                    
                    avg_bf = cluster_data["체지방률"].mean()
                    avg_sm = cluster_data["골격근량"].mean()
                    count = len(cluster_data)
                    
                    print(f"    군집 {cluster_id}: N={count:4d}, 체지방률={avg_bf:.1f}%, 골격근량={avg_sm:.1f}kg")


def main():
    ap = argparse.ArgumentParser(description="Find optimal K for clustering")
    ap.add_argument("--csv", type=str, required=True, help="Path to CSV data")
    ap.add_argument("--max_k", type=int, default=15, help="Maximum K to test")
    ap.add_argument("--by_gender", action="store_true", help="Cluster by gender separately")
    args = ap.parse_args()
    
    find_optimal_k(args.csv, args.max_k, args.by_gender)


if __name__ == "__main__":
    main()
