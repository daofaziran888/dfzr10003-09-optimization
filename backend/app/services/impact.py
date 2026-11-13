"""血缘变更影响分析。

取数链路（三个业务面共用同一份结论）：

    血缘节点（字段映射/图谱版本）
        ├─ 上游/下游血缘节点（沿血缘边双向遍历）
        ├─ 字段映射差异（源字段 -> 目标字段的逐项变化）
        ├─ 元数据目录记录（按下游表名 + 目标字段名定位，写入待复核）
        └─ 历史重跑批次（按关联节点定位，旧批次钉住旧版本，新批次走新版本）

关键约束：
- 同一节点重复分析按「节点 + 新映射 + 新版本」指纹幂等，沿用同一批次结论；
- 节点级互斥锁保证并发分析不会生成两套冲突清单；
- 影响项未逐项确认前，批次不能关闭，节点不能发布；
- 映射冲突时按节点版本号、再按生效时间决定优先级。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.store import store

BATCH_MODULE = "impact_batches"
ITEM_MODULE = "impact_items"
VERSION_MODULE = "lineage_versions"
LINEAGE_MODULE = "lineage"
METADATA_MODULE = "metadata"
BACKFILL_MODULE = "backfill"

BATCH_OPEN = "待处理"
BATCH_CLOSED = "已完成"
ITEM_PENDING = "待确认"
ITEM_CONFIRMED = "已确认"

REVIEW_PENDING = "待复核"
REVIEW_DONE = "已复核"

VERSION_ACTIVE = "已生效"
VERSION_ARCHIVED = "已归档"

# 已经开始跑的批次继续由旧版本服务；草稿批次在影响批次关闭后切到新版本。
RUNNING_STATES = {"运行中"}
DRAFT_STATES = {"草稿"}


# ---------------------------------------------------------------- 基础工具

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def parse_mapping(text: Any) -> dict[str, str]:
    """把 'a->b, c->d' 解析成 {源字段: 目标字段}；容错空串与空白。"""
    result: dict[str, str] = {}
    for part in str(text or "").split(","):
        piece = part.strip()
        if not piece or "->" not in piece:
            continue
        source, target = piece.split("->", 1)
        source, target = source.strip(), target.strip()
        if source:
            result[source] = target
    return result


def diff_mappings(old_text: Any, new_text: Any) -> list[dict[str, str]]:
    """逐源字段比对两版映射，输出变化类型（新增/移除/目标字段变更）。"""
    old_map, new_map = parse_mapping(old_text), parse_mapping(new_text)
    changes: list[dict[str, str]] = []
    for source in sorted(set(old_map) | set(new_map)):
        old_target = old_map.get(source, "")
        new_target = new_map.get(source, "")
        if old_target == new_target:
            continue
        if not old_target:
            change_type = "新增映射"
        elif not new_target:
            change_type = "移除映射"
        else:
            change_type = "目标字段变更"
        changes.append({
            "源字段": source,
            "旧目标字段": old_target,
            "新目标字段": new_target,
            "变化类型": change_type,
        })
    return changes


def split_refs(text: Any) -> list[str]:
    """血缘的上游/下游节点字段可能是逗号分隔的多个节点编号。"""
    return [piece.strip() for piece in str(text or "").split(",") if piece.strip()]


def version_tuple(version: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for piece in str(version or "").lstrip("vV").split("."):
        try:
            numbers.append(int(piece))
        except ValueError:
            numbers.append(0)
    return tuple(numbers) or (0,)


def bump_version(version: str) -> str:
    parts = str(version or "v1.0").lstrip("vV").split(".")
    head = "v"
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        major = 1
    try:
        minor = int(parts[1]) + 1 if len(parts) > 1 else 1
    except ValueError:
        minor = 1
    return f"{head}{major}.{minor}"


def priority_key(version_row: dict[str, Any]) -> tuple[tuple[int, ...], str]:
    """映射冲突优先级：先比节点版本号，版本相同再比生效时间（越晚越优先）。"""
    return version_tuple(str(version_row.get("版本号", ""))), str(version_row.get("生效时间", ""))


def fingerprint(node_id: int, new_mapping: str, new_version: str) -> str:
    raw = f"{node_id}|{new_mapping.strip()}|{new_version.strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 服务主体

class ImpactService:
    # ------------------------------------------------ 节点/版本查询

    def find_node_by_id(self, node_id: int) -> dict[str, Any] | None:
        return store.find(LINEAGE_MODULE, node_id)

    def find_node_by_code(self, node_code: str) -> dict[str, Any] | None:
        for row in store.rows(LINEAGE_MODULE):
            if str(row.get("节点编号", "")).strip() == node_code:
                return row
        return None

    def list_versions(self, node_code: str) -> list[dict[str, Any]]:
        rows = [
            row for row in store.rows(VERSION_MODULE)
            if str(row.get("节点编号", "")).strip() == node_code
        ]
        return sorted(rows, key=priority_key, reverse=True)

    def active_version(self, node_code: str) -> dict[str, Any] | None:
        """当前对外服务的版本：仅取已生效版本，按版本号+生效时间裁决冲突。"""
        candidates = [
            row for row in store.rows(VERSION_MODULE)
            if str(row.get("节点编号", "")).strip() == node_code
            and row.get("状态") == VERSION_ACTIVE
        ]
        if not candidates:
            return None
        return max(candidates, key=priority_key)

    def open_batch_for_node(self, node_id: int) -> dict[str, Any] | None:
        for row in store.rows(BATCH_MODULE):
            if int(row.get("节点id", 0)) == node_id and row.get("状态") == BATCH_OPEN:
                return row
        return None

    # ------------------------------------------------ 影响分析

    def analyze(
        self,
        node_id: int,
        *,
        new_mapping: str | None = None,
        new_version: str | None = None,
        effective_time: str | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, int]:
        """发布节点前计算影响范围并生成影响清单。

        返回 (批次, 提示信息, HTTP状态码)；幂等命中返回既有批次，冲突返回 409。
        """
        node = self.find_node_by_id(node_id)
        if node is None:
            return None, f"血缘节点 {node_id} 不存在或已归档", 404

        node_code = str(node.get("节点编号", "")).strip()
        lock = store.node_lock(node_code)
        with lock:
            old_mapping = str(node.get("字段映射", "") or "")
            candidate_mapping = new_mapping.strip() if new_mapping and new_mapping.strip() else old_mapping
            old_version = str(node.get("图谱版本", "") or "v1.0")
            candidate_version = (
                new_version.strip() if new_version and new_version.strip()
                else bump_version(old_version)
            )
            effective = effective_time.strip() if effective_time and effective_time.strip() else now_str()
            stamp = fingerprint(node_id, candidate_mapping, candidate_version)

            # 幂等：同一节点 + 同一候选映射/版本，无论批次是否关闭都沿用原结论。
            for existing in store.rows(BATCH_MODULE):
                if int(existing.get("节点id", 0)) == node_id and existing.get("分析指纹") == stamp:
                    return existing, "该节点已分析过相同变更，沿用既有影响批次结论", 200

            open_batch = self.open_batch_for_node(node_id)
            if open_batch is not None:
                return (
                    None,
                    f"节点存在未关闭的影响批次 {open_batch.get('批次编号')}，"
                    "请先逐项确认并关闭后再分析新变更，避免两套冲突清单",
                    409,
                )

            mapping_changes = diff_mappings(old_mapping, candidate_mapping)
            if not mapping_changes:
                return None, "候选字段映射与当前生效映射完全一致，没有需要分析的变更", 400

            batch_rows = store.rows(BATCH_MODULE)
            batch_id = next_id(batch_rows)
            batch_no = f"IMPACT-{batch_id:04d}"
            batch: dict[str, Any] = {
                "id": batch_id,
                "批次编号": batch_no,
                "节点id": node_id,
                "节点编号": node_code,
                "节点名称": node.get("节点名称", ""),
                "旧版本": old_version,
                "新版本": candidate_version,
                "旧映射": old_mapping,
                "新映射": candidate_mapping,
                "生效时间": effective,
                "分析指纹": stamp,
                "状态": BATCH_OPEN,
                "创建时间": now_str(),
                "关闭时间": "",
                "备注": remark or "",
                "待确认数": 0,
                "已确认数": 0,
                "影响项总数": 0,
            }
            batch_rows.append(batch)

            items = store.rows(ITEM_MODULE)

            def add_item(kind: str, target: str, module: str, ref_id: Any,
                         detail: str, old_value: str = "", new_value: str = "",
                         diff_summary: str = "") -> dict[str, Any]:
                item = {
                    "id": next_id(items),
                    "影响批次": batch_no,
                    "批次id": batch_id,
                    "影响类型": kind,
                    "关联对象": target,
                    "关联模块": module,
                    "关联记录id": ref_id,
                    "影响说明": detail,
                    "旧值": old_value,
                    "新值": new_value,
                    "差异摘要": diff_summary,
                    "确认状态": ITEM_PENDING,
                    "确认时间": "",
                    "确认备注": "",
                }
                items.append(item)
                return item

            # 1) 上游节点：沿血缘边向上定位。
            for upstream in split_refs(node.get("上游节点")):
                up_node = self.find_node_by_code(upstream)
                detail = "上游节点输出字段的含义将随本次映射调整被重新解释，需确认供数口径"
                add_item(
                    "上游节点", upstream, LINEAGE_MODULE,
                    up_node.get("id") if up_node else None, detail,
                    old_value=old_version, new_value=candidate_version,
                )

            # 2) 下游节点：沿血缘边向下定位，下游表会直接收到变更后的字段。
            for downstream in split_refs(node.get("下游节点")):
                down_node = self.find_node_by_code(downstream)
                detail = "下游节点消费的目标字段发生变化，需评估其转换逻辑与下游汇总口径"
                add_item(
                    "下游节点", downstream, LINEAGE_MODULE,
                    down_node.get("id") if down_node else None, detail,
                    old_value=old_mapping, new_value=candidate_mapping,
                )

            # 3) 字段映射：逐项列出变化，作为待确认范围的最小粒度。
            for change in mapping_changes:
                summary = (
                    f"{change['变化类型']}：{change['源字段']} "
                    f"[{change['旧目标字段'] or '—'} -> {change['新目标字段'] or '—'}]"
                )
                add_item(
                    "字段映射", f"{node_code}.{change['源字段']}", LINEAGE_MODULE, node_id,
                    f"源字段 {change['源字段']} 的映射目标由「{change['旧目标字段'] or '—'}」"
                    f"调整为「{change['新目标字段'] or '—'}」",
                    old_value=change["旧目标字段"], new_value=change["新目标字段"],
                    diff_summary=summary,
                )

            # 4) 关联元数据记录：沿「节点下游表 -> 字段」的当前依赖路径定位旧映射影响。
            downstream_table = str(node.get("下游表名", "") or "").strip()
            touched_fields = sorted({
                value for change in mapping_changes
                for value in (change["旧目标字段"], change["新目标字段"]) if value
            })
            matched_meta_ids: set[int] = set()
            for field_name in touched_fields:
                matched = [
                    row for row in store.rows(METADATA_MODULE)
                    if str(row.get("表名称", "")).strip() == downstream_table
                    and str(row.get("字段名称", "")).strip() == field_name
                ]
                for meta in matched:
                    meta_id = int(meta.get("id", 0))
                    if meta_id in matched_meta_ids:
                        continue
                    matched_meta_ids.add(meta_id)
                    meta["复核状态"] = REVIEW_PENDING
                    meta["影响批次"] = batch_no
                    add_item(
                        "元数据记录", str(meta.get("记录编号", "")), METADATA_MODULE, meta_id,
                        f"元数据字段 {downstream_table}.{field_name} 的业务含义依赖旧映射，"
                        "映射变更后需复核业务含义与统计口径",
                        old_value=str(meta.get("业务含义", "")),
                        new_value="待按新映射复核",
                        diff_summary=f"表 {downstream_table} 字段 {field_name} 进入待复核",
                    )
                if not matched:
                    add_item(
                        "元数据记录", f"（目录缺失：{downstream_table}.{field_name}）",
                        METADATA_MODULE, None,
                        f"新映射目标字段 {downstream_table}.{field_name} 在元数据目录中没有记录，"
                        "需补录后再复核",
                        old_value="目录无记录", new_value=field_name,
                        diff_summary=f"{downstream_table}.{field_name} 目录缺失",
                    )

            # 5) 历史重跑批次：指出引用版本与新版本差异，旧批次继续钉住旧版本。
            for backfill in store.rows(BACKFILL_MODULE):
                if str(backfill.get("关联血缘节点", "")).strip() != node_code:
                    continue
                state = str(backfill.get("重跑状态") or backfill.get("status") or "").strip()
                pinned = str(backfill.get("引用节点版本", "") or old_version)
                if state in RUNNING_STATES:
                    plan = "批次已开始，继续由旧版本服务，不随本次发布切换"
                elif state in DRAFT_STATES:
                    plan = "批次尚未启动，影响批次关闭后自动切换到新版本"
                else:
                    plan = "批次已结束，保留引用版本作为历史审计记录"
                add_item(
                    "重跑批次", str(backfill.get("批次编号", "")), BACKFILL_MODULE,
                    int(backfill.get("id", 0)),
                    f"重跑批次引用 {node_code} {pinned}，与候选 {candidate_version} 存在映射差异；{plan}",
                    old_value=pinned, new_value=candidate_version,
                    diff_summary="；".join(
                        f"{c['源字段']}:{c['旧目标字段'] or '—'}→{c['新目标字段'] or '—'}"
                        for c in mapping_changes
                    ),
                )

            # 待生效信息挂到节点上：发布（关闭批次）前当前映射仍保持旧版本。
            node["待生效映射"] = candidate_mapping
            node["待生效版本"] = candidate_version
            node["待生效时间"] = effective
            node["影响批次"] = batch_no

            batch["影响项总数"] = len(items)
            batch["待确认数"] = len(items)
            batch["已确认数"] = 0
            return batch, f"影响分析完成，共生成 {len(items)} 个待确认影响项", 201

    # ------------------------------------------------ 清单读取

    def list_batches(
        self, *, status: str | None = None, node_code: str | None = None
    ) -> list[dict[str, Any]]:
        rows = store.rows(BATCH_MODULE)
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if node_code:
            rows = [row for row in rows if str(row.get("节点编号", "")).strip() == node_code]
        return sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(BATCH_MODULE, batch_id)

    def list_items(self, batch_id: int) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(ITEM_MODULE)
            if int(row.get("批次id", 0)) == batch_id
        ]

    # ------------------------------------------------ 逐项确认

    def confirm_item(
        self, item_id: int, *, remark: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        item = store.find(ITEM_MODULE, item_id)
        if item is None:
            return None, f"影响项 {item_id} 不存在"
        if item.get("确认状态") == ITEM_CONFIRMED:
            return item, "影响项已确认，重复确认保持幂等"

        item["确认状态"] = ITEM_CONFIRMED
        item["确认时间"] = now_str()
        item["确认备注"] = remark or item.get("确认备注", "")

        # 元数据影响项确认 = 该目录记录复核通过；未确认前目录保持待复核。
        if item.get("影响类型") == "元数据记录" and item.get("关联记录id") is not None:
            meta = store.find(METADATA_MODULE, int(item["关联记录id"]))
            if meta is not None:
                meta["复核状态"] = REVIEW_DONE

        batch = self.get_batch(int(item.get("批次id", 0)))
        if batch is not None:
            items = self.list_items(int(batch["id"]))
            confirmed = sum(1 for row in items if row.get("确认状态") == ITEM_CONFIRMED)
            batch["已确认数"] = confirmed
            batch["待确认数"] = len(items) - confirmed
        return item, "影响项已确认"

    # ------------------------------------------------ 关闭批次 / 发布

    def close_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        batch = self.get_batch(batch_id)
        if batch is None:
            return None, f"影响批次 {batch_id} 不存在"
        if batch.get("状态") == BATCH_CLOSED:
            return batch, "影响批次已关闭，重复关闭保持幂等"

        pending_items = [
            row for row in self.list_items(batch_id)
            if row.get("确认状态") != ITEM_CONFIRMED
        ]
        if pending_items:
            refs = "、".join(str(row.get("关联对象", "")) for row in pending_items)
            return None, f"仍有 {len(pending_items)} 个影响项未确认（{refs}），未确认项不能关闭批次"

        node_code = str(batch.get("节点编号", "")).strip()
        lock = store.node_lock(node_code)
        with lock:
            new_version = str(batch.get("新版本", ""))
            new_mapping = str(batch.get("新映射", ""))
            effective = str(batch.get("生效时间", "")) or now_str()

            # 旧版本归档留痕，新版本生效；冲突时版本号+生效时间决定优先级。
            for version_row in store.rows(VERSION_MODULE):
                if (
                    str(version_row.get("节点编号", "")).strip() == node_code
                    and version_row.get("状态") == VERSION_ACTIVE
                ):
                    version_row["状态"] = VERSION_ARCHIVED
            version_rows = store.rows(VERSION_MODULE)
            version_rows.append({
                "id": next_id(version_rows),
                "节点编号": node_code,
                "版本号": new_version,
                "状态": VERSION_ACTIVE,
                "字段映射": new_mapping,
                "转换逻辑": "",
                "生效时间": effective,
                "创建时间": now_str(),
            })

            # 血缘节点本面：当前映射切到新版本，清掉待生效草稿。
            node = self.find_node_by_code(node_code)
            if node is not None:
                node["字段映射"] = new_mapping
                node["图谱版本"] = new_version
                node["生效时间"] = effective
                node.pop("待生效映射", None)
                node.pop("待生效版本", None)
                node.pop("待生效时间", None)
                node.pop("影响批次", None)

            # 历史重跑面：草稿批次落到同一新版本；已开始/已结束批次保留旧引用。
            for backfill in store.rows(BACKFILL_MODULE):
                if str(backfill.get("关联血缘节点", "")).strip() != node_code:
                    continue
                state = str(backfill.get("重跑状态") or backfill.get("status") or "").strip()
                if state in DRAFT_STATES:
                    backfill["引用节点版本"] = new_version
                    backfill["引用映射快照"] = new_mapping

            # 元数据目录面：本批次挂账的目录记录全部结束复核流程。
            for meta in store.rows(METADATA_MODULE):
                if str(meta.get("影响批次", "")).strip() == str(batch.get("批次编号", "")):
                    meta["复核状态"] = REVIEW_DONE

            batch["状态"] = BATCH_CLOSED
            batch["关闭时间"] = now_str()
            batch["待确认数"] = 0
        return batch, "影响批次已关闭，新版本已生效，元数据复核与重跑引用已同步落版"


impact_service = ImpactService()
