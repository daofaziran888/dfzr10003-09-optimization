"""血缘变更影响分析接口。

覆盖：发布前发起分析、影响清单视图、逐项确认、批次关闭（落版本）、
节点版本历史。所有写动作按节点串行化，重复分析幂等。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.schemas import ActionResult, EntryPayload
from app.services.impact import impact_service

router = APIRouter(prefix="/api/impact", tags=["血缘变更影响分析"])


def _batch_payload(batch: dict[str, Any], message: str, ok: bool = True) -> ActionResult:
    items = impact_service.list_items(int(batch["id"]))
    return ActionResult(ok=ok, message=message, entry={"batch": batch, "items": items})


@router.get("/batches", response_model=dict)
def list_batches(
    status: str | None = Query(default=None, description="待处理、已完成"),
    node: str | None = Query(default=None, description="按血缘节点编号过滤，如 LINEAGE-0002"),
) -> dict[str, Any]:
    """影响清单视图：按节点/状态列出分析批次及其确认进度。"""
    rows = impact_service.list_batches(status=status, node_code=node)
    return {"total": len(rows), "items": rows}


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict[str, Any]:
    """单个影响批次详情：逐项展示上游、下游、字段映射、元数据与重跑影响。"""
    batch = impact_service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"影响批次 {batch_id} 不存在")
    return {"batch": batch, "items": impact_service.list_items(batch_id)}


@router.post("/nodes/{node_id}/analyze")
def analyze_node(node_id: int, payload: EntryPayload):
    """发布节点前计算影响范围；同节点同映射重复调用幂等，冲突中的并发分析被拒绝。

    首次分析返回 201，幂等复用既有批次返回 200，存在未关闭批次的新变更返回 409。
    """
    values = payload.values
    batch, message, code = impact_service.analyze(
        node_id,
        new_mapping=str(values.get("new_mapping") or "").strip() or None,
        new_version=str(values.get("new_version") or "").strip() or None,
        effective_time=str(values.get("effective_time") or "").strip() or None,
        remark=payload.remark,
    )
    if batch is None:
        if code == 409:
            raise HTTPException(status_code=409, detail=message)
        if code == 404:
            raise HTTPException(status_code=404, detail=message)
        return JSONResponse(
            status_code=400,
            content=ActionResult(ok=False, message=message).model_dump(),
        )
    result = _batch_payload(batch, message)
    return JSONResponse(status_code=code, content=result.model_dump())


@router.post("/items/{item_id}/confirm", response_model=ActionResult)
def confirm_item(item_id: int, payload: EntryPayload) -> ActionResult:
    """逐项确认影响范围；未确认的影响项不允许关闭批次。重复确认幂等。"""
    item, message = impact_service.confirm_item(item_id, remark=payload.remark)
    if item is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=item)


@router.post("/batches/{batch_id}/close", response_model=ActionResult)
def close_batch(batch_id: int) -> ActionResult:
    """关闭影响批次：校验全部确认后，新版本在三个业务面同步落版。"""
    batch, message = impact_service.close_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return _batch_payload(batch, message)


@router.get("/nodes/{node_code}/versions", response_model=dict)
def list_node_versions(node_code: str) -> dict[str, Any]:
    """节点版本历史：历史重跑页据此指出引用版本与当前新版本差异。"""
    return {
        "node": node_code,
        "items": impact_service.list_versions(node_code),
        "active": impact_service.active_version(node_code),
    }
