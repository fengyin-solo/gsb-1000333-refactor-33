"""管沟巡检接口：维护管沟段，覆盖疏排积水、更换盖板、强制通风等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.trench import TrenchService
from app.services.trench_risk import RISK_HIGH, RISK_LOW, RISK_MEDIUM, RISK_UNKNOWN

router = APIRouter(prefix="/api/trench", tags=["管沟巡检"])

service = TrenchService()

LIST_FIELDS = ["管沟编号", "管沟位置", "沟内管线", "积水情况", "盖板完好", "气体浓度", "巡检日期", "管沟状态", "风险等级", "风险分值", "风险说明"]
RISK_LEVELS = [RISK_UNKNOWN, RISK_LOW, RISK_MEDIUM, RISK_HIGH]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按管沟编号检索"),
    status: str | None = Query(default=None, description="数据缺失、低风险、中风险、高风险"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按管沟编号与风险等级过滤管沟列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in RISK_LEVELS:
        raise HTTPException(status_code=400, detail="风险等级仅支持：数据缺失、低风险、中风险、高风险")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出管沟巡检清单：与列表、详情共用同一份风险判定投影。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "trench", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条管沟段明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"管沟段 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条管沟段，缺身份字段时说明原因；现状字段缺失会得到“数据缺失”风险。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="管沟段已登记，风险等级已按统一规则判定", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """更新编号、位置、沟内管线或现状字段后立即重算风险，避免旧等级残留。"""
    entry, missing = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=f"管沟段 {entry_id} 不存在或已归档")
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="管沟段已更新，风险等级已重新判定", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条管沟段执行处置；动作先更新对应现状字段，再走统一风险判定。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
