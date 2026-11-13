"""历史重跑业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.impact import diff_mappings, impact_service, parse_mapping
from app.store import store

MODULE = "backfill"
REQUIRED_FIELDS = ["批次编号", "目标表名", "开始日期"]
STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
ACTION_RULES = {"启动重跑": "草稿", "暂停重跑": "运行中", "归档批次": "已暂停"}
NEGATIVE_ACTIONS = []


class BackfillService:
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
            rows = [row for row in rows if keyword in str(row.get("批次编号", ""))]
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
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for optional in ("结束日期", "并行数量", "覆盖模式", "异常区间", "关联血缘节点"):
            if optional in values:
                entry[optional] = values.get(optional)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry.setdefault("重跑状态", STATUS_ORDER[0])

        # 重跑引用必须落到具体节点版本：新批次默认钉住当前生效版本，
        # 保证之后节点再发布也不改变这批补数实际使用的映射。
        node_code = str(entry.get("关联血缘节点", "") or "").strip()
        if node_code:
            active = impact_service.active_version(node_code)
            if active is not None:
                entry["引用节点版本"] = str(active.get("版本号", ""))
                entry["引用映射快照"] = str(active.get("字段映射", ""))
            else:
                node = impact_service.find_node_by_code(node_code)
                if node is not None:
                    entry["引用节点版本"] = str(node.get("图谱版本", ""))
                    entry["引用映射快照"] = str(node.get("字段映射", ""))
        rows.append(entry)
        return entry, []

    def version_diff(self, entry_id: int) -> dict[str, Any] | None:
        """指出该批次引用版本与节点当前新版本之间的映射差异。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        node_code = str(entry.get("关联血缘节点", "") or "").strip()
        pinned_version = str(entry.get("引用节点版本", "") or "")
        active = impact_service.active_version(node_code) if node_code else None
        active_version = str(active.get("版本号", "")) if active else ""
        active_mapping = str(active.get("字段映射", "")) if active else ""
        pinned_mapping = str(entry.get("引用映射快照", "") or "")
        return {
            "批次编号": entry.get("批次编号", ""),
            "关联血缘节点": node_code,
            "引用节点版本": pinned_version,
            "引用映射": pinned_mapping,
            "当前生效版本": active_version,
            "当前生效映射": active_mapping,
            "是否落后": bool(active_version and pinned_version and active_version != pinned_version),
            "映射差异": diff_mappings(pinned_mapping, active_mapping),
            "引用映射解析": parse_mapping(pinned_mapping),
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"重跑批次 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于历史重跑可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["重跑状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"重跑批次已{action}"
