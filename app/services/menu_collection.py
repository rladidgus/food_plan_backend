"""Shared menu collection logic for Node B and APIs."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import MenuItem, Restaurant
from app.services.menu_scraper import scrape_menus
from app.services.llm_inference import infer_menus
from app.services.naver_api_client import fetch_menus, validate_menus


def normalize_menus(raw_menus: List[Dict[str, object]], source: str) -> List[Dict[str, object]]:
    normalized: List[Dict[str, object]] = []
    for item in raw_menus:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "description": item.get("description"),
                "price": item.get("price"),
                "source": source,
                "source_url": item.get("source_url"),
            }
        )
    return normalized


def upsert_menu_item(
    db: Session,
    restaurant_id: int,
    name: str,
    description: Optional[str],
    price: Optional[float],
    source: Optional[str],
    source_url: Optional[str],
) -> MenuItem:
    menu = (
        db.query(MenuItem)
        .filter(MenuItem.restaurant_id == restaurant_id, MenuItem.name == name)
        .one_or_none()
    )
    if menu is None:
        menu = MenuItem(
            restaurant_id=restaurant_id,
            name=name,
            description=description,
            price=price,
            source=source,
            source_url=source_url,
        )
        db.add(menu)
    else:
        menu.description = description or menu.description
        menu.price = price if price is not None else menu.price
        menu.source = source or menu.source
        menu.source_url = source_url or menu.source_url
    return menu


def collect_menus_for_restaurant(
    db: Session,
    restaurant: Restaurant,
    method_priority: List[str],
    llm_fallback: bool,
    naver_validation: bool,
) -> List[MenuItem]:
    menus: List[Dict[str, object]] = []

    if "SCRAPING" in method_priority and restaurant.place_url:
        menus = normalize_menus(scrape_menus(restaurant.place_url), "SCRAPING")

    if not menus and llm_fallback and "LLM" in method_priority:
        menus = normalize_menus(infer_menus(restaurant.name, restaurant.address_text), "LLM")

    if not menus and "API" in method_priority:
        menus = normalize_menus(fetch_menus(restaurant.name, restaurant.address_text), "API")

    if menus and naver_validation:
        menus = normalize_menus(
            validate_menus(restaurant.name, restaurant.address_text, menus),
            menus[0].get("source") or "SCRAPING",
        )

    if not menus:
        return []

    items: List[MenuItem] = []
    for menu in menus:
        item = upsert_menu_item(
            db,
            restaurant.restaurant_id,
            str(menu["name"]),
            menu.get("description"),
            menu.get("price"),
            menu.get("source"),
            menu.get("source_url"),
        )
        items.append(item)
    return items


def collect_menus_for_restaurants(
    db: Session,
    restaurant_ids: List[int],
    method_priority: List[str],
    llm_fallback: bool,
    naver_validation: bool,
) -> Tuple[List[int], List[Dict[str, str]]]:
    menu_item_ids: List[int] = []
    failures: List[Dict[str, str]] = []

    for restaurant_id in restaurant_ids:
        restaurant = (
            db.query(Restaurant)
            .filter(Restaurant.restaurant_id == restaurant_id)
            .one_or_none()
        )
        if restaurant is None:
            failures.append(
                {
                    "restaurant_id": str(restaurant_id),
                    "stage": "INPUT",
                    "reason": "restaurant_not_found",
                    "detail": "restaurant_id not found",
                }
            )
            continue

        try:
            items = collect_menus_for_restaurant(
                db, restaurant, method_priority, llm_fallback, naver_validation
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "restaurant_id": str(restaurant_id),
                    "stage": "COLLECT",
                    "reason": "exception",
                    "detail": str(exc),
                }
            )
            continue

        if not items:
            failures.append(
                {
                    "restaurant_id": str(restaurant_id),
                    "stage": "COLLECT",
                    "reason": "no_menu",
                    "detail": "no menu found",
                }
            )
            continue

        for item in items:
            if item.menu_id:
                menu_item_ids.append(item.menu_id)

    return menu_item_ids, failures


__all__ = [
    "collect_menus_for_restaurant",
    "collect_menus_for_restaurants",
    "normalize_menus",
    "upsert_menu_item",
]
