"""血缘变更影响分析的端到端校验（标准库即可运行，不依赖 pytest）。

运行：cd backend && .venv/bin/python -I -m tests.test_impact
"""
from __future__ import annotations

import http.client
import threading
import time

import uvicorn

from app.main import app
from app.services.impact import (
    BATCH_CLOSED,
    BATCH_OPEN,
    ITEM_CONFIRMED,
    ITEM_PENDING,
    impact_service,
)
from app.services.lineage import LineageService
from app.services.backfill import BackfillService
from app.services.metadata import MetadataService
from app.store import store

NEW_MAPPING = (
    "order_id->order_id, cust_id->customer_id, "
    "order_amt->order_amount_with_tax, order_time->order_ts, "
    "channel->sales_channel"
)

lineage_service = LineageService()
backfill_service = BackfillService()
metadata_service = MetadataService()

passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    assert condition, f"{label} 失败：{detail}"
    passed += 1
    print(f"  ✓ {label}")


def section(title: str) -> None:
    print(f"\n== {title} ==")


def analyze(node_id: int = 2, mapping: str = NEW_MAPPING):
    return impact_service.analyze(node_id, new_mapping=mapping)


# ------------------------------------------------ 1. 影响范围计算
section("发布前影响分析：上游/下游/字段映射/元数据/重跑批次")
store.reset()
batch, message, code = analyze()
check("分析返回 201", code == 201, message)
check("批次处于待处理", batch["状态"] == BATCH_OPEN)
check("旧版本记录为 v1.0", batch["旧版本"] == "v1.0")
check("新版本自动递增为 v1.1", batch["新版本"] == "v1.1")

items = impact_service.list_items(int(batch["id"]))
kinds = {item["影响类型"] for item in items}
check("四类影响齐全", {"上游节点", "下游节点", "字段映射", "元数据记录", "重跑批次"} <= kinds, str(kinds))
upstreams = {i["关联对象"] for i in items if i["影响类型"] == "上游节点"}
downstreams = {i["关联对象"] for i in items if i["影响类型"] == "下游节点"}
check("上游定位到 LINEAGE-0001", upstreams == {"LINEAGE-0001"}, str(upstreams))
check("下游定位到 LINEAGE-0003", downstreams == {"LINEAGE-0003"}, str(downstreams))

mapping_items = [i for i in items if i["影响类型"] == "字段映射"]
changed_sources = {i["关联对象"].split(".", 1)[1] for i in mapping_items}
check("字段映射逐项列出 order_amt 与 channel 变化",
      {"order_amt", "channel"} <= changed_sources, str(changed_sources))

meta_items = [i for i in items if i["影响类型"] == "元数据记录"]
meta_refs = {i["关联对象"] for i in meta_items}
check("旧映射字段 META-0004 被定位", "META-0004" in meta_refs, str(meta_refs))
check("新目标字段缺失时生成目录缺口项",
      any("目录缺失" in i["关联对象"] for i in meta_items), str(meta_refs))

meta_0004 = next(r for r in store.rows("metadata") if r["记录编号"] == "META-0004")
check("元数据目录写入待复核", meta_0004["复核状态"] == "待复核")
check("元数据记录挂到同一影响批次", meta_0004["影响批次"] == batch["批次编号"])
check("待复核清单可从元数据面读出",
      {r["记录编号"] for r in metadata_service.review_queue()} >= {"META-0004"})

bf_items = [i for i in items if i["影响类型"] == "重跑批次"]
bf_refs = {i["关联对象"] for i in bf_items}
check("两个历史批次都进入清单", {"BACK-0001", "BACK-0002"} == bf_refs, str(bf_refs))
running_item = next(i for i in bf_items if i["关联对象"] == "BACK-0001")
check("运行中批次标注继续由旧版本服务", "旧版本服务" in running_item["影响说明"],
      running_item["影响说明"])
check("影响项默认全部待确认", all(i["确认状态"] == ITEM_PENDING for i in items))
check("批次计数与清单一致",
      batch["待确认数"] == len(items) and batch["已确认数"] == 0)

# ------------------------------------------------ 2. 幂等
section("同一节点重复分析幂等，沿用同一批次结论")
batch_again, message_again, code_again = analyze()
check("重复分析返回 200 且批次相同",
      code_again == 200 and batch_again["id"] == batch["id"], message_again)
check("没有新增影响批次", len(store.rows("impact_batches")) == 1)
check("没有新增影响项", len(store.rows("impact_items")) == len(items))

# ------------------------------------------------ 3. 并发安全
section("并发分析不会生成两套冲突清单")
results: list[tuple] = []
errors: list[str] = []


def worker_same() -> None:
    try:
        results.append(analyze())
    except Exception as exc:  # pragma: no cover - 测试兜底
        errors.append(str(exc))


threads = [threading.Thread(target=worker_same) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("并发同参分析无异常", not errors, str(errors))
check("所有并发请求拿到同一批次", {r[0]["id"] for r in results} == {batch["id"]})
check("仍只有一套清单", len(store.rows("impact_batches")) == 1)

conflict_codes: list[int] = []
conflict_lock = threading.Lock()


def worker_diff(mapping: str) -> None:
    _, _, c = impact_service.analyze(2, new_mapping=mapping)
    with conflict_lock:
        conflict_codes.append(c)


other_mapping = NEW_MAPPING.replace("sales_channel", "sale_channel")
t1 = threading.Thread(target=worker_diff, args=(other_mapping,))
t2 = threading.Thread(target=worker_diff, args=(other_mapping + ", ext->ext_col",))
t1.start(); t2.start(); t1.join(); t2.join()
check("开放批次下异参分析全部被拒绝(409)，绝不新建第二批次",
      conflict_codes and all(c == 409 for c in conflict_codes)
      and len(store.rows("impact_batches")) == 1,
      str(conflict_codes))

# ------------------------------------------------ 4. 未确认不能关闭 + 发布闸门
section("未确认影响项不能关闭批次，节点不能发布")
closed, close_msg = impact_service.close_batch(int(batch["id"]))
check("有待确认项时关闭被拒", closed is None and "未确认" in close_msg, close_msg)

node_after_block, pub_msg = lineage_service.run_action(2, "发布节点")
check("影响批次未关闭时发布节点被拦截",
      node_after_block is None and "影响批次" in pub_msg, pub_msg)

for item in items:
    confirmed, confirm_msg = impact_service.confirm_item(int(item["id"]), remark="核对无误")
    check_pairs = confirmed["确认状态"] == ITEM_CONFIRMED
    assert check_pairs, confirm_msg
# 重复确认幂等
again_item, _ = impact_service.confirm_item(int(items[0]["id"]))
check("重复确认保持幂等", again_item["确认状态"] == ITEM_CONFIRMED)

batch = impact_service.get_batch(int(batch["id"]))
check("批次确认进度归零待确认", batch["待确认数"] == 0 and batch["已确认数"] == len(items))

# 元数据影响项确认后目录即复核通过
check("确认元数据影响项后目录变为已复核",
      next(r for r in store.rows("metadata") if r["记录编号"] == "META-0004")["复核状态"] == "已复核")

# ------------------------------------------------ 5. 关闭批次，三个业务面同步落版
section("关闭批次：版本落档，血缘/元数据/重跑三面一致")
closed, close_msg = impact_service.close_batch(int(batch["id"]))
check("全部确认后关闭成功", closed is not None and closed["状态"] == BATCH_CLOSED, close_msg)
check("重复关闭保持幂等", impact_service.close_batch(int(batch["id"]))[0]["状态"] == BATCH_CLOSED)

node2 = store.find("lineage", 2)
check("血缘节点切到新映射", node2["字段映射"] == NEW_MAPPING)
check("血缘节点版本升到 v1.1", node2["图谱版本"] == "v1.1")
check("待生效草稿已清理", "待生效映射" not in node2)

versions = impact_service.list_versions("LINEAGE-0002")
statuses = {v["版本号"]: v["状态"] for v in versions}
check("旧版本归档留痕", statuses.get("v1.0") == "已归档", str(statuses))
check("新版本生效", statuses.get("v1.1") == "已生效", str(statuses))
check("生效版本按版本号裁决为 v1.1",
      impact_service.active_version("LINEAGE-0002")["版本号"] == "v1.1")

back_0001 = next(r for r in store.rows("backfill") if r["批次编号"] == "BACK-0001")
back_0002 = next(r for r in store.rows("backfill") if r["批次编号"] == "BACK-0002")
check("运行中批次继续钉住旧版本 v1.0", back_0001["引用节点版本"] == "v1.0")
check("运行中批次保留旧映射快照", "order_amt->order_amount," in back_0001["引用映射快照"])
check("草稿批次切到新版本 v1.1", back_0002["引用节点版本"] == "v1.1")
check("草稿批次引用落到同一新映射", back_0002["引用映射快照"] == NEW_MAPPING)

for meta in store.rows("metadata"):
    if meta.get("影响批次") == batch["批次编号"]:
        check(f"目录记录 {meta['记录编号']} 复核闭环", meta["复核状态"] == "已复核")

# 发布闸门放开
published, pub_msg = lineage_service.run_action(2, "发布节点")
check("影响闭环后节点可发布", published is not None and published["status"] == "已生效", pub_msg)

# ------------------------------------------------ 6. 重跑版本差异
section("历史重跑页指出引用版本与新版本差异")
diff_running = backfill_service.version_diff(1)
check("旧批次识别为版本落后", diff_running["是否落后"] is True)
check("差异包含 order_amt 目标字段变化",
      any(d["源字段"] == "order_amt" for d in diff_running["映射差异"]),
      str(diff_running["映射差异"]))
diff_draft = backfill_service.version_diff(2)
check("已切换批次不再落后", diff_draft["是否落后"] is False)

created, missing = backfill_service.create_entry({
    "批次编号": "BACK-0004", "目标表名": "dwd_order_detail", "开始日期": "2026-09-20",
    "关联血缘节点": "LINEAGE-0002",
})
check("新批次登记成功", created is not None and not missing)
check("新批次自动钉住当前生效版本 v1.1", created["引用节点版本"] == "v1.1")
check("新批次映射快照与生效版本一致", created["引用映射快照"] == NEW_MAPPING)

# ------------------------------------------------ 7. 闭环后可分析下一轮变更
section("批次关闭后同一节点可发起新一轮分析")
next_mapping = NEW_MAPPING + ", promo_id->promo_id"
batch2, msg2, code2 = impact_service.analyze(2, new_mapping=next_mapping)
check("新一轮分析成功建批", code2 == 201 and batch2["id"] != batch["id"], msg2)
check("新批次旧版本承接 v1.1", batch2["旧版本"] == "v1.1" and batch2["新版本"] == "v1.2")

# ------------------------------------------------ 8. 冲突优先级
section("映射冲突按节点版本号、生效时间裁决")
store.reset()
v_rows = store.rows("lineage_versions")
v_rows.append({"id": 100, "节点编号": "LINEAGE-0009", "版本号": "v1.0", "状态": "已生效",
               "字段映射": "a->b", "转换逻辑": "", "生效时间": "2026-09-01 00:00:00", "创建时间": ""})
v_rows.append({"id": 101, "节点编号": "LINEAGE-0009", "版本号": "v2.0", "状态": "已生效",
               "字段映射": "a->c", "转换逻辑": "", "生效时间": "2026-09-02 00:00:00", "创建时间": ""})
check("版本号优先", impact_service.active_version("LINEAGE-0009")["版本号"] == "v2.0")
v_rows.append({"id": 102, "节点编号": "LINEAGE-0010", "版本号": "v1.0", "状态": "已生效",
               "字段映射": "a->b", "转换逻辑": "", "生效时间": "2026-09-01 00:00:00", "创建时间": ""})
v_rows.append({"id": 103, "节点编号": "LINEAGE-0010", "版本号": "v1.0", "状态": "已生效",
               "字段映射": "a->c", "转换逻辑": "", "生效时间": "2026-09-05 00:00:00", "创建时间": ""})
check("同版本时生效时间晚者优先",
      impact_service.active_version("LINEAGE-0010")["生效时间"] == "2026-09-05 00:00:00")

# ------------------------------------------------ 9. HTTP 接口链路
section("HTTP 接口：分析/清单/确认/关闭/待复核/版本差异")
store.reset()
config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
server = uvicorn.Server(config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(50):
    if server.started:
        break
    time.sleep(0.05)
port = server.servers[0].sockets[0].getsockname()[1]
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)


def api(method: str, path: str, body: dict | None = None):
    import json
    payload = json.dumps(body or {}).encode()
    conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode()
    return resp.status, json.loads(raw) if raw else {}


status, data = api("POST", "/api/impact/nodes/2/analyze", {"values": {"new_mapping": NEW_MAPPING}})
check("分析接口 201", status == 201, str(data))
http_batch = data["entry"]["batch"]
check("接口返回逐项清单", len(data["entry"]["items"]) >= 5)

status, data = api("POST", f"/api/impact/nodes/2/analyze", {"values": {"new_mapping": NEW_MAPPING}})
check("接口幂等返回 200", status == 200 and data["entry"]["batch"]["id"] == http_batch["id"])

status, data = api("GET", "/api/impact/batches?node=LINEAGE-0002")
check("清单视图可按节点过滤", status == 200 and data["total"] == 1)

status, data = api("POST", f"/api/impact/batches/{http_batch['id']}/close")
check("接口层面未确认禁止关闭", status == 200 and data["ok"] is False)

for item in impact_service.list_items(int(http_batch["id"])):
    s, _ = api("POST", f"/api/impact/items/{item['id']}/confirm", {"remark": "http 确认"})
    assert s == 200
status, data = api("POST", f"/api/impact/batches/{http_batch['id']}/close")
check("全部确认后接口关闭成功", status == 200 and data["ok"] is True, str(data))

status, data = api("GET", "/api/metadata/review-queue")
check("元数据待复核清单接口可用", status == 200 and "items" in data)

status, data = api("GET", "/api/backfill/1/version-diff")
check("重跑版本差异接口可用", status == 200 and data["是否落后"] is True)

status, data = api("GET", "/api/impact/nodes/LINEAGE-0002/versions")
check("节点版本历史接口可用",
      status == 200 and {v["版本号"] for v in data["items"]} == {"v1.0", "v1.1"})

server.should_exit = True
thread.join(timeout=5)

print(f"\n全部 {passed} 项断言通过 ✅")
