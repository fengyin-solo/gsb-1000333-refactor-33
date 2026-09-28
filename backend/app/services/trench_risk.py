"""管沟风险判定的唯一来源（single source of truth）。

采集（登记/更新）、展示（列表/明细/导出/看板）、处置（疏排积水等动作）
三个阶段都只能调用 :func:`evaluate_trench`，任何地方都不许再自行拼等级，
否则管沟编号、位置、沟内管线或积水情况一更新，各视图就会算出不一致的等级。

设计约定：

- 纯函数：输入一条管沟记录快照，输出 :class:`TrenchRiskResult`，不读写仓库；
- 输入字段全部可选：缺失/空串/无法解析都算「缺失」，按保守口径（中风险）处理，
  结论保持确定、可复现，不会抛异常；
- 临界值按区间判定（``>=`` 归高一档、``>`` 归高一档），规则随 ``RULE_VERSION`` 走；
- 每个因子都给出 ``level`` 与 ``reason``，总体结论给出触发理由，保证可解释；
- 处置动作只在因子级别留下覆盖记录（``处置覆盖``），判定逻辑仍只有这一份；
  对应因子一旦采集到新观测，处置覆盖立即作废（粘性，不可复活），避免旧处置掩盖新隐患。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

RiskLevel = Literal["高风险", "中风险", "低风险"]

# 规则升级时递增版本号；记录里缓存的旧版本结论会在读取时被惰性重算。
RULE_VERSION = "2026-09-01"

#: 等级取值顺序，数值越大越严重，聚合时直接取最大值。
LEVEL_ORDER: dict[str, int] = {"低风险": 1, "中风险": 2, "高风险": 3}

#: 总体风险等级 -> 业务含义（前端 tooltip / 导出说明共用）。
LEVEL_LABELS: dict[str, str] = {
    "高风险": "存在已确认的重大隐患，须立即处置",
    "中风险": "存在隐患或关键数据缺失，须限期核实/处置",
    "低风险": "各项指标正常，按周期巡检即可",
}

# --- 阈值（口径写死在一处，临界值归属见各解析函数的区间注释） ---------------

#: 积水深度，单位 cm：>=40 高风险；>0（且 <40）中风险；0/干涸 低风险。
WATER_HIGH_CM = 40.0

#: 气体浓度，单位 %LEL：>=25 高风险；>0（且 <25）中风险；0 低风险。
GAS_HIGH_PCT = 25.0

#: 盖板完好字段 -> 等级。取值未列出时按缺失保守处理。
COVER_LEVELS: dict[str, RiskLevel] = {
    "完好": "低风险",
    "破损": "中风险",
    "缺失": "高风险",
}

#: 沟内管线关键字 -> 附加权重等级；燃气/高压等管道出事后果更重。
PIPE_KEYWORD_LEVELS: list[tuple[tuple[str, ...], RiskLevel]] = [
    (("燃气", "天然气", "煤气"), "中风险"),
    (("高压", "蒸汽", "热力"), "中风险"),
    (("供水", "排水", "雨水", "污水", "电力", "通信"), "低风险"),
]

#: 处置动作 -> 覆盖的因子键。
ACTION_FACTORS: dict[str, tuple[str, str]] = {
    # action: (factor_key, 处置后该因子的描述)
    "疏排积水": ("积水", "已疏排，待复测积水深度"),
    "更换盖板": ("盖板", "盖板已更换，待复检"),
    "强制通风": ("气体", "已强制通风，待复测气体浓度"),
}


@dataclass(frozen=True)
class FactorResult:
    """单个风险因子的判定结果。"""

    key: str
    level: RiskLevel
    reason: str
    missing: bool = False  # True 表示源数据缺失，等级是保守兜底值


@dataclass(frozen=True)
class TrenchRiskResult:
    """一次风险判定的完整输出，三个阶段的视图都只认这个结构。"""

    level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    factors: dict[str, FactorResult] = field(default_factory=dict)
    rule_version: str = RULE_VERSION
    missing_fields: list[str] = field(default_factory=list)
    applied_mitigations: list[str] = field(default_factory=list)
    expired_mitigations: list[str] = field(default_factory=list)

    def to_snapshot(self) -> dict[str, Any]:
        """序列化成可随记录缓存的字典（旧记录并存时靠 rule_version 识别）。"""
        return {
            "level": self.level,
            "level_label": LEVEL_LABELS[self.level],
            "reasons": list(self.reasons),
            "rule_version": self.rule_version,
            "missing_fields": list(self.missing_fields),
            "applied_mitigations": list(self.applied_mitigations),
            "expired_mitigations": list(self.expired_mitigations),
            "factors": [
                {
                    "key": item.key,
                    "level": item.level,
                    "reason": item.reason,
                    "missing": item.missing,
                }
                for item in self.factors.values()
            ],
        }


def _text(value: Any) -> str:
    """统一取值口径：None / 空白 / 非字符串都归一成去空白字符串。"""

    if value is None:
        return ""
    return str(value).strip()


def _first_number(text: str) -> float | None:
    """从「35cm」「约 20 厘米」这类文本里抽出第一个数值；抽不到返回 None。"""

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:  # 理论上正则已保证，留作防御
        return None


def _factor_water(raw: str, mitigation: dict[str, Any] | None) -> FactorResult:
    """积水因子。处置覆盖未过期时以「已处置、待复测」采信，否则按实测水深判定。"""

    if mitigation and not _mitigation_expired(raw, mitigation):
        return FactorResult(
            "积水",
            str(mitigation.get("level") or "低风险"),  # 覆盖等级由处置语义决定
            str(mitigation.get("note") or "已处置"),
        )
    if not raw:
        return FactorResult("积水", "中风险", "积水情况未填报，按保守口径暂定为中风险", missing=True)
    dry_words = ("无积水", "干涸", "干燥", "无")
    if raw in dry_words:
        return FactorResult("积水", "低风险", "沟内无积水")
    depth = _first_number(raw)
    if depth is None:
        return FactorResult(
            "积水",
            "中风险",
            f"积水情况「{raw}」无法换算为水深，按保守口径暂定为中风险",
            missing=True,
        )
    # 临界口径：>=40cm 高风险；0<水深<40 中风险；==0 低风险。
    if depth >= WATER_HIGH_CM:
        return FactorResult("积水", "高风险", f"积水深度 {depth:g}cm，已达 {WATER_HIGH_CM:g}cm 高风险阈值")
    if depth > 0:
        return FactorResult("积水", "中风险", f"积水深度 {depth:g}cm，低于 {WATER_HIGH_CM:g}cm 高风险阈值")
    return FactorResult("积水", "低风险", "积水深度为 0cm")


def _factor_gas(raw: str, mitigation: dict[str, Any] | None) -> FactorResult:
    """气体因子（%LEL）。"""

    if mitigation and not _mitigation_expired(raw, mitigation):
        return FactorResult(
            "气体",
            str(mitigation.get("level") or "低风险"),
            str(mitigation.get("note") or "已处置"),
        )
    if not raw:
        return FactorResult("气体", "中风险", "气体浓度未填报，按保守口径暂定为中风险", missing=True)
    concentration = _first_number(raw)
    if concentration is None:
        return FactorResult(
            "气体",
            "中风险",
            f"气体浓度「{raw}」无法解析数值，按保守口径暂定为中风险",
            missing=True,
        )
    # 临界口径：>=25%LEL 高风险；0<浓度<25 中风险；==0 低风险。
    if concentration >= GAS_HIGH_PCT:
        return FactorResult(
            "气体", "高风险", f"气体浓度 {concentration:g}%LEL，已达 {GAS_HIGH_PCT:g}%LEL 高风险阈值"
        )
    if concentration > 0:
        return FactorResult(
            "气体", "中风险", f"气体浓度 {concentration:g}%LEL，低于 {GAS_HIGH_PCT:g}%LEL 高风险阈值"
        )
    return FactorResult("气体", "低风险", "气体浓度为 0%LEL")


def _factor_cover(raw: str, mitigation: dict[str, Any] | None) -> FactorResult:
    """盖板因子。"""

    if mitigation and not _mitigation_expired(raw, mitigation):
        return FactorResult(
            "盖板",
            str(mitigation.get("level") or "低风险"),
            str(mitigation.get("note") or "已处置"),
        )
    if not raw:
        return FactorResult("盖板", "中风险", "盖板完好情况未填报，按保守口径暂定为中风险", missing=True)
    for word, level in COVER_LEVELS.items():
        if word in raw:
            if level == "低风险":
                return FactorResult("盖板", level, "盖板完好")
            if level == "高风险":
                return FactorResult("盖板", level, "盖板缺失，沟口敞开")
            return FactorResult("盖板", level, "盖板破损，存在坠落与进水风险")
    return FactorResult(
        "盖板",
        "中风险",
        f"盖板情况「{raw}」不在可识别取值内，按保守口径暂定为中风险",
        missing=True,
    )


def _factor_pipeline(raw: str) -> FactorResult:
    """沟内管线因子：决定事故后果权重，不接受处置覆盖（换管线不属于巡检处置）。"""

    if not raw:
        return FactorResult("管线", "中风险", "沟内管线未填报，无法评估事故后果，按保守口径暂定为中风险", missing=True)
    for keywords, level in PIPE_KEYWORD_LEVELS:
        if any(word in raw for word in keywords):
            if level == "中风险":
                return FactorResult("管线", level, f"沟内含{'/'.join(keywords[:1])}等高后果管线")
            return FactorResult("管线", level, "沟内为常规市政管线")
    return FactorResult("管线", "低风险", "沟内管线未命中高后果类型，按常规管线处理")


def invalidate_mitigation(entry: dict[str, Any], factor_key: str) -> None:
    """对应因子有新观测采集时，作废旧的处置覆盖（粘性，不可复活）。

    只要采集阶段写入了该因子的新值，无论数值是否与处置前相同，处置时的
    结论都已过期——新观测必须重新判定，否则雨后水位涨回原值会被误判为已处置。
    """

    mitigations = _mitigations(entry)
    mitigation = mitigations.get(factor_key)
    if isinstance(mitigation, dict) and not mitigation.get("expired"):
        mitigation["expired"] = True


def _mitigation_expired(raw: str, mitigation: dict[str, Any]) -> bool:
    """处置覆盖是否已失效：已被标记过期（粘性），或源字段在处置后又被更新过。"""

    if mitigation.get("expired"):
        return True
    return _text(mitigation.get("source_value")) != raw


def _mitigations(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("处置覆盖")
    return value if isinstance(value, dict) else {}


def evaluate_trench(entry: dict[str, Any]) -> TrenchRiskResult:
    """对一条管沟记录做风险判定。

    输入（``entry`` 可缺字段，缺失按保守口径处理）：

    - ``积水情况``：可空/可填「无积水」或带 cm 的水深文本；
    - ``气体浓度``：可空/可填带数值的 %LEL 文本；
    - ``盖板完好``：可空/「完好、破损、缺失」；
    - ``沟内管线``：可空/管线描述，燃气热力等关键字抬高后果权重；
    - ``处置覆盖``：由处置动作写入的因子级覆盖（含处置时源值快照）。

    输出 :class:`TrenchRiskResult`：总体等级取各因子最严档；
    任一因子缺失时总体至少为中风险，并在 ``missing_fields`` 中列出待补字段。
    """

    water_raw = _text(entry.get("积水情况"))
    gas_raw = _text(entry.get("气体浓度"))
    cover_raw = _text(entry.get("盖板完好"))
    pipe_raw = _text(entry.get("沟内管线"))

    mitigations = _mitigations(entry)
    applied: list[str] = []
    expired: list[str] = []
    for key, mitigation in mitigations.items():
        if not isinstance(mitigation, dict):
            continue
        raw_now = {"积水": water_raw, "气体": gas_raw, "盖板": cover_raw}.get(key, "")
        if _mitigation_expired(raw_now, mitigation):
            # 新观测已采集（显式作废）或源值与处置快照不一致：旧处置结论作废。
            expired.append(str(mitigation.get("action") or key))
        else:
            applied.append(str(mitigation.get("action") or key))

    factors = {
        "积水": _factor_water(water_raw, mitigations.get("积水")),
        "气体": _factor_gas(gas_raw, mitigations.get("气体")),
        "盖板": _factor_cover(cover_raw, mitigations.get("盖板")),
        "管线": _factor_pipeline(pipe_raw),
    }

    missing_fields = [
        {"积水": "积水情况", "气体": "气体浓度", "盖板": "盖板完好", "管线": "沟内管线"}[key]
        for key, result in factors.items()
        if result.missing
    ]

    overall: RiskLevel = max(  # type: ignore[assignment]
        (item.level for item in factors.values()),
        key=lambda level: LEVEL_ORDER[level],
    )

    high = [item for item in factors.values() if item.level == "高风险"]
    medium = [item for item in factors.values() if item.level == "中风险"]
    reasons: list[str] = []
    if high:
        reasons.append("高风险因子：" + "；".join(f"{item.key}（{item.reason}）" for item in high))
    if medium:
        missing_medium = [item for item in medium if item.missing]
        real_medium = [item for item in medium if not item.missing]
        if real_medium:
            reasons.append("中风险因子：" + "；".join(f"{item.key}（{item.reason}）" for item in real_medium))
        if missing_medium:
            reasons.append("数据缺失，保守定为中风险：" + "、".join(item.key for item in missing_medium))
    if not reasons:
        reasons.append("积水、气体、盖板与管线各项指标均正常")
    if applied:
        reasons.append("已采纳处置记录：" + "、".join(applied))
    if expired:
        reasons.append("以下处置对应的源数据已更新，旧处置结论作废：" + "、".join(expired))

    return TrenchRiskResult(
        level=overall,
        reasons=reasons,
        factors=factors,
        missing_fields=missing_fields,
        applied_mitigations=sorted(set(applied)),
        expired_mitigations=sorted(set(expired)),
    )


def apply_action(entry: dict[str, Any], action: str) -> None:
    """把处置动作落到记录上：只写因子级覆盖，不在调用方拼等级。

    覆盖值固定为「低风险 + 待复测」——处置只代表现场动作已执行，
    下一次观测数据更新后覆盖自动失效，等级由 :func:`evaluate_trench` 重新给出。
    """

    if action not in ACTION_FACTORS:
        raise ValueError(f"未知处置动作：{action}")
    factor_key, note = ACTION_FACTORS[action]
    source_field = {"积水": "积水情况", "气体": "气体浓度", "盖板": "盖板完好"}[factor_key]
    mitigations = _mitigations(entry)
    mitigations[factor_key] = {
        "action": action,
        "note": note,
        "level": "低风险",
        "source_value": _text(entry.get(source_field)),
        "rule_version": RULE_VERSION,
    }
    entry["处置覆盖"] = mitigations


def attach_risk(entry: dict[str, Any]) -> dict[str, Any]:
    """把统一判定结果写回记录并同步派生标记，返回同一条记录。

    所有展示出口（列表/明细/导出/看板）都先经过这里，因此旧记录即使带着
    历史缓存的等级，也会在出口处被重算成当前规则下的同一份结论。
    过期的处置覆盖会被就地打上 ``expired`` 标记（粘性），避免源值改回原值时
    旧处置结论复活，也保证连续多次读取得到逐字一致的说明。
    """

    # 先固化过期状态，再判定：使本次返回的缓存（含「处置作废」理由）与后续
    # 任何一次读取逐字一致。显式作废标记或源值偏离处置快照都算过期。
    raw_by_factor = {
        "积水": _text(entry.get("积水情况")),
        "气体": _text(entry.get("气体浓度")),
        "盖板": _text(entry.get("盖板完好")),
    }
    for key, mitigation in _mitigations(entry).items():
        if not isinstance(mitigation, dict):
            continue
        if _mitigation_expired(raw_by_factor.get(key, ""), mitigation):
            mitigation["expired"] = True

    result = evaluate_trench(entry)
    entry["风险判定"] = result.to_snapshot()
    entry["风险等级"] = result.level
    entry["风险说明"] = "；".join(result.reasons)
    # pending/abnormal 与风险结论保持同一口径，看板汇总不再自己猜。
    entry["pending"] = result.level != "低风险"
    entry["abnormal"] = result.level == "高风险"
    return entry
