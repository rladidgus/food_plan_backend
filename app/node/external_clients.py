from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

KAKAO_LOCAL_BASE = "https://dapi.kakao.com/v2/local"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _auth_header(token: str) -> Dict[str, str]:
    return {"Authorization": f"KakaoAK {token}"}


def get_tavily_api_key() -> str:
    """Return Tavily API key or raise clear error."""
    api_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY 환경변수가 설정되지 않았습니다.")
    return api_key


def _get_tavily_client():
    """Create Tavily SDK client lazily."""
    try:
        from tavily import TavilyClient  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Tavily SDK가 필요합니다. `pip install tavily-python`로 설치하세요."
        ) from exc

    return TavilyClient(api_key=get_tavily_api_key())


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


def tavily_search(
    query: str,
    max_results: int = 5,
    timeout_s: float = 12.0,
    *,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # keep signature compatibility (api_key/timeout_s). SDK path is used.
    _ = timeout_s
    if not query.strip():
        return []

    if api_key:
        # optional override when caller wants explicit key
        os.environ["TAVILY_API_KEY"] = api_key

    client = _get_tavily_client()
    payload = client.search(
        query=query,
        search_depth="advanced",
        max_results=max_results,
        include_answer=True,
        include_raw_content=False,
    )
    return payload.get("results", []) if isinstance(payload, dict) else []
