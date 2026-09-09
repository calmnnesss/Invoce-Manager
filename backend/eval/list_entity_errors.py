#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出闭集模式下购方主体 ID 选错的样本，并附上模型自报读到的内容。

buyer_observed_* 只存在于日志，不在 results/*.json 里，所以要回日志读。

    python eval/list_entity_errors.py --result v3_1_evidence --version v3.1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
LOGS = EVAL.parent / "logs" / "llm"


def read_raw(fname: str, version: str) -> dict:
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


def key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


ap = argparse.ArgumentParser()
ap.add_argument("--result", default="v3_1_evidence")
ap.add_argument("--version", default="v3.1")
args = ap.parse_args()

rows = json.loads((EVAL / "results" / f"{args.result}.json").read_text(encoding="utf-8"))["per_sample"]
meta = json.loads((EVAL / "image_meta.json").read_text(encoding="utf-8"))
ents = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                  .read_text(encoding="utf-8"))["entities"]
ENT = {e["id"]: e for e in ents}

GT_BUYER = "深圳市购机汇网络有限公司"
GT_BUYER_TAX = "440300083885931"

bad = [r for r in rows
       if "buyer_entity_id" in r["fields"] and not r["fields"]["buyer_entity_id"]["raw_ok"]]

print(f"购方主体 ID 选错: {len(bad)}/{len(rows)}\n")
print("=" * 74)

buckets = {"读错地名": [], "读的是销售方栏": [], "读对了却选错": [], "其他": []}

for r in sorted(bad, key=lambda x: key(x["file"])):
    f = r["file"]
    o = read_raw(f, args.version)
    pick = r["fields"]["buyer_entity_id"]["raw"]
    dims = meta.get(f)
    orient = "横" if dims and dims[0] >= dims[1] else "竖"
    on = o.get("buyer_observed_name") or ""
    ot = o.get("buyer_observed_tax_id") or ""

    if on == GT_BUYER and ot == GT_BUYER_TAX:
        cat = "读对了却选错"
    elif "晶" in on or "乐" in on or "贸易" in on or ot.startswith("9144"):
        cat = "读的是销售方栏"
    elif "广州" in on or "东莞" in on:
        cat = "读错地名"
    else:
        cat = "其他"
    buckets[cat].append(f)

    print(f"\n{f:<9}({orient}图)   选中 {pick} = {ENT.get(pick, {}).get('name', '?')}")
    print(f"           读到名称 : {on}")
    print(f"           读到税号 : {ot}")
    print(f"           销售方填 : {o.get('seller_name')}")
    print(f"           归类     : {cat}")

print()
print("=" * 74)
print("按成因归类")
print("=" * 74)
for k, v in buckets.items():
    if v:
        print(f"  {k:<16}{len(v):>2} 张   " + " ".join(x.replace('.jpg', '') for x in v))
