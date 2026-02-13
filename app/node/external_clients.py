from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

KAKAO_LOCAL_BASE = "https://dapi.kakao.com/v2/local"
SERPER_SEARCH_URL = "https://google.serper.dev/search"


def _auth_header(token: str) -> Dict[str, str]:
    return {"Authorization": f"KakaoAK {token}"}


def get_serper_api_key() -> str:
    """Return Serper API key or raise clear error."""
    api_key = (os.getenv("SERPER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("SERPER_API_KEY 환경변수가 설정되지 않았습니다.")
    return api_key


def kakao_geocode_address(address_text: str, timeout_s: float = 8.0) -> Optional[Dict[str, float]]:
    token = os.getenv("KAKAO_REST_API_KEY")
    if not token or not address_text.strip():
        return None

    resp = requests.get(
        f"{KAKAO_LOCAL_BASE}/search/address.json",
        headers=_auth_header(token),
        params={"query": address_text},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    docs = resp.json().get("documents", [])
    if not docs:
        return None

    first = docs[0]
    return {"lat": float(first["y"]), "lng": float(first["x"])}


def kakao_search_restaurants(lat: float, lng: float, radius_m: int = 500, timeout_s: float = 8.0) -> List[Dict[str, Any]]:
    token = os.getenv("KAKAO_REST_API_KEY")
    if not token:
        return []

    params = {
        "category_group_code": "FD6",
        "x": lng,
        "y": lat,
        "radius": max(50, min(int(radius_m), 20000)),
        "size": 10,
        "sort": "distance",
    }

    results: List[Dict[str, Any]] = []
    for page in (1, 2):
        page_params = dict(params)
        page_params["page"] = page
        resp = requests.get(
            f"{KAKAO_LOCAL_BASE}/search/category.json",
            headers=_auth_header(token),
            params=page_params,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
        docs = payload.get("documents", [])
        results.extend(docs)

        meta = payload.get("meta", {})
        if meta.get("is_end", True) or len(results) >= 10:
            break

    return results[:10]


def kakao_keyword_search_places(
    query: str,
    lat: float,
    lng: float,
    radius_m: int = 500,
    timeout_s: float = 8.0,
) -> List[Dict[str, Any]]:
    token = os.getenv("KAKAO_REST_API_KEY")
    if not token or not query.strip():
        return []

    params = {
        "query": query,
        "category_group_code": "FD6",
        "x": lng,
        "y": lat,
        "radius": max(50, min(int(radius_m), 20000)),
        "size": 15,
        "sort": "distance",
    }

    results: List[Dict[str, Any]] = []
    for page in (1, 2):
        page_params = dict(params)
        page_params["page"] = page
        resp = requests.get(
            f"{KAKAO_LOCAL_BASE}/search/keyword.json",
            headers=_auth_header(token),
            params=page_params,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
        docs = payload.get("documents", [])
        results.extend(docs)

        meta = payload.get("meta", {})
        if meta.get("is_end", True) or len(results) >= 30:
            break

    return results[:30]


def serper_search(
    query: str,
    max_results: int = 5,
    timeout_s: float = 12.0,
    *,
    api_key: Optional[str] = None,
    naver_only: bool = False,
) -> List[Dict[str, Any]]:
    if not query.strip():
        return []

    key = (api_key or "").strip() or get_serper_api_key()
    payload = {
        "q": query,
        "num": max(1, min(int(max_results), 10)),
        "gl": "kr",
        "hl": "ko",
    }
    resp = requests.post(
        SERPER_SEARCH_URL,
        headers={
            "X-API-KEY": key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_s,
    )
    resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, dict):
        return []

    results: List[Dict[str, Any]] = []
    organic = data.get("organic") or []
    if isinstance(organic, list):
        for row in organic:
            if not isinstance(row, dict):
                continue
            url = row.get("link")
            if not url:
                continue
            if naver_only and "naver.com" not in str(url).lower():
                continue
            results.append(
                {
                    "title": row.get("title"),
                    "url": url,
                    "content": row.get("snippet") or row.get("date"),
                }
            )

    return results[: max(1, int(max_results))]
