"""管沟巡检接口：维护管沟段，覆盖疏排积水、更换盖板、强制通风等动作。

风险等级相关的列（风险等级/风险说明）由统一判定模块产出，接口层只读不判。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.trench import RISK_LEVELS, TrenchService

router = APIRouter(prefix="/api/trench", tags=["管沟巡检"])

service = TrenchService()

LIST_FIELDS = ["管沟编号", "管沟位置", "沟内管线", "积水情况", "盖板完好", "气体浓度", "巡检日期", "风险等级", "风险说明", "管沟状态"]
STATUSES = ["正常", "积水", "盖板破损", "气体积聚"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按管沟编号或位置检索"),
    status: str | None = Query(default=None, description="正常、积水、盖板破损、气体积聚"),
    risk_level: str | None = Query(default=None, description="高风险、中风险、低风险"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按编号、状态与风险等级过滤管沟列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if risk_level is not None and risk_level not in RISK_LEVELS:
        raise HTTPException(status_code=400, detail=f"风险等级仅支持：{'、'.join(RISK_LEVELS)}")
    items, total = service.list_entries(
        keyword=keyword, status=status, risk_level=risk_level, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/risk-summary")
def risk_summary() -> dict[str, int]:
    """管沟风险分布：与列表、明细、导出共用同一判定口径。"""
    return service.risk_summary()


@router.get("/export")
def export_entries() -> dict[str, object]:
    """导出管沟巡检清单：风险结论与列表接口完全一致，不另算一遍。"""
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
    """登记一条管沟段，缺必填字段时说明原因而不是静默丢弃；风险结论当场算出。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="管沟段已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """更新管沟编号、位置、沟内管线或积水/盖板/气体等观测字段，风险即时重算。"""
    if service.get_entry(entry_id) is None:
        return ActionResult(ok=False, message=f"管沟段 {entry_id} 不存在或已归档")
    entry, missing = service.update_entry(entry_id, payload.values)
    if missing:
        return ActionResult(ok=False, message=f"必填字段不允许清空：{'、'.join(missing)}")
    return ActionResult(ok=True, message="管沟段已更新，风险等级已重新判定", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条管沟段执行疏排积水、更换盖板、强制通风；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
