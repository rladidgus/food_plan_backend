"""Kakao Local API client (stub)."""

from __future__ import annotations

from typing import Dict, Any, List


def geocode_address(address_text: str) -> Dict[str, Any]:
    """주소 -> 좌표 변환 (stub)."""
    return {"lat": None, "lng": None}


def search_restaurants(lat: float, lng: float, radius_m: int = 500) -> List[Dict[str, Any]]:
    """좌표 기준 음식점 검색 (stub)."""
    return []


__all__ = ["geocode_address", "search_restaurants"]
