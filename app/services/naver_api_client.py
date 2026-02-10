"""Naver Maps API client (best-effort)."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import requests

from app.services.menu_scraper import scrape_menus


def fetch_menus(restaurant_name: str, address_text: Optional[str]) -> List[Dict[str, str]]:
    """Fetch menus using Naver Search API + place URL scraping."""
    place_url = _search_place_link(restaurant_name, address_text)
    if not place_url:
        return []
    return scrape_menus(place_url)


def validate_menus(
    restaurant_name: str,
    address_text: Optional[str],
    menus: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Validate or enrich menus using Naver Search API."""
    place_url = _search_place_link(restaurant_name, address_text)
    if not place_url:
        return menus
    refreshed = scrape_menus(place_url)
    if refreshed:
        return refreshed
    return menus


def _search_place_link(restaurant_name: str, address_text: Optional[str]) -> Optional[str]:
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    query = restaurant_name
    if address_text:
        query = f"{restaurant_name} {address_text}"

    try:
        response = requests.get(
            "https://openapi.naver.com/v1/search/local.json",
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            params={"query": query, "display": 1},
            timeout=6,
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except requests.RequestException:
        return None

    items = data.get("items") or []
    if not items:
        return None
    link = items[0].get("link")
    return link


__all__ = ["fetch_menus", "validate_menus"]
