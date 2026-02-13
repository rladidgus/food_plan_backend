from __future__ import annotations

import json
import os
from typing import List

from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import MenuItem, Restaurant
from app.node.external_clients import get_serper_api_key, serper_search
from app.schemas import AgentState, MenuCandidate

MAX_MENUS_PER_RESTAURANT = 5


class ParsedMenuItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: float | None = None
    description: str | None = None


class ParsedMenuResponse(BaseModel):
    items: List[ParsedMenuItem]


def _get_openai_client() -> OpenAI:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
    return OpenAI(api_key=api_key)


def _append_error(state: AgentState, message: str) -> None:
    errors = state.get("errors") or []
    errors.append(message)
    state["errors"] = errors


def _build_menu_query(state: AgentState, restaurant: Restaurant) -> str:
    address = (state.get("address_text") or "").strip()
    lat = state.get("lat")
    lng = state.get("lng")
    parts = [restaurant.name, "메뉴", "가격", "네이버"]
    if address:
        parts.append(f"{address} 근처")
    if lat is not None and lng is not None:
        parts.append(f"좌표({lat},{lng})")
    return " ".join(parts)


def _parse_with_gpt(restaurant: Restaurant, query: str, search_results: List[dict]) -> List[MenuCandidate]:
    if not search_results:
        return []

    client = _get_openai_client()

    condensed = []
    for row in search_results[:8]:
        condensed.append(
            {
                "title": row.get("title"),
                "url": row.get("url"),
                "content": row.get("content"),
            }
        )

    user_payload = {
        "restaurant_name": restaurant.name,
        "restaurant_category": restaurant.category,
        "query": query,
        "search_results": condensed,
    }

    resp = client.responses.parse(
        model="gpt-4.1-mini",
        temperature=0.1,
        input=[
            {
                "role": "system",
                "content": (
                    "너는 메뉴 데이터 추출기다. 검색 결과에서 실제 메뉴명과 가격만 추출한다. "
                    "불확실하거나 광고성 문구는 제외하고, 반드시 JSON 스키마로만 응답한다."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ],
        text_format=ParsedMenuResponse,
    )

    parsed: ParsedMenuResponse = resp.output_parsed
    if not parsed or not parsed.items:
        return []

    source_url = search_results[0].get("url")
    out: List[MenuCandidate] = []
    for item in parsed.items[:MAX_MENUS_PER_RESTAURANT]:
        name = (item.name or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "price": float(item.price) if item.price is not None else None,
                "description": (item.description or "").strip() or None,
                "source_url": source_url,
            }
        )
    return out


def _extract_menu_candidates(state: AgentState, restaurant: Restaurant) -> List[MenuCandidate]:
    query = _build_menu_query(state, restaurant)
    serper_key = get_serper_api_key()
    results = serper_search(query=query, max_results=6, api_key=serper_key, naver_only=True)
    print(f"[B] restaurant={restaurant.restaurant_id}:{restaurant.name} query={query}")
    print(f"[B] serper_naver_results_count={len(results)}")
    return _parse_with_gpt(restaurant, query, results)


def _upsert_menu_item(db: Session, restaurant_id: int, candidate: MenuCandidate) -> MenuItem:
    name = (candidate.get("name") or "").strip()
    menu = (
        db.query(MenuItem)
        .filter(MenuItem.restaurant_id == restaurant_id, MenuItem.name == name)
        .order_by(MenuItem.menu_id.asc())
        .first()
    )

    if menu is None:
        menu = MenuItem(
            restaurant_id=restaurant_id,
            name=name,
            description=candidate.get("description"),
            price=candidate.get("price"),
            source="search",
            source_url=candidate.get("source_url"),
        )
        db.add(menu)
        db.flush()
        return menu

    menu.description = candidate.get("description") or menu.description
    menu.price = candidate.get("price") if candidate.get("price") is not None else menu.price
    menu.source = "search"
    menu.source_url = candidate.get("source_url") or menu.source_url
    db.flush()
    return menu


def _get_existing_menu_ids(db: Session, restaurant_id: int) -> List[int]:
    menus = (
        db.query(MenuItem)
        .filter(MenuItem.restaurant_id == restaurant_id)
        .order_by(MenuItem.menu_id.asc())
        .limit(MAX_MENUS_PER_RESTAURANT)
        .all()
    )
    return [m.menu_id for m in menus]


def node_b_fetch_menus(state: AgentState) -> AgentState:
    """Node B: DB 우선 메뉴 조회, 없으면 Serper+GPT 파싱 후 menu_items upsert."""
    restaurant_ids = [int(x) for x in (state.get("restaurant_ids") or [])]
    if not restaurant_ids:
        _append_error(state, "restaurant_ids가 비어 있어 Node B를 건너뜁니다.")
        state["menu_item_ids"] = []
        return state

    db: Session = SessionLocal()
    try:
        restaurants = (
            db.query(Restaurant)
            .filter(Restaurant.restaurant_id.in_(restaurant_ids))
            .all()
        )

        menu_item_ids: List[int] = []
        for restaurant in restaurants:
            existing_ids = _get_existing_menu_ids(db, restaurant.restaurant_id)
            if existing_ids:
                print(
                    f"[B] restaurant={restaurant.restaurant_id}:{restaurant.name} "
                    f"existing_menu_count={len(existing_ids)} -> skip search"
                )
                menu_item_ids.extend(existing_ids)
                continue

            candidates = _extract_menu_candidates(state, restaurant)
            if not candidates:
                _append_error(state, f"B_fetch_menus: 메뉴 추출 실패 restaurant_id={restaurant.restaurant_id}")
                continue
            print(f"[B] parsed_candidates_count={len(candidates)} sample={candidates[:3]}")

            inserted_count = 0
            for candidate in candidates:
                if not candidate.get("name"):
                    continue
                menu = _upsert_menu_item(db, restaurant.restaurant_id, candidate)
                menu_item_ids.append(menu.menu_id)
                inserted_count += 1
                if inserted_count >= MAX_MENUS_PER_RESTAURANT:
                    break

        db.commit()
        state["menu_item_ids"] = sorted(set(menu_item_ids))
        return state
    except Exception as exc:
        db.rollback()
        _append_error(state, f"B_fetch_menus 실패: {type(exc).__name__}: {exc}")
        state["menu_item_ids"] = []
        return state
    finally:
        db.close()
