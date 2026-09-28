"""管沟巡检业务规则：状态流转、字段校验、风险判定与筛选口径都收在这里。

风险等级不在本文件里计算，统一委托 :mod:`app.services.trench_risk`，
保证采集（登记/更新）、展示（列表/明细/导出/看板）、处置（动作）拿到同一份结论。
"""
from __future__ import annotations

from typing import Any

from app.services.trench_risk import (
    ACTION_FACTORS,
    apply_action,
    attach_risk,
    evaluate_trench,
    invalidate_mitigation,
)
from app.store import store

MODULE = "trench"
#: 观测字段 -> 风险因子；这些字段一有新值采集，对应因子的旧处置结论即作废。
FACTOR_FIELD_KEY = {"积水情况": "积水", "盖板完好": "盖板", "气体浓度": "气体"}
REQUIRED_FIELDS = ["管沟编号", "管沟位置", "沟内管线"]
#: 登记/更新时允许写入的观测字段；风险等级、风险判定等一律由判定模块产出。
OPTIONAL_FIELDS = ["积水情况", "盖板完好", "气体浓度", "巡检日期"]
EDITABLE_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
STATUS_ORDER = ["正常", "积水", "盖板破损", "气体积聚"]
ACTION_RULES = {"疏排积水": "正常", "更换盖板": "正常", "强制通风": "正常"}
NEGATIVE_ACTIONS = []
RISK_LEVELS = ["高风险", "中风险", "低风险"]


class TrenchService:
    # ----- 展示阶段：任何出口都先过 attach_risk，旧记录在此惰性重算 ---------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [attach_risk(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("管沟编号", "")) or keyword in str(row.get("管沟位置", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if risk_level:
            rows = [row for row in rows if row.get("风险等级") == risk_level]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return attach_risk(entry) if entry is not None else None

    def risk_summary(self) -> dict[str, int]:
        """看板/列表页统计共用：对全量记录按同一判定口径计数。"""

        counts = {level: 0 for level in RISK_LEVELS}
        for row in store.rows(MODULE):
            counts[evaluate_trench(row).level] += 1
        counts["待处理"] = counts["高风险"] + counts["中风险"]
        counts["合计"] = len(store.rows(MODULE))
        return counts

    # ----- 采集阶段：登记与更新后立刻走同一份判定，缓存结论 ---------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS:
            entry[field] = str(values.get(field)).strip()
        for field in OPTIONAL_FIELDS:
            text = str(values.get(field) or "").strip()
            if text:
                entry[field] = text
        entry["status"] = STATUS_ORDER[0]
        rows.append(entry)
        return attach_risk(entry), []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """更新管沟编号、位置、沟内管线或观测字段。

        必填字段不允许清空；观测字段变化后，对应因子的旧处置覆盖由判定模块
        按源值快照自动判废，等级随之重算，不需要调用方关心。
        """

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, []
        missing = [
            field
            for field in REQUIRED_FIELDS
            if field in values and not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        for field_name in EDITABLE_FIELDS:
            if field_name not in values:
                continue
            text = str(values.get(field_name) or "").strip()
            if not text:
                continue
            entry[field_name] = text
            factor_key = FACTOR_FIELD_KEY.get(field_name)
            if factor_key is not None:
                # 新观测已采集：该因子上一次处置的结论即刻作废，按新值重判。
                invalidate_mitigation(entry, factor_key)
        return attach_risk(entry), []

    # ----- 处置阶段：动作只写因子覆盖，等级仍由判定模块给出 ---------------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"管沟段 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于管沟巡检可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        apply_action(entry, action)
        attach_risk(entry)
        factor = ACTION_FACTORS[action][0]
        result = evaluate_trench(entry)
        return entry, f"管沟段已{action}，{factor}因子处置已记录，当前风险等级：{result.level}"
