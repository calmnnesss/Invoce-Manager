#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 与 v3.1 的对照：购买方口径（闭集 ID vs 自由抽取）。

v2 的购买方由模型自由识别名称+税号；v3.1 改为从主体清单选 ID。
可比的问法是：「这张票的购买方主体，系统最终识别对了没有」。
  v2   ：buyer_name 与 buyer_tax_id 都正确 才算对
  v3.1 ：buyer_entity_id == E01 才算对
"""
from __future__ import annotations

import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
R = EVAL / "results"

GT = {
    "invoice_code": "4400154130",
    "issue_date": "2016-06-12",
    "buyer_name": "深圳市购机汇网络有限公司",
    "buyer_tax_id": "440300083885931",
    "buyer_entity_id": "E01",
    "seller_name": "广州晶东贸易有限公司",
    "seller_tax_id": "91440101664041243T",
    "amount": "2987.18",
    "tax_amount": "507.82",
    "total_with_tax": "3495.00",
    "tax_rate": "17%",
}

COMMON = ["invoice_code", "issue_date", "amount", "tax_amount", "total_with_tax", "tax_rate"]


def load(label):
    return {r["file"]: r for r in json.loads((R / f"{label}.json").read_text(encoding="utf-8"))["per_sample"]}


def raw(rows, f, field):
    r = rows.get(f)
    if not r or field not in r["fields"]:
        return None
    return r["fields"][field]["raw"]


def ok(rows, f, field):
    return raw(rows, f, field) == GT[field]


def buyer_ok(rows, f, version):
    if version == "v2":
        return ok(rows, f, "buyer_name") and ok(rows, f, "buyer_tax_id")
    return ok(rows, f, "buyer_entity_id")


v2, v31 = load("v2_prompt"), load("v3_1_evidence")
files = sorted(v31, key=lambda x: int(x.lstrip("b").split(".")[0]))
n = len(files)


def pct(x):
    return f"{x / n:6.1%}"


print("=" * 72)
print(f"购买方主体识别（不看销售方）  n={n}")
print("=" * 72)
b2 = sum(1 for f in files if buyer_ok(v2, f, "v2"))
b31 = sum(1 for f in files if buyer_ok(v31, f, "v3.1"))
print(f"  v2   自由抽取，名称+税号都对   {b2:>2}/{n} = {pct(b2)}")
print(f"  v3.1 闭集选 ID，选中 E01       {b31:>2}/{n} = {pct(b31)}")
print(f"  提升                                    {(b31 - b2) / n * 100:+.1f}pt")
print()
print("  拆开看 v2 的两个字段：")
for fld in ("buyer_name", "buyer_tax_id"):
    c = sum(1 for f in files if ok(v2, f, fld))
    print(f"    {fld:<14} {c:>2}/{n} = {pct(c)}")

print()
print("=" * 72)
print("整单口径（剔除 seller_name / seller_tax_id 后）")
print("=" * 72)
e2 = sum(1 for f in files
         if buyer_ok(v2, f, "v2") and all(ok(v2, f, x) for x in COMMON))
e31 = sum(1 for f in files
          if buyer_ok(v31, f, "v3.1") and all(ok(v31, f, x) for x in COMMON))
print(f"  v2   购买方 + 6 个通用字段全对  {e2:>2}/{n} = {pct(e2)}")
print(f"  v3.1 购买方 + 6 个通用字段全对  {e31:>2}/{n} = {pct(e31)}")
print(f"  提升                                    {(e31 - e2) / n * 100:+.1f}pt")

print()
print("  6 个通用字段各自：")
print(f"  {'字段':<18}{'v2':>10}{'v3.1':>10}{'Δ':>10}")
for fld in COMMON:
    a = sum(1 for f in files if ok(v2, f, fld))
    b = sum(1 for f in files if ok(v31, f, fld))
    print(f"  {fld:<18}{pct(a):>10}{pct(b):>10}{(b - a) / n * 100:>+9.1f}pt")

print()
print("=" * 72)
print("完整口径（含销售方）——供对照，不要漏掉这一栏")
print("=" * 72)
for fld in ("seller_name", "seller_tax_id"):
    a = sum(1 for f in files if ok(v2, f, fld))
    b = sum(1 for f in files if ok(v31, f, fld))
    print(f"  {fld:<18}{pct(a):>10}{pct(b):>10}{(b - a) / n * 100:>+9.1f}pt")
a = sum(1 for f in files if v2[f]["exact_match"])
b = sum(1 for f in files if v31[f]["exact_match"])
print(f"  {'Exact Match':<18}{pct(a):>10}{pct(b):>10}{(b - a) / n * 100:>+9.1f}pt")
