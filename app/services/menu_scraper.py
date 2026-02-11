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
    if "place.naver.com" in place_url:
        menus.extend(_extract_menus_from_naver(html))
    if not menus:
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


def _extract_menus_from_naver(html: str) -> List[Dict[str, Any]]:
    menus: List[Dict[str, Any]] = []
    menus.extend(_extract_from_next_data(html))
    menus.extend(_extract_from_apollo_state(html))
    # 마지막으로 일반 파서로 한번 더
    if not menus:
        menus.extend(_extract_menus_generic_json(html))
    return _dedupe_menus(menus)


def _extract_from_next_data(html: str) -> List[Dict[str, Any]]:
    pattern = re.compile(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return []
    text = match.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    return _find_menu_candidates(payload)


def _extract_from_apollo_state(html: str) -> List[Dict[str, Any]]:
    pattern = re.compile(r"__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;\s*", re.DOTALL)
    match = pattern.search(html)
    if not match:
        return []
    raw = match.group(1)
    try:
        raw = raw.replace("undefined", "null")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _find_menu_candidates(payload)


def _extract_menus_generic_json(html: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for raw in _extract_jsonld(html):
        candidates.extend(_extract_menus_from_jsonld(raw))
    return candidates


def _find_menu_candidates(payload: Any) -> List[Dict[str, Any]]:
    menus: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if _looks_like_menu(node):
                menus.append(
                    {
                        "name": node.get("name") or node.get("menuName"),
                        "description": node.get("description") or node.get("desc"),
                        "price": node.get("price") or node.get("priceInfo"),
                        "source_url": None,
                    }
                )
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return menus


def _looks_like_menu(node: Dict[str, Any]) -> bool:
    name = node.get("name") or node.get("menuName")
    if not isinstance(name, str) or not name.strip():
        return False
    if "price" in node or "priceInfo" in node:
        return True
    if "description" in node or "desc" in node:
        return True
    return False


def _dedupe_menus(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(item)
    return out


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
