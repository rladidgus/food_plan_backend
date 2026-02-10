"""LLM-based menu inference."""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from openai import OpenAI


def infer_menus(restaurant_name: str, address_text: Optional[str]) -> List[Dict[str, str]]:
    """Infer menu list using LLM.

    Returns list of dicts: {name, description, price, source_url}
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []
    client = OpenAI(api_key=api_key)
    prompt = (
        "다음 음식점의 최신 메뉴를 추정해서 JSON 배열로만 답하세요.\n"
        f"- 음식점: {restaurant_name}\n"
        f"- 주소: {address_text or ''}\n"
        "형식: [{\"name\": \"...\", \"description\": \"...\", \"price\": 9000}, ...]\n"
        "메뉴가 불확실하면 빈 배열 [] 로 답하세요."
    )
    try:
        response = client.responses.create(
            model=os.getenv("MENU_LLM_MODEL", "gpt-4o-mini"),
            input=prompt,
            temperature=0.2,
        )
        text = response.output_text
    except Exception:
        return []

    data = _extract_json_array(text)
    if not isinstance(data, list):
        return []
    results: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        results.append(
            {
                "name": name,
                "description": item.get("description"),
                "price": item.get("price"),
                "source_url": None,
            }
        )
    return results


def _extract_json_array(text: str) -> Optional[List[Dict[str, str]]]:
    if not text:
        return None
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


__all__ = ["infer_menus"]
