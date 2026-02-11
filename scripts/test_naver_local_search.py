import os
import sys

import requests

from app.services.naver_api_client import debug_search_place_link


def main() -> int:
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 필요합니다.")
        return 1

    name = os.getenv("TEST_RESTAURANT_NAME", "스타벅스")
    address = os.getenv("TEST_RESTAURANT_ADDRESS", "강남역")

    result = debug_search_place_link(name, address)
    print(result)

    # Raw top items for debugging
    query = name if not address else f"{name} {address}"
    response = requests.get(
        "https://openapi.naver.com/v1/search/local.json",
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
        params={"query": query, "display": 5},
        timeout=6,
    )
    if response.status_code == 200:
        items = (response.json() or {}).get("items") or []
        links = [item.get("link") for item in items]
        titles = [item.get("title") for item in items]
        print({"titles": titles, "links": links})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
