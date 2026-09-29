"""管沟风险的唯一判定入口。

采集（登记/更新）、展示（列表/详情/导出）和处置（动作流转）都只能调用
:func:`apply_trench_risk`，不能各自保存或解释一份风险等级，避免编号、位置、
沟内管线或积水情况更新后，旧记录里的 ``status``、``abnormal`` 与当前数据不一致。

输入：管沟记录原始字段，至少可能包含“管沟编号、管沟位置、沟内管线、积水情况、
盖板完好、气体浓度”，旧记录还可能只有 ``status``。
输出：在原记录上统一补充风险等级、风险分值、判定说明，以及供列表筛选和看板
使用的 ``status`` / ``pending`` / ``abnormal``。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

RISK_UNKNOWN = "数据缺失"
RISK_LOW = "低风险"
RISK_MEDIUM = "中风险"
RISK_HIGH = "高风险"

RISK_ORDER = [RISK_UNKNOWN, RISK_LOW, RISK_MEDIUM, RISK_HIGH]
RISK_SCORES = {RISK_LOW: 0, RISK_MEDIUM: 40, RISK_HIGH: 70}
ACTIVE_RISKS = {RISK_MEDIUM, RISK_HIGH}

# 纳入风险判定的全部现状字段；缺少时会在说明中明确列出，而不是按正常值处理。
OBSERVATION_FIELDS = ("积水情况", "盖板完好", "气体浓度")
IDENTITY_FIELDS = ("管沟编号", "管沟位置", "沟内管线")

_DRY_WORDS = ("无积水", "无明显积水", "未积水", "不积水", "干燥", "已排干", "积水已清除", "正常")
_WATER_HIGH_WORDS = ("严重积水", "大量积水", "灌满", "水淹", "水深")
_WATER_MEDIUM_WORDS = ("有积水", "积水较浅", "渗水", "积水")
_COVER_NEGATIVE_WORDS = ("无破损", "未破损", "未见破损", "没有破损")
_COVER_HIGH_WORDS = ("严重破损", "断裂", "缺失", "坍塌", "破损")
_COVER_MEDIUM_WORDS = ("裂缝", "松动", "沉降", "翘起")
_COVER_LOW_WORDS = ("完好", "完整", "正常", "无破损")
_GAS_NEGATIVE_WORDS = ("无报警", "无泄漏", "无气体积聚", "无积聚", "未报警", "未泄漏", "未检出")
_GAS_HIGH_WORDS = ("爆炸", "严重", "报警", "超限", "超标", "危险")
_GAS_MEDIUM_WORDS = ("预警", "偏高", "气体积聚", "泄漏")
_GAS_LOW_WORDS = ("正常", "合格", "安全")
_HAZARDOUS_PIPELINE_WORDS = ("燃气", "煤气", "天然气", "热力", "蒸汽", "石油", "化工", "电力", "电缆")


@dataclass(frozen=True)
class RiskFactor:
    """单个风险因子：因子名称、等级、可展示原因和数据来源。"""

    name: str
    level: str
    reason: str
    source: str


@dataclass(frozen=True)
class TrenchRiskAssessment:
    """管沟风险判定结果。risk_score 为 None 表示现状数据不足，不能数值化。"""

    risk_level: str
    risk_score: int | None
    status: str
    pending: bool
    abnormal: bool
    risk_reason: str
    risk_basis: list[str]
    risk_factors: list[dict[str, Any]]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_number(text: str) -> float | None:
    number = ""
    for char in text:
        if char.isdigit() or char == ".":
            number += char
        elif number:
            break
    if not number or number == ".":
        return None
    try:
        return float(number)
    except ValueError:
        return None


def _water_depth_millimeters(text: str) -> float | None:
    number = _first_number(text)
    if number is None:
        return None
    lowered = text.lower()
    if "厘米" in text or "cm" in lowered:
        return number * 10
    if "毫米" in text or "mm" in lowered:
        return number
    if "米" in text and "厘米" not in text or "m" in lowered and "cm" not in lowered and "mm" not in lowered:
        return number * 1000
    # 未写单位时按现场常用的毫米记录处理。
    return number


def _water_factor(raw: str) -> tuple[RiskFactor | None, str | None]:
    field = "积水情况"
    if not raw:
        return None, f"缺少{field}"

    depth = _water_depth_millimeters(raw)
    if depth is not None:
        if depth >= 300:
            return RiskFactor(field, RISK_HIGH, f"积水深度 {depth:g}mm，达到高风险阈值 300mm", field), None
        if depth >= 100:
            return RiskFactor(field, RISK_MEDIUM, f"积水深度 {depth:g}mm，达到中风险阈值 100mm", field), None
        return RiskFactor(field, RISK_LOW, f"积水深度 {depth:g}mm，低于中风险阈值 100mm", field), None

    if any(word in raw for word in _DRY_WORDS):
        return RiskFactor(field, RISK_LOW, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _WATER_HIGH_WORDS):
        return RiskFactor(field, RISK_HIGH, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _WATER_MEDIUM_WORDS):
        return RiskFactor(field, RISK_MEDIUM, f"{field}为“{raw}”", field), None
    return None, f"{field}“{raw}”无法识别"


def _cover_factor(raw: str) -> tuple[RiskFactor | None, str | None]:
    field = "盖板完好"
    if not raw:
        return None, f"缺少{field}"

    if any(word in raw for word in _COVER_NEGATIVE_WORDS):
        return RiskFactor(field, RISK_LOW, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _COVER_HIGH_WORDS):
        return RiskFactor(field, RISK_HIGH, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _COVER_MEDIUM_WORDS):
        return RiskFactor(field, RISK_MEDIUM, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _COVER_LOW_WORDS):
        return RiskFactor(field, RISK_LOW, f"{field}为“{raw}”", field), None
    return None, f"{field}“{raw}”无法识别"


def _gas_factor(raw: str) -> tuple[RiskFactor | None, str | None]:
    field = "气体浓度"
    if not raw:
        return None, f"缺少{field}"

    if any(word in raw for word in _GAS_NEGATIVE_WORDS):
        return RiskFactor(field, RISK_LOW, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _GAS_HIGH_WORDS):
        return RiskFactor(field, RISK_HIGH, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _GAS_MEDIUM_WORDS):
        return RiskFactor(field, RISK_MEDIUM, f"{field}为“{raw}”", field), None
    if any(word in raw for word in _GAS_LOW_WORDS):
        return RiskFactor(field, RISK_LOW, f"{field}为“{raw}”", field), None

    number = _first_number(raw)
    lowered = raw.lower()
    if number is not None:
        if "lel" in lowered:
            value = number
            unit = "%LEL"
        elif "ppm" in lowered:
            # 以甲烷约 500ppm = 1%LEL 换算，25%LEL 预警、50%LEL 报警。
            value = number / 500
            unit = "ppm"
        else:
            return None, f"{field}“{raw}”缺少浓度单位"

        if value >= 50:
            return RiskFactor(field, RISK_HIGH, f"{field} {number:g}{unit}，达到高风险阈值 50%LEL", field), None
        if value >= 25:
            return RiskFactor(field, RISK_MEDIUM, f"{field} {number:g}{unit}，达到中风险阈值 25%LEL", field), None
        return RiskFactor(field, RISK_LOW, f"{field} {number:g}{unit}，低于中风险阈值 25%LEL", field), None

    return None, f"{field}“{raw}”无法识别"


def _legacy_level(status: Any) -> str | None:
    status_text = _clean(status)
    if status_status := {
        "气体积聚": RISK_HIGH,
        "盖板破损": RISK_HIGH,
        "积水": RISK_MEDIUM,
        "正常": RISK_LOW,
    }.get(status_text):
        return status_status
    if status_text in RISK_ORDER:
        return status_text
    return None


def _legacy_factor(data: Mapping[str, Any], legacy_level: str | None) -> RiskFactor | None:
    """仅在缺少全部现状字段时使用旧状态；有任一现状字段则新数据优先。"""
    if legacy_level is None or legacy_level == RISK_UNKNOWN:
        return None
    status_text = _clean(data.get("status"))
    return RiskFactor(
        "旧记录状态",
        legacy_level,
        f"缺少积水情况、盖板完好、气体浓度，沿用旧状态“{status_text}”",
        "旧状态",
    )


def evaluate_trench_risk(data: Mapping[str, Any]) -> TrenchRiskAssessment:
    """根据管沟原始字段计算风险。

    临界值按“达到阈值归入更高等级”处理（如 100mm 为中风险、300mm 为高风险）。
    任何无法识别或缺失的现状字段都会进入 ``risk_basis``；存在明确高风险因子时
    不因其他字段缺失而降级。
    """
    legacy_level = _legacy_level(data.get("status"))
    factors: list[RiskFactor] = []
    missing_messages: list[str] = []

    water, water_missing = _water_factor(_clean(data.get("积水情况")))
    cover, cover_missing = _cover_factor(_clean(data.get("盖板完好")))
    gas, gas_missing = _gas_factor(_clean(data.get("气体浓度")))
    for factor, missing in ((water, water_missing), (cover, cover_missing), (gas, gas_missing)):
        if factor is not None:
            factors.append(factor)
        if missing:
            missing_messages.append(missing)

    positive = [factor for factor in factors if factor.level in ACTIVE_RISKS]
    known = [factor for factor in factors if factor.level != RISK_UNKNOWN]
    missing_identity = [field for field in IDENTITY_FIELDS if not _clean(data.get(field))]
    level = RISK_UNKNOWN
    score: int | None = None

    legacy = _legacy_factor(data, legacy_level) if not known else None

    if positive:
        level = max((factor.level for factor in positive), key=RISK_ORDER.index)
        score = RISK_SCORES[level]
        if len(positive) > 1:
            score += 10
    elif len(known) == len(OBSERVATION_FIELDS) and all(factor.level == RISK_LOW for factor in known) and not missing_identity:
        level = RISK_LOW
        score = 0
    elif legacy is not None and not missing_identity:
        # 完全没有可解析的现状字段时，旧状态只作为旧记录的兜底结论；
        # 只要有部分新现状字段，就以新数据为准并提示补齐其余字段。
        level = legacy.level
        score = RISK_SCORES[level]
        factors = [legacy]

    hazardous_pipeline = any(word in _clean(data.get("沟内管线")) for word in _HAZARDOUS_PIPELINE_WORDS)
    if hazardous_pipeline and level in ACTIVE_RISKS:
        if level == RISK_MEDIUM:
            level = RISK_HIGH
            factors.append(RiskFactor("沟内管线", RISK_HIGH, "沟内为高后果管线，中风险隐患上调为高风险", "沟内管线"))
        else:
            factors.append(RiskFactor("沟内管线", RISK_HIGH, "沟内为高后果管线，需优先处置", "沟内管线"))
        score = min(100, (score or 0) + 10)

    basis = [factor.reason for factor in factors]
    identity_warning = ""
    if missing_identity:
        identity_warning = "缺少必填字段：" + "、".join(missing_identity)
        missing_messages = [identity_warning] + missing_messages
        basis.append(identity_warning)
    if level == RISK_UNKNOWN:
        basis.extend(missing_messages)
        reason = "现状数据不足，需补采后再判定：" + "、".join(dict.fromkeys(missing_messages or ["积水情况、盖板完好、气体浓度"]))
    elif level == RISK_LOW:
        reason = "积水、盖板与气体浓度均未见异常"
    else:
        primary = next(factor for factor in factors if factor.level == level)
        reason = primary.reason
        if missing_identity:
            reason = f"{identity_warning}；{reason}"
        extra = [factor.reason for factor in factors if factor is not primary and factor.level in ACTIVE_RISKS]
        if extra:
            reason += "；同时存在：" + "、".join(extra)

    return TrenchRiskAssessment(
        risk_level=level,
        risk_score=score,
        status=level,
        pending=level in {RISK_UNKNOWN, RISK_MEDIUM, RISK_HIGH},
        abnormal=level in ACTIVE_RISKS,
        risk_reason=reason,
        risk_basis=list(dict.fromkeys(basis)),
        risk_factors=[asdict(factor) for factor in factors],
    )


def apply_trench_risk(entry: dict[str, Any], *, mutate: bool = False) -> dict[str, Any]:
    """把统一风险结论写回管沟记录。

    ``mutate=False`` 时返回投影副本，适合列表、详情和导出，避免旧内存数据影响展示；
    ``mutate=True`` 时同时落库，供登记、更新和处置动作调用。
    """
    assessment = evaluate_trench_risk(entry)
    view = entry if mutate else dict(entry)
    view.update({
        "风险等级": assessment.risk_level,
        "风险分值": assessment.risk_score,
        "风险说明": assessment.risk_reason,
        "风险依据": assessment.risk_basis,
        "风险因子": assessment.risk_factors,
        "管沟状态": assessment.risk_level,
        "status": assessment.status,
        "pending": assessment.pending,
        "abnormal": assessment.abnormal,
    })
    return view
