"""管沟巡检业务规则：原始字段采集、统一风险判定与处置流转。"""
from __future__ import annotations

from typing import Any

from app.services.trench_risk import apply_trench_risk
from app.store import store

MODULE = "trench"
REQUIRED_FIELDS = ["管沟编号", "管沟位置", "沟内管线"]
COLLECT_FIELDS = REQUIRED_FIELDS + ["积水情况", "盖板完好", "气体浓度", "巡检日期"]
ACTION_RESULTS = {
    "疏排积水": ("积水情况", "无积水"),
    "更换盖板": ("盖板完好", "完好"),
    "强制通风": ("气体浓度", "正常"),
}


class TrenchService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._present(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("管沟编号", ""))]
        if status:
            rows = [row for row in rows if row.get("风险等级") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._present(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(self._normalized_fields(values))
        rows.append(entry)
        return apply_trench_risk(entry, mutate=True), []

    def update_entry(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, []

        collected = self._normalized_fields(values)
        merged = {**entry, **collected}
        missing = [field for field in REQUIRED_FIELDS if not str(merged.get(field) or "").strip()]
        if missing:
            return None, missing

        entry.update(collected)
        return apply_trench_risk(entry, mutate=True), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"管沟段 {entry_id} 不存在或已归档"
        if action not in ACTION_RESULTS:
            return None, f"动作「{action}」不属于管沟巡检可执行范围"

        field, value = ACTION_RESULTS[action]
        entry[field] = value
        entry["最近处置"] = action
        apply_trench_risk(entry, mutate=True)
        return entry, f"管沟段已{action}"

    @staticmethod
    def _present(entry: dict[str, Any]) -> dict[str, Any]:
        """所有读视图共用的风险投影，不改变仓库里的原始记录。"""
        return apply_trench_risk(entry, mutate=False)

    @staticmethod
    def _normalized_fields(values: dict[str, Any]) -> dict[str, str]:
        collected: dict[str, str] = {}
        for field in COLLECT_FIELDS:
            value = values.get(field)
            if value is not None:
                collected[field] = str(value).strip()
        return collected
