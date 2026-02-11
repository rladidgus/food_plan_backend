"""Node A: fetch restaurants within radius for a given address."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import logging
import math
import re

from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models import LocationProfile, Restaurant, RestaurantSnapshot
from app.services import naver_api


State = Dict[str, Any]

logger = logging.getLogger(__name__)

NAVER_SOURCE = "naver"
DEFAULT_RADIUS_M = 500
PAGE_SIZE = 15
MAX_PAGES = 3


def _clean_title(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", "", str(value))
    text = text.replace("&amp;", "&").strip()
    return text or None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_radius(radius_m: Any) -> int:
    try:
        radius = int(radius_m)
    except (TypeError, ValueError):
        return DEFAULT_RADIUS_M
    return max(50, min(radius, 5000))


def _haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    # Note: assumes lat/lng are WGS84 decimal degrees.
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _update_if_present(model: Any, **values: Any) -> None:
    for key, value in values.items():
        if value is not None:
            setattr(model, key, value)


def _extract_source_place_id(item: Dict[str, Any]) -> Optional[str]:
    return item.get("id") or item.get("link")


def _extract_distance_m(item: Dict[str, Any], lat: float, lng: float) -> Optional[float]:
    distance = _safe_float(item.get("distance"))
    if distance is not None:
        return distance
    item_lat = _safe_float(item.get("y") or item.get("lat"))
    item_lng = _safe_float(item.get("x") or item.get("lng"))
    if item_lat is None or item_lng is None:
        return None
    return _haversine_distance_m(lat, lng, item_lat, item_lng)


def node_a_fetch_restaurants(state: State) -> State:
    """A: 주소 기준 500m 음식점 리스트 추출

    Expected input in state:
    - address_text: str
    - user_number: int
    - label: str (home/work)
    - radius_m: int (optional, default 500)

    Expected output in state:
    - restaurant_ids: list[int]
    """
    errors: List[str] = state.setdefault("errors", [])
    restaurant_ids: List[int] = []

    user_number = state.get("user_number")
    label = state.get("label")
    address_text = state.get("address_text")
    radius_m = _normalize_radius(state.get("radius_m", DEFAULT_RADIUS_M))

    if not user_number or not label or not address_text:
        errors.append("필수 입력값(user_number, label, address_text)이 누락되었습니다.")
        state["restaurant_ids"] = restaurant_ids
        return state

    try:
        geo = naver_api.geocode_address(str(address_text).strip())
    except Exception as exc:
        logger.exception("Naver geocode failed: %s", exc)
        errors.append("Naver Geocoding API 실패")
        state["restaurant_ids"] = restaurant_ids
        return state

    lat = _safe_float((geo or {}).get("lat"))
    lng = _safe_float((geo or {}).get("lng"))
    if lat is None or lng is None:
        errors.append("주소 좌표 변환 실패")
        state["restaurant_ids"] = restaurant_ids
        return state

    state["lat"] = lat
    state["lng"] = lng

    db = SessionLocal()
    try:
        profile = (
            db.query(LocationProfile)
            .filter(LocationProfile.user_number == user_number, LocationProfile.label == label)
            .one_or_none()
        )
        if profile is None:
            profile = LocationProfile(
                user_number=user_number,
                label=label,
                address_text=str(address_text).strip(),
                lat=lat,
                lng=lng,
            )
            db.add(profile)
        else:
            profile.address_text = str(address_text).strip()
            profile.lat = lat
            profile.lng = lng
        db.commit()
        db.refresh(profile)
        state["location_profile_id"] = profile.location_id
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("LocationProfile upsert failed: %s", exc)
        errors.append("location_profiles 저장 실패")
        state["restaurant_ids"] = restaurant_ids
        db.close()
        return state

    try:
        items = naver_api.search_restaurants(
            lat=lat,
            lng=lng,
            radius_m=radius_m,
            page_size=PAGE_SIZE,
            max_pages=MAX_PAGES,
        )
    except Exception as exc:
        logger.exception("Naver local search failed: %s", exc)
        errors.append("Naver Local Search API 실패")
        state["restaurant_ids"] = restaurant_ids
        db.close()
        return state

    if not items:
        state["restaurant_ids"] = restaurant_ids
        db.close()
        return state

    seen: set[str] = set()
    for item in items:
        source_place_id = _extract_source_place_id(item)
        if not source_place_id or source_place_id in seen:
            continue
        seen.add(source_place_id)

        name = _clean_title(item.get("name") or item.get("place_name"))
        if not name:
            continue

        category = item.get("category")
        address = item.get("road_address") or item.get("address")
        item_lat = _safe_float(item.get("y") or item.get("lat"))
        item_lng = _safe_float(item.get("x") or item.get("lng"))
        phone = item.get("tel")
        place_url = item.get("link")
        distance_m = _extract_distance_m(item, lat, lng)
        if distance_m is not None and distance_m > radius_m:
            continue

        restaurant = (
            db.query(Restaurant)
            .filter(Restaurant.source == NAVER_SOURCE, Restaurant.source_place_id == source_place_id)
            .one_or_none()
        )
        if restaurant is None:
            restaurant = Restaurant(
                source=NAVER_SOURCE,
                source_place_id=source_place_id,
                name=name,
                category=category,
                address_text=address,
                lat=item_lat,
                lng=item_lng,
                phone=phone,
                place_url=place_url,
            )
            db.add(restaurant)
        else:
            _update_if_present(
                restaurant,
                name=name,
                category=category,
                address_text=address,
                lat=item_lat,
                lng=item_lng,
                phone=phone,
                place_url=place_url,
            )

        db.flush()

        snapshot = (
            db.query(RestaurantSnapshot)
            .filter(
                RestaurantSnapshot.location_profile_id == profile.location_id,
                RestaurantSnapshot.restaurant_id == restaurant.restaurant_id,
            )
            .one_or_none()
        )
        if snapshot is None:
            snapshot = RestaurantSnapshot(
                location_profile_id=profile.location_id,
                restaurant_id=restaurant.restaurant_id,
                distance_m=distance_m,
            )
            db.add(snapshot)
        else:
            snapshot.distance_m = distance_m

        restaurant_ids.append(restaurant.restaurant_id)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Restaurant upsert failed: %s", exc)
        errors.append("restaurants 저장 실패")
        restaurant_ids = []
    finally:
        db.close()

    state["restaurant_ids"] = restaurant_ids
    return state


__all__ = ["node_a_fetch_restaurants"]
