#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨版本追踪关键字段，看每次改动到底动了什么。

    python eval/track_versions.py
"""
from __future__ import annotations

import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
R = EVAL / "results"
LOGS = EVAL.parent / "logs" / "llm"

RUNS = [("baseline", "v1"), ("v2_prompt", "v2"),
        ("v3_closedset", "v3"), ("v3_1_evidence", "v3.1")]

GT = {"seller_name": "广州晶东贸易有限公司",
      "seller_tax_id": "91440101664041243T",
      "amount": "2987.18", "tax_amount": "507.82",
      "total_with_tax": "3495.00", "issue_date": "2016-06-12"}

ENT = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                 .read_text(encoding="utf-8"))["entities"]
BY_ID = {e["id"]: e for e in ENT}


def load(label):
    p = R / f"{label}.json"
    if not p.exists():
        return None
    return {r["file"]: r for r in json.loads(p.read_text(encoding="utf-8"))["per_sample"]}


def raw(rows, f, field):
    r = rows.get(f) if rows else None
    if not r or field not in r["fields"]:
        return None
    return r["fields"][field]["raw"]


def key(f):
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


def read_log(fname, version):
    for p in sorted(LOGS.rglob(f"*_{fname}_*.log"), reverse=True):
        t = p.read_text(encoding="utf-8", errors="replace")
        if f"Prompt     : {version}" not in t:
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


data = {label: load(label) for label, _ in RUNS}
files = sorted(data["v3_1_evidence"], key=key)
n = len(files)

print("=" * 76)
print("跨版本关键字段追踪")
print("=" * 76)
hdr = f"{'字段':<18}" + "".join(f"{v:>9}" for _, v in RUNS)
print(hdr)
for field, gtv in GT.items():
    line = f"{field:<18}"
    for label, _ in RUNS:
        rows = data[label]
        if rows is None:
            line += f"{'-':>9}"
            continue
        ok = sum(1 for f in files if raw(rows, f, field) == gtv)
        line += f"{ok / n:>8.1%} "
    print(line)

print(f"\n{'方向正确率':<18}", end="")
for label, _ in RUNS:
    rows = data[label]
    ok = sum(1 for f in files if rows[f]["direction_ok"])
    print(f"{ok / n:>8.1%} ", end="")
print(f"\n{'Exact Match':<18}", end="")
for label, _ in RUNS:
    rows = data[label]
    ok = sum(1 for f in files if rows[f]["exact_match"])
    print(f"{ok / n:>8.1%} ", end="")
print(f"\n{'勾稽不平':<18}", end="")
for label, _ in RUNS:
    rows = data[label]
    bad = sum(1 for f in files if rows[f].get("arith_ok") is False)
    print(f"{bad:>8}  ", end="")
print("\n")

print("=" * 76)
print("v3.1 buyer_entity_id 选错的样本")
print("=" * 76)
for f in files:
    d = data["v3_1_evidence"][f]["fields"].get("buyer_entity_id")
    if not d or d["raw_ok"]:
        continue
    o = read_log(f, "v3.1")
    prev = raw(data["v3_closedset"], f, "buyer_entity_id")
    e = BY_ID.get(d["raw"], {})
    flag = "  <- v3 时是对的" if prev == "E01" else ""
    print(f"\n{f}   选中 {d['raw']} ({e.get('name','?')}){flag}")
    print(f"   observed_name : {o.get('buyer_observed_name')}")
    print(f"   observed_tax  : {o.get('buyer_observed_tax_id')}")

print()
print("=" * 76)
print("v3.1 销售方名称错误明细")
print("=" * 76)
for f in files:
    v = raw(data["v3_1_evidence"], f, "seller_name")
    if v == GT["seller_name"]:
        continue
    v2v = raw(data["v2_prompt"], f, "seller_name")
    mark = "  [v2 时是对的]" if v2v == GT["seller_name"] else ""
    print(f"  {f:<10} {v}{mark}")

print()
print("=" * 76)
print("v3.1 数值字段错误明细")
print("=" * 76)
for f in files:
    probs = []
    for field in ("amount", "tax_amount", "total_with_tax"):
        v = raw(data["v3_1_evidence"], f, field)
        if v != GT[field]:
            probs.append(f"{field}={v}")
    if probs:
        v2bad = any(raw(data["v2_prompt"], f, x) != GT[x]
                    for x in ("amount", "tax_amount", "total_with_tax"))
        mark = "" if v2bad else "  [v2 时全对]"
        print(f"  {f:<10} " + "  ".join(probs) + mark)
