from __future__ import annotations

from typing import Dict, Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


Gender = Literal["M", "F"]


# -----------------------------
# 1) Input / Output Schemas
# -----------------------------
class InbodyInput(BaseModel):
    gender: Gender = Field(description="M(남) 또는 F(여)")
    height_cm: float = Field(gt=50, lt=250, description="키(cm)")
    weight_kg: float = Field(gt=10, lt=400, description="체중(kg)")

    bmi: Optional[float] = Field(default=None, gt=5, lt=80, description="BMI(없으면 자동 계산)")
    body_fat_kg: float = Field(ge=0, lt=200, description="체지방량(kg)")
    body_fat_pct: float = Field(gt=0, lt=80, description="체지방률(%)")
    skeletal_muscle_kg: float = Field(ge=0, lt=200, description="골격근량(kg)")
    bmr_kcal: Optional[float] = Field(default=None, ge=500, lt=5000, description="기초대사량(kcal), optional")

    @model_validator(mode="after")
    def fill_bmi_if_missing(self):
        if self.bmi is None:
            h_m = self.height_cm / 100.0
            self.bmi = self.weight_kg / (h_m ** 2)
        return self


Stage1 = Literal["마름", "표준", "비만"]

Stage2 = Literal[
    # stage1=마름
    "초저체중(위험)", "마름", "마른비만",
    # stage1=표준
    "표준", "표준저근육", "근육형(표준)",
    # stage1=비만
    "비만(지방형)", "근감소성비만", "근육형비만",
]

class BodyTypeResult(BaseModel):
    stage1: Stage1
    stage2: Stage2
    metrics: Dict[str, float]
    reason: str


# -----------------------------
# 2) Thresholds
# -----------------------------
def thresholds(gender: Gender) -> Dict[str, Any]:
    """
    운영용 현실 기준 (룰 기반)
    - BMI: 아시아 기준 정상 18.5~22.9, 비만 25+
    - 체지방률:
        남: 정상 <=20, 경계 20~25, 비만 >=25
        여: 정상 <=28, 경계 28~33, 비만 >=33
    - 근육지표:
        SMM_ratio = SMM / weight
        남: 정상 하한 ~0.40, 여: ~0.35
    - FFMI:
        남: 저근육 <17, 근육형 기준 >=19, 고근육 >=22
        여: 저근육 <15, 근육형 기준 >=17, 고근육 >=20
    """
    if gender == "M":
        return dict(
            bf_normal_max=20.0,
            bf_obese_min=25.0,
            bf_high_min=20.0,

            smm_low_max=0.40,      # 이하면 근육 부족으로 간주(대략)
            smm_high_min=0.45,     # 이하면 보통, 이상이면 근육 충분(선택적)

            ffmi_low=17.0,
            ffmi_muscular=19.0,
            ffmi_very_muscular=22.0,

            skinnyfat_bf_min=20.0,
            skinnyfat_smm_max=0.40,

            severe_under_bmi=17.0,
        )
    else:
        return dict(
            bf_normal_max=28.0,
            bf_obese_min=33.0,
            bf_high_min=28.0,

            smm_low_max=0.35,
            smm_high_min=0.40,

            ffmi_low=15.0,
            ffmi_muscular=17.0,
            ffmi_very_muscular=20.0,

            skinnyfat_bf_min=28.0,
            skinnyfat_smm_max=0.35,

            severe_under_bmi=17.0,
        )


# -----------------------------
# 3) Metrics
# -----------------------------
def compute_metrics(x: InbodyInput) -> Dict[str, float]:
    h_m = x.height_cm / 100.0
    bmi = float(x.bmi)

    ffm = x.weight_kg - x.body_fat_kg
    ffmi = ffm / (h_m ** 2) if h_m > 0 else 0.0
    smm_ratio = x.skeletal_muscle_kg / x.weight_kg if x.weight_kg > 0 else 0.0

    metrics: Dict[str, float] = {
        "bmi": round(bmi, 2),
        "body_fat_pct": round(x.body_fat_pct, 1),
        "body_fat_kg": round(x.body_fat_kg, 2),
        "skeletal_muscle_kg": round(x.skeletal_muscle_kg, 2),
        "ffm_kg": round(ffm, 2),
        "ffmi": round(ffmi, 2),
        "smm_ratio": round(smm_ratio, 3),
    }
    if x.bmr_kcal is not None and x.weight_kg > 0:
        metrics["bmr_per_kg"] = round(x.bmr_kcal / x.weight_kg, 2)

    return metrics


# -----------------------------
# 4) Stage 1 Classifier
# -----------------------------
def classify_stage1(x: InbodyInput, m: Dict[str, float]) -> Stage1:
    th = thresholds(x.gender)
    bmi = m["bmi"]
    bf = m["body_fat_pct"]

    # 1차=마름: BMI로 직관 분리(필요하면 bf 보정 가능)
    if bmi < 18.5:
        return "마름"

    # 1차=비만: BMI 비만이거나, 체지방률 비만 기준이면 비만군으로
    if bmi >= 25.0 or bf >= th["bf_obese_min"]:
        return "비만"

    # 그 외는 표준군(여기에는 BMI 23~24.9도 같이 들어갈 수 있음)
    return "표준"


# -----------------------------
# 5) Stage 2 Classifier
# -----------------------------
def classify_stage2(x: InbodyInput, m: Dict[str, float], stage1: Stage1) -> tuple[Stage2, str]:
    th = thresholds(x.gender)

    bmi = m["bmi"]
    bf = m["body_fat_pct"]
    smm_ratio = m["smm_ratio"]
    ffmi = m["ffmi"]

    # ---------- stage1 = 마름 ----------
    if stage1 == "마름":
        # 초저체중(위험)
        if bmi < th["severe_under_bmi"]:
            return (
                "초저체중(위험)",
                f"BMI({bmi:.1f})가 {th['severe_under_bmi']:.1f} 미만이라 초저체중 위험군으로 분류합니다."
            )

        # 마른비만: BMI는 낮은데 체지방률 높고 근육비율 낮음
        if bf >= th["skinnyfat_bf_min"] and smm_ratio <= th["skinnyfat_smm_max"]:
            return (
                "마른비만",
                f"BMI({bmi:.1f})는 낮지만 체지방률({bf:.1f}%)이 높고 SMM/Weight({smm_ratio:.3f})가 낮아 마른비만으로 분류합니다."
            )

        # 그 외: 마름
        return (
            "마름",
            f"BMI({bmi:.1f})가 저체중 범위이며(18.5 미만), 마른비만 조건(체지방률/근육비율)이 강하지 않아 마름으로 분류합니다."
        )

    # ---------- stage1 = 표준 ----------
    if stage1 == "표준":
        # 표준저근육(근감소 위험): 체지방률이 아주 높지 않더라도 근육이 부족하면
        if smm_ratio < th["smm_low_max"] or ffmi < th["ffmi_low"]:
            return (
                "표준저근육",
                f"BMI({bmi:.1f})는 표준군이지만 SMM/Weight({smm_ratio:.3f}) 또는 FFMI({ffmi:.1f})가 낮아 저근육(근감소 위험)으로 분류합니다."
            )

        # 근육형(표준): 체지방 정상 & FFMI가 근육형 기준 이상
        if bf <= th["bf_normal_max"] and ffmi >= th["ffmi_muscular"]:
            return (
                "근육형(표준)",
                f"체지방률({bf:.1f}%)이 정상 범위이고 FFMI({ffmi:.1f})가 기준(≥{th['ffmi_muscular']:.0f}) 이상이라 근육형(표준)으로 분류합니다."
            )

        # 그 외: 표준
        return (
            "표준",
            f"BMI({bmi:.1f})가 표준 범위에 가깝고, 체지방률({bf:.1f}%)과 근육 지표(SMM/Weight={smm_ratio:.3f}, FFMI={ffmi:.1f})가 큰 위험 신호가 없어 표준으로 분류합니다."
        )

    # ---------- stage1 = 비만 ----------
    if stage1 == "비만":
        # 근감소성비만: 체지방 비만 + 근육 부족
        if bf >= th["bf_obese_min"] and (smm_ratio < th["smm_low_max"] or ffmi < th["ffmi_low"]):
            return (
                "근감소성비만",
                f"체지방률({bf:.1f}%)이 비만 범위(≥{th['bf_obese_min']:.0f}%)이며 근육 지표(SMM/Weight={smm_ratio:.3f} 또는 FFMI={ffmi:.1f})가 낮아 근감소성비만으로 분류합니다."
            )

        # 근육형비만: 체지방 비만 + FFMI도 높음(근육도 많은 편)
        if bf >= th["bf_obese_min"] and ffmi >= th["ffmi_muscular"]:
            return (
                "근육형비만",
                f"체지방률({bf:.1f}%)이 비만 범위지만 FFMI({ffmi:.1f})도 기준(≥{th['ffmi_muscular']:.0f}) 이상이라 근육형비만으로 분류합니다."
            )

        # 그 외: 비만(지방형)
        return (
            "비만(지방형)",
            f"1차에서 비만군으로 분류되었고(BMI={bmi:.1f} 또는 체지방률={bf:.1f}%), 근감소성/근육형비만의 명확한 조건은 약해 지방형 비만으로 분류합니다."
        )

    # 방어 코드(이론상 도달 X)
    return ("표준", "판정 로직에 예외가 발생했습니다. 입력값을 확인해주세요.")


# -----------------------------
# 6) Final API
# -----------------------------
def classify_body_type(x: InbodyInput) -> BodyTypeResult:
    m = compute_metrics(x)
    s1 = classify_stage1(x, m)
    s2, reason2 = classify_stage2(x, m, s1)

    reason = (
        f"[1차] {s1} 분류. "
        f"[2차] {s2} 분류. "
        f"{reason2}"
    )

    return BodyTypeResult(stage1=s1, stage2=s2, metrics=m, reason=reason)
