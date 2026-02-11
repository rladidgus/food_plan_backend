"""Naver place search client (NCP Maps preferred, Local Search fallback)."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import requests

from app.services.menu_scraper import scrape_menus
from app.services import naver_api


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
    base_query = restaurant_name
    if address_text:
        base_query = f"{restaurant_name} {address_text}"

    # 1) NCP Maps Place Search (preferred)
    maps_key_id = os.getenv("NAVER_MAPS_API_KEY_ID") or os.getenv("NAVER_MAPS_KEY_ID")
    maps_key = os.getenv("NAVER_MAPS_API_KEY") or os.getenv("NAVER_MAPS_KEY")
    if maps_key_id and maps_key:
        lat = lng = None
        if address_text:
            geo = naver_api.geocode_address(address_text)
            lat = geo.get("lat")
            lng = geo.get("lng")
        for query in (base_query, f"{base_query} 네이버플레이스"):
            params = {"query": query, "display": 5}
            if lat is not None and lng is not None:
                params["coordinate"] = f"{lng},{lat}"
                params["radius"] = 2000
            try:
                response = requests.get(
                    "https://naveropenapi.apigw.ntruss.com/map-place/v1/search",
                    headers={
                        "X-NCP-APIGW-API-KEY-ID": maps_key_id,
                        "X-NCP-APIGW-API-KEY": maps_key,
                    },
                    params=params,
                    timeout=6,
                )
            except requests.RequestException:
                continue
            if response.status_code != 200:
                continue
            data = response.json() or {}
            items = data.get("places") or data.get("items") or []
            for item in items:
                link = item.get("link")
                if link and "place.naver.com" in link:
                    return link
            if items:
                link = items[0].get("link")
                if link:
                    return link

    # 2) Local Search fallback
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    def request(query: str) -> Optional[Dict[str, object]]:
        try:
            response = requests.get(
                "https://openapi.naver.com/v1/search/local.json",
                headers={
                    "X-Naver-Client-Id": client_id,
                    "X-Naver-Client-Secret": client_secret,
                },
                params={"query": query, "display": 5},
                timeout=6,
            )
            if response.status_code != 200:
                return None
            return response.json()
        except requests.RequestException:
            return None

    for query in (base_query, f"{base_query} 네이버플레이스"):
        data = request(query)
        if not data:
            continue
        items = data.get("items") or []
        if not items:
            continue
        for item in items:
            link = item.get("link")
            if link and "place.naver.com" in link:
                return link
        if items[0].get("link"):
            return items[0].get("link")
    return None


def debug_search_place_link(restaurant_name: str, address_text: Optional[str]) -> Dict[str, Optional[str]]:
    """Debug helper to verify Naver place search response."""
    base_query = restaurant_name if not address_text else f"{restaurant_name} {address_text}"
    queries = [base_query, f"{base_query} 네이버플레이스"]

    maps_key_id = os.getenv("NAVER_MAPS_API_KEY_ID") or os.getenv("NAVER_MAPS_KEY_ID")
    maps_key = os.getenv("NAVER_MAPS_API_KEY") or os.getenv("NAVER_MAPS_KEY")
    if maps_key_id and maps_key:
        lat = lng = None
        if address_text:
            geo = naver_api.geocode_address(address_text)
            lat = geo.get("lat")
            lng = geo.get("lng")
        for query in queries:
            params = {"query": query, "display": 5}
            if lat is not None and lng is not None:
                params["coordinate"] = f"{lng},{lat}"
                params["radius"] = 2000
            try:
                response = requests.get(
                    "https://naveropenapi.apigw.ntruss.com/map-place/v1/search",
                    headers={
                        "X-NCP-APIGW-API-KEY-ID": maps_key_id,
                        "X-NCP-APIGW-API-KEY": maps_key,
                    },
                    params=params,
                    timeout=6,
                )
            except requests.RequestException as exc:
                return {"error": str(exc), "status": None, "link": None}
            if response.status_code != 200:
                return {"error": response.text[:200], "status": str(response.status_code), "link": None}
            data = response.json() or {}
            items = data.get("places") or data.get("items") or []
            if not items:
                continue
            for item in items:
                if item.get("link") and "place.naver.com" in item.get("link"):
                    return {"error": None, "status": "200", "link": item.get("link")}
            return {"error": None, "status": "200", "link": items[0].get("link")}

    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {"error": "missing_client_id_or_secret", "status": None, "link": None}

    for query in queries:
        try:
            response = requests.get(
                "https://openapi.naver.com/v1/search/local.json",
                headers={
                    "X-Naver-Client-Id": client_id,
                    "X-Naver-Client-Secret": client_secret,
                },
                params={"query": query, "display": 5},
                timeout=6,
            )
        except requests.RequestException as exc:
            return {"error": str(exc), "status": None, "link": None}

        if response.status_code != 200:
            return {"error": response.text[:200], "status": str(response.status_code), "link": None}

        data = response.json()
        items = data.get("items") or []
        if not items:
            continue
        link = None
        for item in items:
            if item.get("link") and "place.naver.com" in item.get("link"):
                link = item.get("link")
                break
        link = link or items[0].get("link")
        return {"error": None, "status": "200", "link": link}

    return {"error": "no_items", "status": "200", "link": None}


__all__ = ["fetch_menus", "validate_menus", "debug_search_place_link"]
