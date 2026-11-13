"""血缘图谱业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.impact import impact_service
from app.store import store

MODULE = "lineage"
REQUIRED_FIELDS = ["节点编号", "节点名称", "节点类型"]
STATUS_ORDER = ["草稿", "待审批", "已生效", "已废弃"]
ACTION_RULES = {"提交审批": "待审批", "发布节点": "已生效", "废弃节点": "已废弃"}
NEGATIVE_ACTIONS = []


class LineageService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("节点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_mapping(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记节点字段映射的待生效修改：当前映射不变，先挂为待生效草稿，
        随后必须经过影响分析与逐项确认才能随发布落版。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"血缘节点 {entry_id} 不存在或已归档"
        new_mapping = str(values.get("字段映射") or "").strip()
        if not new_mapping:
            return None, "字段映射不能为空"
        if new_mapping == str(entry.get("字段映射", "") or "").strip():
            return None, "提交的字段映射与当前生效映射一致，没有变更"
        entry["待生效映射"] = new_mapping
        if values.get("图谱版本"):
            entry["待生效版本"] = str(values["图谱版本"]).strip()
        return entry, "待生效映射已登记，请发起影响分析后再发布节点"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"血缘节点 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于血缘图谱可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 发布前必须先关闭影响批次：未确认的影响项不能随发布直接关闭。
        if action == "发布节点":
            open_batch = impact_service.open_batch_for_node(entry_id)
            if open_batch is not None:
                return (
                    None,
                    f"节点存在未关闭的影响批次 {open_batch.get('批次编号')}，"
                    "请先逐项确认影响范围并关闭批次后再发布",
                )
            pending_mapping = str(entry.get("待生效映射", "") or "").strip()
            if pending_mapping:
                return None, "节点存在尚未完成影响分析的待生效映射，请先发起影响分析"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"血缘节点已{action}"
