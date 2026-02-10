"""Menu scraping service."""

from __future__ import annotations

import json
import re
from typing import Dict, Any, List, Optional

import requests


def scrape_menus(place_url: str) -> List[Dict[str, Any]]:
    """음식점 URL에서 메뉴를 스크래핑 (best-effort).

    현재는 JSON-LD 기반 메뉴 정보만 파싱한다.
    """
    if not place_url:
        return []
    try:
        response = requests.get(
            place_url,
            timeout=8,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
        )
        if response.status_code != 200:
            return []
        html = response.text
    except requests.RequestException:
        return []

    menus: List[Dict[str, Any]] = []
    for raw in _extract_jsonld(html):
        menus.extend(_extract_menus_from_jsonld(raw))
    return menus


def _extract_jsonld(html: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.findall(html):
        text = match.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    results.append(item)
        elif isinstance(payload, dict):
            results.append(payload)
    return results


def _extract_menus_from_jsonld(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    menus: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_type = node.get("@type")
            if node_type == "MenuItem":
                menu = _menu_from_menuitem(node)
                if menu:
                    menus.append(menu)
            if "menu" in node:
                walk(node["menu"])
            if "hasMenu" in node:
                walk(node["hasMenu"])
            if "hasMenuSection" in node:
                walk(node["hasMenuSection"])
            if "itemListElement" in node:
                walk(node["itemListElement"])
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return menus


def _menu_from_menuitem(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    description = item.get("description")
    price = None
    offers = item.get("offers")
    if isinstance(offers, dict):
        price = _parse_price(offers.get("price"))
    elif isinstance(offers, list) and offers:
        if isinstance(offers[0], dict):
            price = _parse_price(offers[0].get("price"))
    return {
        "name": name,
        "description": description,
        "price": price,
        "source_url": None,
    }


def _parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        digits = re.sub(r"[^0-9.]", "", value)
        try:
            return float(digits)
        except ValueError:
            return None
    return None


__all__ = ["scrape_menus"]
