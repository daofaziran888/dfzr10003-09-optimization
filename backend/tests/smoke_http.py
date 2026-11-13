"""真实 HTTP 链路冒烟：分析 -> 幂等 -> 并发冲突 -> 待复核 -> 确认 -> 三面落版。

运行：cd backend && .venv/bin/python -I -m tests.smoke_http
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:8012"
NEW = ("order_id->order_id, cust_id->customer_id, order_amt->order_amount_with_tax, "
       "order_time->order_ts, channel->sales_channel")


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


print("== 初始状态")
s, d = call("GET", "/api/lineage/2")
print("  节点:", d["节点编号"], d["status"], d["图谱版本"])
s, d = call("GET", f"/api/impact/batches?node={q('LINEAGE-0002')}")
assert d["total"] == 0

print("== 1) 发起影响分析（发布前）")
s, d = call("POST", "/api/impact/nodes/2/analyze", {"values": {"new_mapping": NEW}})
assert s == 201, d
batch, items = d["entry"]["batch"], d["entry"]["items"]
print(" ", d["message"], "| 批次:", batch["批次编号"], "| 类型分布:", dict(Counter(i["影响类型"] for i in items)))

print("== 2) 分析进行中，发布节点必须被拦截")
s, d = call("POST", "/api/lineage/2/actions", {"values": {"action": "发布节点"}})
assert s == 200 and d["ok"] is False and "影响批次" in d["message"], d
print("  拦截成功 ->", d["message"])

print("== 3) 重复分析幂等")
s, d = call("POST", "/api/impact/nodes/2/analyze", {"values": {"new_mapping": NEW}})
assert s == 200 and d["entry"]["batch"]["id"] == batch["id"], d
print(" ", d["message"])

print("== 4) 异参并发分析被拒绝，不产生第二批次")
s, d = call("POST", "/api/impact/nodes/2/analyze", {"values": {"new_mapping": NEW + ", x->y"}})
assert s == 409, d
s, listing = call("GET", f"/api/impact/batches?node={q('LINEAGE-0002')}")
assert listing["total"] == 1
print("  409 ->", d["detail"])

print("== 5) 元数据目录写入待复核（第二个业务面）")
s, d = call("GET", "/api/metadata/review-queue")
pending = [(r["记录编号"], r["字段名称"], r["影响批次"]) for r in d["items"]]
assert {r[0] for r in pending} == {"META-0004", "META-0006"}, pending
assert all(r[2] == batch["批次编号"] for r in pending)
print("  待复核:", pending)

print("== 6) 未确认影响项不能关闭批次")
s, d = call("POST", f"/api/impact/batches/{batch['id']}/close", {})
assert d["ok"] is False and "未确认" in d["message"], d
print(" ", d["message"][:40], "…")

print("== 7) 逐项确认")
for item in items:
    s, d = call("POST", f"/api/impact/items/{item['id']}/confirm", {"remark": "核对"})
    assert s == 200 and d["ok"], d
print("  全部", len(items), "项已确认；重复确认再点一次：", end="")
s, d = call("POST", f"/api/impact/items/{items[0]['id']}/confirm", {})
print(d["message"])

print("== 8) 关闭批次，三个业务面同步落版")
s, d = call("POST", f"/api/impact/batches/{batch['id']}/close", {})
assert d["ok"], d
print(" ", d["message"])

print("== 9) 校验三个业务面")
s, node = call("GET", "/api/lineage/2")
assert node["图谱版本"] == "v1.1" and node["字段映射"] == NEW and "待生效映射" not in node
print("  血缘面:", node["图谱版本"], "映射已切版，待生效草稿已清理")

s, versions = call("GET", f"/api/impact/nodes/{q('LINEAGE-0002')}/versions")
assert {v["版本号"]: v["状态"] for v in versions["items"]} == {"v1.0": "已归档", "v1.1": "已生效"}
assert versions["active"]["版本号"] == "v1.1"
print("  版本面: v1.0 已归档 / v1.1 已生效，冲突裁决生效版本 = v1.1")

s, bf1 = call("GET", "/api/backfill/1/version-diff")
assert bf1["引用节点版本"] == "v1.0" and bf1["是否落后"] is True
assert {c["源字段"] for c in bf1["映射差异"]} == {"order_amt", "channel"}
print("  重跑面 BACK-0001(运行中): 钉住 v1.0 继续服务，差异字段",
      [c["源字段"] for c in bf1["映射差异"]])

s, bf2 = call("GET", "/api/backfill/2/version-diff")
assert bf2["引用节点版本"] == "v1.1" and bf2["是否落后"] is False
print("  重跑面 BACK-0002(草稿): 已落到同一新版本 v1.1")

s, d = call("GET", f"/api/metadata?review_status={q('待复核')}")
assert d["total"] == 0, d
print("  元数据面: 待复核残留 0，目录记录全部随批次闭环为已复核")

print("== 10) 闭环后节点可以发布")
s, d = call("POST", "/api/lineage/2/actions", {"values": {"action": "发布节点"}})
assert d["ok"] and d["entry"]["status"] == "已生效", d
print(" ", d["message"], "| 状态:", d["entry"]["status"])

print("\nHTTP 冒烟链路全部通过 ✅")
