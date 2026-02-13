from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import LocationProfile, Restaurant, RestaurantSnapshot, User
from app.node.external_clients import kakao_geocode_address, kakao_search_restaurants
from app.schemas import AgentState


def _append_error(state: AgentState, message: str) -> None:
    errors = state.get("errors") or []
    errors.append(message)
    state["errors"] = errors


def _upsert_location_profile(db: Session, user_number: int, label: str, address_text: str, lat: float, lng: float) -> LocationProfile:
    profile = (
        db.query(LocationProfile)
        .filter(LocationProfile.user_number == user_number, LocationProfile.label == label)
        .one_or_none()
    )
    if profile is None:
        profile = LocationProfile(
            user_number=user_number,
            label=label,
            address_text=address_text,
            lat=lat,
            lng=lng,
        )
        db.add(profile)
        db.flush()
        return profile

    profile.address_text = address_text
    profile.lat = lat
    profile.lng = lng
    db.flush()
    return profile


def _upsert_restaurant(db: Session, place: dict) -> Restaurant:
    source_place_id = str(place.get("id") or "")
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.source == "kakao", Restaurant.source_place_id == source_place_id)
        .one_or_none()
    )

    values = {
        "source": "kakao",
        "source_place_id": source_place_id,
        "name": place.get("place_name") or "",
        "category": place.get("category_name"),
        "address_text": place.get("road_address_name") or place.get("address_name"),
        "lat": float(place["y"]) if place.get("y") else None,
        "lng": float(place["x"]) if place.get("x") else None,
        "phone": place.get("phone"),
        "place_url": place.get("place_url"),
    }

    if restaurant is None:
        restaurant = Restaurant(**values)
        db.add(restaurant)
        db.flush()
        return restaurant

    for key, value in values.items():
        setattr(restaurant, key, value)
    db.flush()
    return restaurant


def _upsert_snapshot(db: Session, location_profile_id: int, restaurant_id: int, distance_m: float | None) -> None:
    snapshot = (
        db.query(RestaurantSnapshot)
        .filter(
            RestaurantSnapshot.location_profile_id == location_profile_id,
            RestaurantSnapshot.restaurant_id == restaurant_id,
        )
        .one_or_none()
    )

    if snapshot is None:
        snapshot = RestaurantSnapshot(
            location_profile_id=location_profile_id,
            restaurant_id=restaurant_id,
            distance_m=distance_m,
        )
        db.add(snapshot)
        db.flush()
        return

    snapshot.distance_m = distance_m
    db.flush()


def _safe_distance(place: dict) -> float:
    try:
        value = place.get("distance")
        return float(value) if value is not None else 999999.0
    except Exception:
        return 999999.0


def node_a_fetch_restaurants(state: AgentState) -> AgentState:
    """Node A: GPS 좌표(우선) 또는 주소 기반 음식점 마스터 + 스냅샷 수집."""
    max_restaurants = 5
    user_number = int(state["user_number"])
    label = (state.get("label") or "current").strip().lower()
    if not label:
        label = "current"
    address_text = (state.get("address_text") or "").strip()
    state_lat = state.get("lat")
    state_lng = state.get("lng")
    radius_m = int(state.get("radius_m") or 500)
    if (state_lat is None or state_lng is None) and not address_text:
        _append_error(state, "lat/lng 또는 address_text 중 하나는 필요합니다.")
        return state

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.user_number == user_number).one_or_none()
        if user is None:
            _append_error(state, f"존재하지 않는 user_number: {user_number}")
            return state

        if state_lat is not None and state_lng is not None:
            lat = float(state_lat)
            lng = float(state_lng)
        else:
            coords = kakao_geocode_address(address_text)
            if not coords:
                _append_error(state, "주소를 좌표로 변환하지 못했습니다.")
                return state
            lat = coords["lat"]
            lng = coords["lng"]

        if not address_text:
            address_text = f"gps:{lat:.6f},{lng:.6f}"

        profile = _upsert_location_profile(db, user_number, label, address_text, lat, lng)
        places = kakao_search_restaurants(lat, lng, radius_m)
        selected_places = sorted(places, key=_safe_distance)[:max_restaurants]

        print(f"[A] user_number={user_number} label={label} coords=({lat},{lng}) radius_m={radius_m}")
        print(f"[A] kakao_places_count={len(places)} max_restaurants={max_restaurants}")
        restaurant_ids: List[int] = []
        sampled_names: List[str] = []
        for place in selected_places:
            if not place.get("id") or not place.get("place_name"):
                continue
            restaurant = _upsert_restaurant(db, place)
            try:
                distance_m = float(place.get("distance")) if place.get("distance") else None
            except Exception:
                distance_m = None
            _upsert_snapshot(db, profile.location_id, restaurant.restaurant_id, distance_m)
            restaurant_ids.append(restaurant.restaurant_id)
            if len(sampled_names) < 5:
                sampled_names.append(place.get("place_name"))

        db.commit()
        print(f"[A] upserted_restaurants={len(set(restaurant_ids))} sample_names={sampled_names}")

        state["location_profile_id"] = profile.location_id
        state["lat"] = lat
        state["lng"] = lng
        state["restaurant_ids"] = list(dict.fromkeys(restaurant_ids))
        return state
    except Exception as exc:
        db.rollback()
        _append_error(state, f"A_fetch_restaurants 실패: {type(exc).__name__}: {exc}")
        return state
    finally:
        db.close()
