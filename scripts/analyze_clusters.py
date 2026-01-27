#!/usr/bin/env python3
"""
군집 분석 스크립트: 학습된 KMeans 모델의 각 군집 특성을 분석

출력: cluster_analysis.json - 각 군집의 평균 체성분 수치 및 샘플 수
"""
import argparse
import json
import pandas as pd
import numpy as np
from pathlib import Path
from joblib import load


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
    """train_inbody_cluster.py와 동일한 피처 엔지니어링"""
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


def analyze_clusters(model_path: str, data_path: str, latest_per_user: bool = True, gender: str = None):
    """군집 특성 분석 및 JSON 출력"""
    
    # 모델 로드
    print(f"[INFO] Loading model from {model_path}...")
    pipeline = load(model_path)
    
    # 데이터 로드
    print(f"[INFO] Loading data from {data_path}...")
    df = pd.read_csv(data_path)
    df = build_features(df)
    
    # 최신 측정 1건만 사용 (train과 동일)
    if latest_per_user and "사용자 고유번호" in df.columns and "측정일자" in df.columns:
        df = df.sort_values(["사용자 고유번호", "측정일자"]).groupby("사용자 고유번호").tail(1)

    # 성별 필터링 (모델 학습과 동일)
    if gender and "성별" in df.columns:
        df = df[df["성별"] == gender].copy()
    
    # 학습에 사용된 컬럼과 동일하게 전처리
    drop_cols = [c for c in ["사용자 고유번호", "측정일자"] if c in df.columns]
    X = df.drop(columns=drop_cols)
    
    # 모든 컬럼을 숫자로 변환
    cat_cols = [c for c in ["성별", "행정동명"] if c in X.columns]
    num_cols = [c for c in X.columns if c not in cat_cols]
    X = coerce_numeric(X, num_cols)
    
    # 군집 예측
    print("[INFO] Predicting clusters...")
    labels = pipeline.predict(X)
    df["cluster"] = labels
    
    # 원본 데이터에서 주요 인바디 지표 선택
    key_metrics = [
        "키", "체중", "체지방량", "체지방률", "골격근량", 
        "기초대사량", "내장지방레벨", "복부지방률", "인바디점수"
    ]
    
    # 군집별 분석
    analysis = {}
    for cluster_id in sorted(df["cluster"].unique()):
        cluster_df = df[df["cluster"] == cluster_id]
        
        cluster_info = {
            "count": int(len(cluster_df)),
            "percentage": round(len(cluster_df) / len(df) * 100, 2)
        }
        
        # 각 지표의 평균 계산
        for metric in key_metrics:
            if metric in cluster_df.columns:
                # 숫자 변환
                cluster_df[metric] = pd.to_numeric(cluster_df[metric], errors="coerce")
                mean_val = cluster_df[metric].mean()
                std_val = cluster_df[metric].std()
                
                if not pd.isna(mean_val):
                    cluster_info[f"avg_{metric}"] = round(mean_val, 2)
                    cluster_info[f"std_{metric}"] = round(std_val, 2)
        
        # 성별 분포
        if "성별" in cluster_df.columns:
            gender_counts = cluster_df["성별"].value_counts().to_dict()
            cluster_info["gender_distribution"] = gender_counts
        
        # 나이 분포
        if "나이_대략" in cluster_df.columns:
            cluster_info["avg_age"] = round(cluster_df["나이_대략"].mean(), 1)
        
        analysis[str(cluster_id)] = cluster_info
    
    # 전체 통계
    analysis["summary"] = {
        "total_samples": int(len(df)),
        "num_clusters": len(analysis) - 1,  # summary 제외
        "model_path": model_path
    }
    
    # JSON 저장
    output_file = "cluster_analysis.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    
    print(f"\n[OK] Analysis saved to {output_file}")
    print("\n=== Cluster Summary ===")
    for cluster_id in sorted([int(k) for k in analysis.keys() if k != "summary"]):
        info = analysis[str(cluster_id)]
        print(f"\nCluster {cluster_id}: {info['count']} samples ({info['percentage']}%)")
        print(f"  체지방률: {info.get('avg_체지방률', 'N/A'):.1f}% (±{info.get('std_체지방률', 0):.1f})")
        print(f"  골격근량: {info.get('avg_골격근량', 'N/A'):.1f}kg (±{info.get('std_골격근량', 0):.1f})")
        print(f"  내장지방: {info.get('avg_내장지방레벨', 'N/A'):.1f} (±{info.get('std_내장지방레벨', 0):.1f})")
        print(f"  인바디점수: {info.get('avg_인바디점수', 'N/A'):.0f} (±{info.get('std_인바디점수', 0):.0f})")
        if "gender_distribution" in info:
            print(f"  성별: {info['gender_distribution']}")
    
    return analysis


def main():
    ap = argparse.ArgumentParser(description="Analyze InBody clusters")
    ap.add_argument("--model", type=str, required=True, help="Path to trained model (.joblib)")
    ap.add_argument("--data", type=str, required=True, help="Path to CSV data")
    ap.add_argument("--latest_per_user", action="store_true", help="Use latest measurement per user")
    ap.add_argument("--gender", type=str, choices=["M", "F"], help="Filter by gender (M or F)")
    args = ap.parse_args()
    
    analyze_clusters(args.model, args.data, args.latest_per_user, args.gender)


if __name__ == "__main__":
    main()
