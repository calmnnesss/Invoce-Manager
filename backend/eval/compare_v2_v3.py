#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 与 v3 的逐张对照：看清闭集改动到底动了什么。

重点回答三个问题：
  1. buyer_entity_id 选错的 6 张，模型实际读到了什么？是盲选还是读错？
  2. 销售方为什么大幅回退？回退的样本在 v2 里是什么状态？
  3. 方向正确率下降，是真的填反了，还是判定口径变了？
"""
from __future__ import annotations

import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
R = EVAL / "results"
LOGS = EVAL.parent / "logs" / "llm"

GT_SELLER = "广州晶东贸易有限公司"
GT_SELLER_TAX = "91440101664041243T"
ENTITIES = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                      .read_text(encoding="utf-8"))["entities"]
BY_ID = {e["id"]: e for e in ENTITIES}


def load(label):
    rows = json.loads((R / f"{label}.json").read_text(encoding="utf-8"))["per_sample"]
    return {r["file"]: r for r in rows}


def raw_of(label_rows, f, field):
    r = label_rows.get(f)
    if not r or field not in r["fields"]:
        return None
    return r["fields"][field]["raw"]


def observed(fname):
    """从 v3 日志里取 buyer_observed_* 与 party 相关原始输出。"""
    hits = sorted(LOGS.rglob(f"*_{fname}_*.log"))
    for p in reversed(hits):
        t = p.read_text(encoding="utf-8", errors="replace")
        if "Prompt     : v3" not in t:
            continue
        i = t.find("[1] 原始响应")
        j = t.find("{", i)
        depth = 0
        for k in range(j, len(t)):
            if t[k] == "{":
                depth += 1
            elif t[k] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[j:k + 1])
                    except json.JSONDecodeError:
                        return {}
        return {}
    return {}


def key(f):
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


v2, v3 = load("v2_prompt"), load("v3_closedset")
files = sorted(v3, key=key)

print("=" * 78)
print("1) buyer_entity_id 选错的样本 —— 模型到底读到了什么")
print("=" * 78)
for f in files:
    d = v3[f]["fields"].get("buyer_entity_id")
    if not d or d["raw_ok"]:
        continue
    o = observed(f)
    picked = BY_ID.get(d["raw"], {})
    print(f"\n{f}")
    print(f"   选中          : {d['raw']}  ({picked.get('name','?')} / {picked.get('tax_id','?')})")
    print(f"   实际读到名称   : {o.get('buyer_observed_name')}")
    print(f"   实际读到税号   : {o.get('buyer_observed_tax_id')}")

print()
print("=" * 78)
print("2) 销售方名称：v2 对 / v3 错 的样本")
print("=" * 78)
regress = []
improve = []
for f in files:
    a = raw_of(v2, f, "seller_name") == GT_SELLER
    b = raw_of(v3, f, "seller_name") == GT_SELLER
    if a and not b:
        regress.append(f)
    if b and not a:
        improve.append(f)
print(f"\nv2对→v3错（回退）{len(regress)} 张:")
for f in regress:
    print(f"   {f:<10} v3 填了: {raw_of(v3, f, 'seller_name')}")
print(f"\nv3对→v2错（改善）{len(improve)} 张: {' '.join(improve)}")

print()
print("=" * 78)
print("3) 方向判定：v3 判为方向错的 16 张，逐张看证据")
print("=" * 78)
for f in files:
    if v3[f]["direction_ok"]:
        continue
    o = observed(f)
    print(f"\n{f}   buyer_side={v3[f]['buyer_side']}  seller_side={v3[f]['seller_side']}")
    print(f"   observed_name : {o.get('buyer_observed_name')}")
    print(f"   seller_name   : {o.get('seller_name')}")

print()
print("=" * 78)
print("4) 汇总：v2 vs v3 关键字段")
print("=" * 78)
n = len(files)
for field, gtv in (("seller_name", GT_SELLER), ("seller_tax_id", GT_SELLER_TAX)):
    a = sum(1 for f in files if raw_of(v2, f, field) == gtv)
    b = sum(1 for f in files if raw_of(v3, f, field) == gtv)
    print(f"  {field:<16} v2 {a:>2}/{n} = {a/n:5.1%}    v3 {b:>2}/{n} = {b/n:5.1%}    Δ {(b-a)/n*100:+.1f}pt")
