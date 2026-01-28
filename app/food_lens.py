import os
import base64
from io import BytesIO
from pathlib import Path
from typing import Dict, Any

from PIL import Image
from openai import OpenAI
from pydantic import BaseModel, Field


# -------------------------
# Env (컨테이너/서버 시작 시점에 바로 검증)
# -------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

client = OpenAI(api_key=OPENAI_API_KEY)


# -------------------------
# Output Schema (GPT 응답 강제)
# -------------------------
class Nutrition(BaseModel):
    serving_description: str = Field(
        description="영양성분 기준 1회 제공량 설명 (예: 일반적인 1인분 약 300g)"
    )
    calories_kcal: int = Field(description="대략적인 칼로리(kcal)")
    carbs_g: float = Field(description="대략적인 탄수화물(g)")
    protein_g: float = Field(description="대략적인 단백질(g)")
    fat_g: float = Field(description="대략적인 지방(g)")
    note: str = Field(description="추정치임을 감안한 오차 원인 (양/조리법/소스 등)")


class GPTOnlyDecision(BaseModel):
    chosen_food: str = Field(description="최종 음식명 (한국어)")
    reason: str = Field(description="2~6문장. 이미지에서 보이는 근거 중심 설명")
    nutrition: Nutrition


# -------------------------
# Image Utils
# -------------------------
def _to_png_bytes(image_path: str) -> bytes:
    """
    이미지를 RGB PNG bytes로 변환
    (Vision 모델 입력 안정성을 위해 포맷 통일)
    """
    img = Image.open(image_path).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _b64_data_url(png_bytes: bytes) -> str:
    """
    PNG bytes → base64 data URL
    """
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


# -------------------------
# GPT-4.1-mini Vision (단독 판단)
# -------------------------
def decide_food_gpt_only(image_path: str) -> Dict[str, Any]:
    """
    Clarifai 없이 GPT-4.1-mini가 이미지를 직접 보고
    음식명 + 근거 + 대략적인 영양정보를 추론한다.
    """
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")

    data_url = _b64_data_url(_to_png_bytes(str(p)))

    instruction = """
너는 음식 분류 AI다. 반드시 한국어로만 답해라.

[규칙]
1) 이미지를 직접 보고 가장 적절한 음식명을 하나로 판단한다.
2) reason에는 이미지에서 보이는 근거를 중심으로 2~6문장으로 설명한다.
3) nutrition은 일반적인 1인분 기준의 대략적인 추정치로 제시한다.
4) 조리 방식, 양, 소스 등에 따라 오차가 있음을 note에 명시한다.
5) 반드시 지정된 JSON 스키마만을 출력한다.
""".strip()

    resp = client.responses.parse(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "너는 한국어로만 답하고, "
                    "지정된 JSON 스키마로만 출력하는 "
                    "신중하고 보수적인 음식 인식 AI다."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
        text_format=GPTOnlyDecision,
    )

    decision: GPTOnlyDecision = resp.output_parsed

    # 서버측 최소 안전장치
    if not decision.chosen_food:
        raise ValueError("GPT 응답에 chosen_food가 비어있습니다.")

    return {
        "decision": decision.model_dump()
    }


# -------------------------
# Local Test
# -------------------------
if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    img_path = base_dir / "img" / "chicken.jpg"

    result = decide_food_gpt_only(str(img_path))
    print(result)
