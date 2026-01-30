# app.py
# pip install -U python-dotenv requests gradio pillow

import io
import os
import re
from typing import Optional
from pathlib import Path
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from PIL import Image
from app.database import SessionLocal
from app.models import User, UserProfile, InBodyRecord, BMIHistory, UserGoal
from app.inbody import InbodyInput, classify_body_type
from app.goal_rules import infer_goal_type, estimate_target_calorie, normalize_activity_level
from app.diet_plan import create_diet_plan_record

# 1) 환경변수 로드 (.env에 UPSTAGE_API_KEY=... 넣어두면 됨)
load_dotenv(override=False)
UPSTAGE_API_KEY = os.getenv("UPSTAGE_API_KEY")

if not UPSTAGE_API_KEY:
    raise RuntimeError("UPSTAGE_API_KEY가 없습니다. .env 또는 환경변수로 설정하세요.")

# 2) Upstage OCR API (너가 쓰던 엔드포인트 유지)
UPSTAGE_OCR_URL = "https://api.upstage.ai/v1/document-digitization"
UPSTAGE_OCR_MODEL = os.getenv("UPSTAGE_OCR_MODEL", "ocr")


def upstage_ocr_from_path(image_path: str) -> str:
    """이미지 파일을 Upstage OCR로 보내고, JSON에서 text들을 모아서 반환."""
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # 파일 확장자에 따라 Content-Type을 대충 맞춰줌(엄격하진 않음)
    ext = p.suffix.lower()
    mime = "image/jpeg"
    if ext == ".png":
        mime = "image/png"

    with open(p, "rb") as f:
        content = f.read()

    return upstage_ocr_from_bytes(content, filename=p.name, mime=mime)


def upstage_ocr_from_bytes(content: bytes, filename: str, mime: str) -> str:
    """이미지 바이트를 Upstage OCR로 보내고, JSON에서 text들을 모아서 반환."""
    headers = {"Authorization": f"Bearer {UPSTAGE_API_KEY}"}
    try:
        files = {"document": (filename, content, mime)}
        data = {"model": UPSTAGE_OCR_MODEL}
        resp = requests.post(UPSTAGE_OCR_URL, headers=headers, files=files, data=data, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        detail = ""
        if e.response is not None:
            detail = f" status={e.response.status_code} body={e.response.text[:500]}"
        raise RuntimeError(f"OCR 요청 실패.{detail}") from e

    # 구조화된 응답이 있으면 순서를 유지해 수집, 없으면 전체 검색 fallback
    texts = []
    if isinstance(data, dict):
        pages = data.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict):
                    continue
                blocks = page.get("blocks")
                if isinstance(blocks, list):
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        lines = block.get("lines")
                        if isinstance(lines, list):
                            for line in lines:
                                if isinstance(line, dict):
                                    t = line.get("text")
                                    if isinstance(t, str) and t.strip():
                                        texts.append(t)
                        else:
                            t = block.get("text")
                            if isinstance(t, str) and t.strip():
                                texts.append(t)
                else:
                    t = page.get("text")
                    if isinstance(t, str) and t.strip():
                        texts.append(t)

    if not texts:
        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "text" and isinstance(v, str):
                        texts.append(v)
                    else:
                        walk(v)
            elif isinstance(x, list):
                for item in x:
                    walk(item)

        walk(data)

    out = "\n".join(t for t in texts if t.strip())
    return out.strip()

def extract_key_values(text: str) -> dict:
    """OCR 결과에서 핵심 항목과 값만 추출."""
    if not text:
        return {}

    def normalize_lines(raw_lines):
        lines = []
        buffer = ""
        for raw in raw_lines:
            line = raw.strip()
            if not line:
                continue
            # 세로 인쇄된 한 글자씩 합치기 (예: 신/장)
            if len(line) == 1:
                buffer += line
                continue
            if buffer:
                lines.append(buffer + " " + line)
                buffer = ""
            else:
                lines.append(line)
        if buffer:
            lines.append(buffer)
        return lines

    key_patterns = {
        "height": [r"키", r"신장"],
        "weight": [r"체중", r"Weight"],
        "age": [r"연령", r"Age"],
        "gender": [r"성별", r"Gender"],
        "bmi": [r"BMI", r"B\.M\.I", r"B\s*M\s*I"],
        "body_fat_mass": [r"체지방량", r"체지방\s*\(kg\)", r"Body\s*Fat\s*Mass", r"Body\s*Fat\s*Mas"],
        "body_fat_pct": [r"체지방률", r"체지방\s*율", r"체지방를"],
        "skeletal_muscle_mass": [r"골격근량", r"골격근랑", r"골격근윙"],
        "bmr": [r"기초대사량", r"기초대사랑", r"기초대사항"],
        "inbody_score": [r"인바디\s*점수", r"인바디점수", r"Score", r"Scon"],
    }

    def parse_number(raw: str):
        raw = raw.replace(",", "").strip()
        try:
            return float(raw)
        except ValueError:
            return None

    ranges = {
        "height": (120, 220),
        "weight": (30, 150),
        "age": (10, 100),
        "bmi": (10, 50),
        "body_fat_mass": (1, 80),
        "body_fat_pct": (3, 70),
        "skeletal_muscle_mass": (10, 80),
        "bmr": (800, 3000),
        "inbody_score": (1, 100),
    }

    found = {}
    lines = normalize_lines(text.splitlines())

    def pick_value_from_window(window_lines, key):
        # 단위 있는 숫자를 우선 찾고, 없으면 일반 숫자
        unit_pattern = r"([0-9]+(?:[.,][0-9]+)?)\s*(cm|kg|%|kcal)?"
        if key == "inbody_score":
            # "/100" 형태를 최우선 (예: 68 / 100 점)
            for line in window_lines:
                m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*/\s*100", line)
                if m:
                    val = parse_number(m.group(1))
                    if val is not None:
                        lo, hi = ranges.get(key, (None, None))
                        if lo is None or (lo <= val <= hi):
                            return val
            # "점" 형태 우선
            for line in window_lines:
                # "100점을 넘을 수 있습니다" 같은 explanatory text 제외
                if re.search(r"(넘을 수|종합점수|점수입니다|만점)", line):
                    continue

                m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*점", line)
                if m:
                    val = parse_number(m.group(1))
                    if val is not None:
                        lo, hi = ranges.get(key, (None, None))
                        if lo is None or (lo <= val <= hi):
                            return val
            # "인바디점수 68" 형태 보조
            for line in window_lines:
                m = re.search(r"(인바디\s*점수|인바디점수|Score|Scon)\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?)", line)
                if m:
                    val = parse_number(m.group(2))
                    if val is not None:
                        lo, hi = ranges.get(key, (None, None))
                        if lo is None or (lo <= val <= hi):
                            return val
        if key == "weight":
            # "체중 89.5" 또는 "체중 (kg) 89.5" 형태 우선
            for line in window_lines:
                if re.search(r"(체중|Weight)", line, re.IGNORECASE) and not re.search(r"(적정체중|체중조절)", line):
                    m = re.search(r"(체중|Weight)[^0-9]*([0-9]+(?:[.,][0-9]+)?)", line, re.IGNORECASE)
                    if m:
                        val = parse_number(m.group(2))
                        if val is not None:
                            lo, hi = ranges.get(key, (None, None))
                            if lo is None or (lo <= val <= hi):
                                return val
            # "체중 (kg)" 다음 줄에 값이 있는 경우
            for line in window_lines:
                if re.search(r"(체중|Weight).*kg", line, re.IGNORECASE) and not re.search(r"(적정체중|체중조절)", line):
                    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)", line)
                    if m:
                        val = parse_number(m.group(1))
                        if val is not None:
                            lo, hi = ranges.get(key, (None, None))
                            if lo is None or (lo <= val <= hi):
                                return val
        for line in window_lines:
            # (체중/지방)조절, Control 등은 측정치가 아닌 참고용 수치(범위 등)일 가능성이 높음
            if re.search(r"(Flue|Fluid|Control|조절|적정체중|표준체중)", line, re.IGNORECASE):
                continue

            matches = list(re.finditer(unit_pattern, line, re.IGNORECASE))
            if len(matches) > 3:
                # 숫자가 많아도 단위(cm, kg 등)가 명시된 값이 하나라도 있으면 유효한 데이터일 수 있음(예: 헤더)
                has_unit = any(m.group(2) for m in matches)
                if not has_unit:
                    # 단위가 없고 숫자만 많으면 그래프 눈금/축일 가능성이 높음
                    continue

            for m in matches:
                val = parse_number(m.group(1))
                if val is None:
                    continue
                lo, hi = ranges.get(key, (None, None))
                if lo is None or (lo <= val <= hi):
                    return val
        return None

    for idx, line in enumerate(lines):
        if not line:
            continue

        for key, patterns in key_patterns.items():
            if key in found:
                continue
            if any(re.search(pat, line) for pat in patterns):
                # 체중 감지 시 "체중조절", "적정체중" 등이 포함된 줄은 건너뜀
                if key == "weight":
                    if re.search(r"(체중조절|적정체중)", line):
                        continue
                    # "체중" 외에 "체수분", "근육량" 등 다른 헤더 키워드가 같이 있으면 표 헤더일 확률이 높음 -> 건너뜀
                    if re.search(r"(체수분|근육량|제지방량)", line):
                        continue

                if key == "body_fat_mass":
                    # (103-16.5) 같은 범위 표기나 Flue 같은 오검출 패턴 제외
                    if re.search(r"\([0-9.]+\s*[-=~]\s*[0-9.]+\)", line):
                        continue
                     # 체중조절, 지방조절 등의 "조절"이나 Control 제외
                    if re.search(r"(Flue|Fluid|Control|조절)", line, re.IGNORECASE):
                        continue

                # 체크박스(진단표)가 있는 줄은 키워드가 있어도 제외 (예: 체지방률 진단 섹션)
                if re.search(r"[□☑■]", line):
                    continue

                window = lines[idx : idx + 5]  # 현재 줄 + 다음 4줄

                # 성별 별도 처리 (숫자가 아닌 텍스트)
                if key == "gender":
                    val = None
                    for wline in window:
                        if re.search(r"(남성|남|Male|Man)", wline, re.IGNORECASE):
                            val = "M"
                            break
                        if re.search(r"(여성|여|Female|Woman)", wline, re.IGNORECASE):
                            val = "F"
                            break
                    if val:
                        found[key] = val
                    continue

                val = pick_value_from_window(window, key)
                if val is not None:
                    found[key] = val

    # 정수형 보정
    for int_key in ("inbody_score", "age"):
        if int_key in found:
            found[int_key] = int(round(found[int_key]))

    return found


def format_key_values(values: dict) -> str:
    labels = {
        "height": "키",
        "weight": "체중",
        "age": "나이",
        "gender": "성별",
        "bmi": "BMI",
        "body_fat_mass": "체지방량(kg)",
        "body_fat_pct": "체지방률(%)",
        "skeletal_muscle_mass": "골격근량",
        "bmr": "기초대사량",
        "inbody_score": "인바디점수",
    }
    lines = []
    for key in labels:
        if key in values:
            value = values[key]
            # 소수점 표시를 위해 불필요한 int 변환 제거 (사용자 요청: 체중 소수점까지 나오도록)
            lines.append(f"{labels[key]}: {value}")
    return "\n".join(lines).strip()


def update_user_inbody(user_number: int, values: dict) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_number == user_number).one_or_none()
        if not user:
            raise RuntimeError(f"user_number={user_number} 사용자를 찾을 수 없습니다.")

        profile = db.query(UserProfile).filter(UserProfile.user_number == user.user_number).one_or_none()
        if not profile:
            profile = UserProfile(user_number=user.user_number)
            db.add(profile)

        if "height" in values:
            profile.height = values["height"]
        if "weight" in values:
            profile.weight = values["weight"]
        if "age" in values:
            profile.age = values["age"]
        if "gender" in values:
            profile.gender = values["gender"]
        if "body_fat_pct" in values:
            profile.body_fat_percent = values["body_fat_pct"]
        if "skeletal_muscle_mass" in values:
            profile.skeletal_muscle_mass = values["skeletal_muscle_mass"]
        if "bmr" in values:
            profile.bmr = values["bmr"]

        gender_value = values.get("gender") or profile.gender
        sex: Optional[str] = None
        if gender_value:
            gender_raw = str(gender_value).strip().lower()
            if gender_raw in ("m", "male", "남", "남성"):
                sex = "M"
            elif gender_raw in ("f", "female", "여", "여성"):
                sex = "F"

        # InBodyRecord 저장 (이력 관리)
        new_record = InBodyRecord(
            user_number=user.user_number,
            measurement_date=datetime.now(timezone.utc),
            height=values.get("height"),
            weight=values.get("weight"),
            body_fat_mass=values.get("body_fat_mass"),
            body_fat_pct=values.get("body_fat_pct"),
            skeletal_muscle_mass=values.get("skeletal_muscle_mass"),
            bmr=values.get("bmr"),
            inbody_score=values.get("inbody_score"),
            source="ocr",
            # 기타 항목은 나중에 확장
        )

        required_fields = {
            "height": new_record.height,
            "weight": new_record.weight,
            "body_fat_mass": new_record.body_fat_mass,
            "body_fat_pct": new_record.body_fat_pct,
            "skeletal_muscle_mass": new_record.skeletal_muscle_mass,
        }
        if sex and all(value is not None for value in required_fields.values()):
            inbody_input = InbodyInput(
                sex=sex,
                height_cm=new_record.height,
                weight_kg=new_record.weight,
                body_fat_kg=new_record.body_fat_mass,
                body_fat_pct=new_record.body_fat_pct,
                skeletal_muscle_kg=new_record.skeletal_muscle_mass,
                bmr_kcal=new_record.bmr,
            )
            result = classify_body_type(inbody_input)
            new_record.predicted_classify = None
            new_record.classify_name = result.stage2

            goal_type = infer_goal_type(result.stage1, result.stage2)
            target_calorie = estimate_target_calorie(
                goal_type=goal_type,
                bmr_kcal=new_record.bmr,
                weight_kg=new_record.weight,
                activity_level=normalize_activity_level(profile.activity_level) if profile else None,
            )
            latest_goal = (
                db.query(UserGoal)
                .filter(UserGoal.user_number == user.user_number)
                .order_by(UserGoal.created_at.desc())
                .first()
            )
            if latest_goal:
                latest_goal.goal_type = goal_type
                latest_goal.target_calorie = target_calorie
                latest_goal.start_date = datetime.now(timezone.utc)
            else:
                latest_goal = UserGoal(
                    user_number=user.user_number,
                    id=user.id,
                    goal_type=goal_type,
                    target_calorie=target_calorie,
                    start_date=datetime.now(timezone.utc),
                )
                db.add(latest_goal)

            if profile:
                profile.goal_type = goal_type
                if target_calorie is not None:
                    create_diet_plan_record(
                        db=db,
                        user_number=user.user_number,
                        goal_type=goal_type,
                        target_calorie=target_calorie,
                    )

        db.add(new_record)

        # BMIHistory 저장
        if "bmi" in values:
            new_bmi = BMIHistory(
                user_number=user.user_number,
                bmi=values["bmi"]
            )
            db.add(new_bmi)

        db.commit()
    finally:
        db.close()


def ocr_from_camera(image: Image.Image, user_number: Optional[int] = None) -> str:
    """Gradio에서 들어온 PIL 이미지를 임시 파일로 저장 후 OCR"""
    if image is None:
        return "이미지를 업로드/촬영해 주세요."

    try:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=95)
        text = upstage_ocr_from_bytes(buf.getvalue(), filename="inbody.jpg", mime="image/jpeg")
        if not text:
            return "(텍스트를 추출하지 못했습니다. 더 밝게/가깝게/정면에서 찍어보세요.)"
        values = extract_key_values(text)
        if not values:
            return "(핵심 항목을 추출하지 못했습니다. 더 선명하게 찍어보세요.)"
        if user_number is not None:
            update_user_inbody(int(user_number), values)
        return format_key_values(values)
    except Exception as e:
        print(f"OCR 실패: {e}")
        return "OCR 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."


def build_demo():
    import gradio as gr

    return gr.Interface(
        fn=ocr_from_camera,
        inputs=[
            gr.Image(
                sources=["webcam", "upload"],  # 모바일에서 카메라/업로드 둘 다 뜸
                type="pil",
                label="인바디 사진 (카메라/업로드)",
            ),
            gr.Number(label="user_number (로그인 사용자)", precision=0),
        ],
        outputs=gr.Textbox(label="추출된 핵심 항목", lines=12),
        title="InBody OCR (Upstage)",
        description="휴대폰으로 인바디 사진을 찍어 업로드하면 핵심 항목만 추출합니다.",
    )

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="InBody OCR runner")
    parser.add_argument("--image", help="샘플 이미지 경로로 OCR 테스트 실행")
    parser.add_argument("--user-number", type=int, help="OCR 결과를 저장할 사용자 번호")
    args = parser.parse_args()

    if args.image:
        text = upstage_ocr_from_path(args.image)
        values = extract_key_values(text)
        if args.user_number and values:
            update_user_inbody(args.user_number, values)
        print(format_key_values(values))
    else:
        # 같은 Wi-Fi에서 휴대폰으로 접속하려면 0.0.0.0 로 열어야 함
        # share=True 로 바꾸면 외부에서도 접속 가능한 링크가 생성됨
        demo = build_demo()
        demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
