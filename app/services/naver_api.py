"""Naver Maps API client (geocode + place search)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
import os

import requests

logger = logging.getLogger(__name__)

NAVER_GEOCODE_URL = os.getenv(
    "NAVER_GEOCODE_URL",
    "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode",
)
NAVER_MAPS_PLACE_SEARCH_URL = os.getenv(
    "NAVER_MAPS_PLACE_SEARCH_URL",
    "https://naveropenapi.apigw.ntruss.com/map-place/v1/search",
)

NAVER_MAPS_API_KEY_ID = os.getenv("NAVER_MAPS_API_KEY_ID") or os.getenv("NAVER_MAPS_KEY_ID")
NAVER_MAPS_API_KEY = os.getenv("NAVER_MAPS_API_KEY") or os.getenv("NAVER_MAPS_KEY")


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def geocode_address(address_text: str) -> Dict[str, Any]:
    """주소 -> 좌표 변환."""
    if not address_text:
        return {"lat": None, "lng": None}
    if not NAVER_MAPS_API_KEY_ID or not NAVER_MAPS_API_KEY:
        logger.warning("NAVER_MAPS_API_KEY_ID/API_KEY 환경변수가 필요합니다.")
        return {"lat": None, "lng": None}
    try:
        resp = requests.get(
            NAVER_GEOCODE_URL,
            headers={
                "X-NCP-APIGW-API-KEY-ID": NAVER_MAPS_API_KEY_ID,
                "X-NCP-APIGW-API-KEY": NAVER_MAPS_API_KEY,
            },
            params={"query": address_text},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("Naver geocode failed: status=%s, body=%s", resp.status_code, (resp.text or "")[:200])
            return {"lat": None, "lng": None}
        data = resp.json()
        addresses = data.get("addresses") or []
        if not addresses:
            return {"lat": None, "lng": None}
        first = addresses[0]
        return {"lat": _safe_float(first.get("y")), "lng": _safe_float(first.get("x"))}
    except Exception as exc:
        logger.exception("Naver geocode exception: %s", exc)
        return {"lat": None, "lng": None}


def search_restaurants(
    *,
    lat: float,
    lng: float,
    radius_m: int = 500,
    page_size: int = 15,
    max_pages: int = 3,
) -> List[Dict[str, Any]]:
    """좌표 기준 음식점 검색 (Naver Maps Place Search API)."""
    if not NAVER_MAPS_API_KEY_ID or not NAVER_MAPS_API_KEY:
        logger.warning("NAVER_MAPS_API_KEY_ID/API_KEY 환경변수가 필요합니다.")
        return []

    query = "음식점"
    results: List[Dict[str, Any]] = []
    display = max(1, min(int(page_size), 50))
    pages = max(1, min(int(max_pages), 5))

    for page in range(pages):
        start = page * display + 1
        try:
            resp = requests.get(
                NAVER_MAPS_PLACE_SEARCH_URL,
                headers={
                    "X-NCP-APIGW-API-KEY-ID": NAVER_MAPS_API_KEY_ID,
                    "X-NCP-APIGW-API-KEY": NAVER_MAPS_API_KEY,
                },
                params={
                    "query": query,
                    "coordinate": f"{lng},{lat}",
                    "radius": int(radius_m),
                    "display": display,
                    "start": start,
                },
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Naver maps place search failed: status=%s, body=%s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                break
            data = resp.json()
            items = data.get("places") or data.get("items") or []
            if not items:
                break
            results.extend(items)
        except Exception as exc:
            logger.exception("Naver maps place search exception: %s", exc)
            break

    return results


__all__ = ["geocode_address", "search_restaurants"]
