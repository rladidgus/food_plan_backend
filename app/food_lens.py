import os
import base64
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Any, Optional

from clarifai.client.model import Model
from PIL import Image
from openai import OpenAI
from pydantic import BaseModel, Field


# -------------------------
# Env (컨테이너/서버 시작 시점에 바로 검증)
# -------------------------
CLARIFAI_PAT = os.getenv("CLARIFAI_PAT")
if not CLARIFAI_PAT:
    raise ValueError("CLARIFAI_PAT 환경변수가 설정되지 않았습니다.")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")


# -------------------------
# Clarifai (공식 SDK) - Food model
# -------------------------
MODEL_URL = "https://clarifai.com/clarifai/main/models/general-image-recognition"
clarifai_model = Model(url=MODEL_URL, pat=CLARIFAI_PAT)


def recognize_food(image_path: str, top_k: int = 5) -> List[Dict[str, Any]]:
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")

    response = clarifai_model.predict_by_filepath(str(p), input_type="image")

    outputs = getattr(response, "outputs", None)
    if not outputs:
        raise RuntimeError(f"Clarifai response has no outputs. Raw response: {response}")

    concepts = outputs[0].data.concepts or []
    return [{"name": c.name, "confidence": float(round(c.value, 3))} for c in concepts[:top_k]]


# -------------------------
# OpenAI (공식 SDK) - Vision + 후보 참고 + (후보 밖 허용)
# -------------------------
client = OpenAI(api_key=OPENAI_API_KEY)


class Nutrition(BaseModel):
    serving_description: str = Field(description="영양성분 기준 1회 제공량 설명(예: '일반적인 1인분(약 300g) 기준')")
    calories_kcal: int = Field(description="대략적인 칼로리(kcal)")
    carbs_g: float = Field(description="대략적인 탄수화물(g)")
    protein_g: float = Field(description="대략적인 단백질(g)")
    fat_g: float = Field(description="대략적인 지방(g)")
    note: str = Field(description="추정치라는 점/오차 원인(양, 조리법, 소스 등)")


class GPTDecision(BaseModel):
    is_in_candidates: bool = Field(description="후보 리스트에 정답이 포함되어 있다고 판단하면 true, 아니면 false")
    chosen_candidate: Optional[str] = Field(default=None, description="is_in_candidates=true일 때만: 후보 name 중 하나(정확히)")
    chosen_food: str = Field(description="최종 선택 음식명. 후보 안이면 후보명 그대로, 후보 밖이면 실제 음식명(한국어) 가능")
    reason: str = Field(description="2~6문장. 이미지 근거 + 후보 confidence 비교 근거 포함")
    why_not_in_candidates: Optional[str] = Field(default=None, description="is_in_candidates=false일 때 필수: 후보가 아닌 이유")
    nutrition: Nutrition


def _to_png_bytes(image_path: str) -> bytes:
    img = Image.open(image_path).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _b64_data_url(png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def decide_food_with_gpt_vision(image_path: str, candidates: List[Dict[str, Any]]) -> GPTDecision:
    if not candidates:
        raise ValueError("candidates가 비어있습니다.")

    data_url = _b64_data_url(_to_png_bytes(image_path))
    candidate_names = [c["name"] for c in candidates]

    instruction = f"""
너는 음식 분류 AI다. 반드시 한국어로만 답해라.

너에게는 (1) 이미지, (2) 후보 리스트(이름+confidence)가 제공된다.
너는 이미지 내용을 우선으로 판단하되 후보 리스트도 참고한다.

[출력 규칙]
- 반드시 지정된 JSON 스키마를 따른다.
- 먼저 '후보 리스트 안에 정답이 존재하는지' 평가해라.

[결정 규칙]
1) 후보 안에 정답이 '충분히 있다'고 판단되면:
   - is_in_candidates=true
   - chosen_candidate는 후보 name 중 하나를 "정확히 그대로" 적어라.
   - chosen_food는 chosen_candidate와 동일하게 적어라.
   - why_not_in_candidates는 null

2) 후보가 너무 포괄적이거나(예: meat, food) 조리 형태(탕/찌개/볶음/면 등)를 반영하지 못해
   후보 안에 정답이 없다고 판단되면:
   - is_in_candidates=false
   - chosen_candidate=null
   - chosen_food는 실제 음식명을 한국어로 구체적으로 적어라. (예: 닭볶음탕/닭도리탕 등)
   - why_not_in_candidates에 왜 후보들이 이미지와 안 맞는지 구체적으로 적어라.

3) reason에는 반드시 아래를 함께 포함해라(2~6문장):
   - 이미지에서 보이는 근거
   - 후보 confidence 비교 근거(왜 높은 후보를 선택/배제했는지)

4) nutrition은 일반적인 1인분 기준의 대략치(추정치)로 제시하고,
   note에 오차 원인을 명확히 적어라.

[후보 리스트(JSON)]
{candidates}

[후보 이름 목록]
{candidate_names}
""".strip()

    resp = client.responses.parse(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": "너는 한국어로만 답하고, 지정된 JSON 스키마로만 출력하는 신중한 어시스턴트다."},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
        text_format=GPTDecision,
    )

    decision: GPTDecision = resp.output_parsed

    # 서버측 안전장치
    if decision.is_in_candidates:
        if not decision.chosen_candidate:
            raise ValueError("is_in_candidates=true인데 chosen_candidate가 비어있습니다.")
        if decision.chosen_candidate not in candidate_names:
            raise ValueError(f"chosen_candidate가 후보에 없습니다: {decision.chosen_candidate}")

        # 일관성 보정
        decision.chosen_food = decision.chosen_candidate
        decision.why_not_in_candidates = None
    else:
        if not decision.why_not_in_candidates:
            raise ValueError("is_in_candidates=false인데 why_not_in_candidates가 비어있습니다.")
        decision.chosen_candidate = None

    return decision


def recognize_and_explain(image_path: str, top_k: int = 5) -> Dict[str, Any]:
    candidates = recognize_food(image_path=image_path, top_k=top_k)
    decision = decide_food_with_gpt_vision(image_path=image_path, candidates=candidates)
    return {"candidates": candidates, "decision": decision.model_dump()}


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    img_path = base_dir / "img" / "chicken.jpg"
    print(recognize_and_explain(str(img_path), top_k=5))
