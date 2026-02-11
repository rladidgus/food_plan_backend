"""Kakao Local API client."""

from __future__ import annotations

import os
from typing import Dict, Any, List, Optional

import requests


KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_LOCAL_ADDR_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_LOCAL_CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"


def geocode_address(address_text: str) -> Dict[str, Any]:
    """주소 -> 좌표 변환."""
    if not address_text:
        return {"lat": None, "lng": None}
    if not KAKAO_REST_API_KEY:
        return {"lat": None, "lng": None}
    try:
        resp = requests.get(
            KAKAO_LOCAL_ADDR_URL,
            headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
            params={"query": address_text},
            timeout=5,
        )
        if resp.status_code != 200:
            return {"lat": None, "lng": None}
        data = resp.json()
        docs = data.get("documents") or []
        if not docs:
            return {"lat": None, "lng": None}
        first = docs[0]
        return {"lat": _safe_float(first.get("y")), "lng": _safe_float(first.get("x"))}
    except requests.RequestException:
        return {"lat": None, "lng": None}


def search_restaurants(lat: float, lng: float, radius_m: int = 500) -> List[Dict[str, Any]]:
    """좌표 기준 음식점 검색."""
    if not KAKAO_REST_API_KEY:
        return []
    results: List[Dict[str, Any]] = []
    page = 1
    size = 15
    max_pages = 3
    while page <= max_pages:
        try:
            resp = requests.get(
                KAKAO_LOCAL_CATEGORY_URL,
                headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
                params={
                    "category_group_code": "FD6",
                    "x": lng,
                    "y": lat,
                    "radius": radius_m,
                    "page": page,
                    "size": size,
                    "sort": "distance",
                },
                timeout=5,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("documents") or []
            results.extend(items)
            meta = data.get("meta") or {}
            if meta.get("is_end"):
                break
        except requests.RequestException:
            break
        page += 1
    return results


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["geocode_address", "search_restaurants"]
