"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import RUNTIME_MODULES, SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 影响分析按血缘节点串行化：同一节点的并发分析共用一把锁，
        # 保证不会生成两套相互冲突的影响清单。
        self._locks: dict[str, threading.RLock] = {}
        self._lock_guard = threading.Lock()

    def node_lock(self, node_code: str) -> threading.RLock:
        """返回某个血缘节点专属的可重入锁（节点之间互不阻塞）。"""
        with self._lock_guard:
            lock = self._locks.get(node_code)
            if lock is None:
                lock = threading.RLock()
                self._locks[node_code] = lock
            return lock

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in RUNTIME_MODULES)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def reset(self) -> None:
        """把全部表恢复到种子数据：仅供测试用例之间隔离状态。"""
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        with self._lock_guard:
            self._locks.clear()

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
