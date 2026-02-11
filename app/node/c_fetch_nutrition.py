from __future__ import annotations

import json
import os
from typing import List

from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import MenuItem, NutritionFacts, Restaurant
from app.node.external_clients import get_tavily_api_key, tavily_search
from app.schemas import AgentState, NutritionCandidate


class ParsedNutritionItem(BaseModel):
    calories_kcal: float | None = None
    carbs_g: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    sodium_mg: float | None = None
    sugar_g: float | None = None
    fiber_g: float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ParsedNutritionResponse(BaseModel):
    found: bool
    nutrition: ParsedNutritionItem | None = None


def _get_openai_client() -> OpenAI:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
    return OpenAI(api_key=api_key)


def _append_error(state: AgentState, message: str) -> None:
    errors = state.get("errors") or []
    errors.append(message)
    state["errors"] = errors


def _build_nutrition_query(state: AgentState, restaurant: Restaurant, menu: MenuItem) -> str:
    address = (state.get("address_text") or "").strip()
    lat = state.get("lat")
    lng = state.get("lng")
    parts = [restaurant.name, menu.name, "영양성분", "칼로리", "탄수화물", "단백질", "지방"]
    if address:
        parts.append(f"{address} 근처")
    if lat is not None and lng is not None:
        parts.append(f"좌표({lat},{lng})")
    return " ".join(parts)


def _search_nutrition_with_gpt(state: AgentState, restaurant: Restaurant, menu: MenuItem) -> NutritionCandidate | None:
    query = _build_nutrition_query(state, restaurant, menu)
    tavily_key = get_tavily_api_key()
    results = tavily_search(query=query, max_results=6, api_key=tavily_key)
    print(f"[C] menu={menu.menu_id}:{menu.name} query={query}")
    print(f"[C] tavily_results_count={len(results)}")
    if not results:
        return None

    condensed: List[dict] = []
    for row in results[:8]:
        condensed.append(
            {
                "title": row.get("title"),
                "url": row.get("url"),
                "content": row.get("content"),
            }
        )

    payload = {
        "restaurant_name": restaurant.name,
        "restaurant_category": restaurant.category,
        "menu_name": menu.name,
        "menu_price": menu.price,
        "query": query,
        "search_results": condensed,
    }

    client = _get_openai_client()
    resp = client.responses.parse(
        model="gpt-4.1-mini",
        temperature=0.1,
        input=[
            {
                "role": "system",
                "content": (
                    "너는 영양정보 추출기다. 검색 결과에서 근거가 있는 숫자만 추출한다. "
                    "근거가 부족하면 found=false로 응답한다. 반드시 JSON 스키마만 출력한다."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        text_format=ParsedNutritionResponse,
    )

    parsed: ParsedNutritionResponse = resp.output_parsed
    if not parsed or not parsed.found or parsed.nutrition is None:
        print(f"[C] parsed_found=False menu_id={menu.menu_id}")
        return None

    first_url = results[0].get("url")
    candidate = {
        "calories_kcal": parsed.nutrition.calories_kcal,
        "carbs_g": parsed.nutrition.carbs_g,
        "protein_g": parsed.nutrition.protein_g,
        "fat_g": parsed.nutrition.fat_g,
        "sodium_mg": parsed.nutrition.sodium_mg,
        "sugar_g": parsed.nutrition.sugar_g,
        "fiber_g": parsed.nutrition.fiber_g,
        "source_type": "search",
        "source_ref": first_url,
        "confidence": float(parsed.nutrition.confidence),
    }
    print(f"[C] parsed_candidate menu_id={menu.menu_id} value={candidate}")
    return candidate


def _upsert_nutrition(db: Session, menu_item_id: int, candidate: NutritionCandidate) -> NutritionFacts:
    nutrition = (
        db.query(NutritionFacts)
        .filter(NutritionFacts.menu_item_id == menu_item_id)
        .order_by(NutritionFacts.nutrition_id.desc())
        .first()
    )

    if nutrition is None:
        nutrition = NutritionFacts(menu_item_id=menu_item_id, source_type=candidate.get("source_type") or "search")
        db.add(nutrition)

    nutrition.calories_kcal = candidate.get("calories_kcal")
    nutrition.carbs_g = candidate.get("carbs_g")
    nutrition.protein_g = candidate.get("protein_g")
    nutrition.fat_g = candidate.get("fat_g")
    nutrition.sodium_mg = candidate.get("sodium_mg")
    nutrition.sugar_g = candidate.get("sugar_g")
    nutrition.fiber_g = candidate.get("fiber_g")
    nutrition.source_type = candidate.get("source_type") or "search"
    nutrition.source_ref = candidate.get("source_ref")
    nutrition.confidence = candidate.get("confidence")
    db.flush()
    return nutrition


def node_c_fetch_nutrition(state: AgentState) -> AgentState:
    """Node C: 위치 맥락 + Tavily 검색 결과를 GPT로 파싱해 nutrition_facts upsert."""
    menu_item_ids = [int(x) for x in (state.get("menu_item_ids") or [])]
    if not menu_item_ids:
        _append_error(state, "menu_item_ids가 비어 있어 Node C를 건너뜁니다.")
        state["nutrition_ids"] = []
        return state

    db: Session = SessionLocal()
    try:
        rows = (
            db.query(MenuItem, Restaurant)
            .join(Restaurant, Restaurant.restaurant_id == MenuItem.restaurant_id)
            .filter(MenuItem.menu_id.in_(menu_item_ids))
            .all()
        )

        nutrition_ids: List[int] = []
        for menu, restaurant in rows:
            candidate = _search_nutrition_with_gpt(state, restaurant, menu)
            if candidate is None:
                _append_error(state, f"C_fetch_nutrition: 영양 추출 실패 menu_id={menu.menu_id}")
                continue

            nutrition = _upsert_nutrition(db, menu.menu_id, candidate)
            nutrition_ids.append(nutrition.nutrition_id)

        db.commit()
        state["nutrition_ids"] = sorted(set(nutrition_ids))
        return state
    except Exception as exc:
        db.rollback()
        _append_error(state, f"C_fetch_nutrition 실패: {type(exc).__name__}: {exc}")
        state["nutrition_ids"] = []
        return state
    finally:
        db.close()
