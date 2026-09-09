#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追踪指定样本在各版本下的买卖方输出，判断「清单污染」与「版面混淆」哪个是主因。

关键点：v1 / v2 的 prompt 里没有任何主体清单。
若这些样本在 v1/v2 就已经把销售方填成本公司，说明现象先于清单存在。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
LOGS = EVAL.parent / "logs" / "llm"

RUNS = [
    ("v1", None, "v1 无清单"),
    ("v2", None, "v2 无清单"),
    ("v3", None, "v3 五主体"),
    ("v3.1", "no-E05", None),          # 占位，下面按指纹区分
]


def read_raw(fname: str, version: str, entities: str | None = None,
             exclude_entities: str | None = None):
    for p in sorted(LOGS.rglob(f"*_{fname}_*.log"), reverse=True):
        t = p.read_text(encoding="utf-8", errors="replace")
        if f"Prompt     : {version}" not in t:
            continue
        if entities and entities not in t:
            continue
        if exclude_entities and exclude_entities in t:
            continue
        i = t.find("[1] 原始响应")
        j = t.find("{", i)
        d = 0
        for k in range(j, len(t)):
            if t[k] == "{":
                d += 1
            elif t[k] == "}":
                d -= 1
                if d == 0:
                    try:
                        return json.loads(t[j:k + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


ap = argparse.ArgumentParser()
ap.add_argument("--files", default="b2,b22,b24,b9")
args = ap.parse_args()

VARIANTS = [
    ("v1",   dict(version="v1"),                                  "v1  （prompt 中无清单）"),
    ("v2",   dict(version="v2"),                                  "v2  （prompt 中无清单）"),
    ("v3",   dict(version="v3"),                                  "v3  （五主体，含 E05）"),
    ("v3.1a", dict(version="v3.1", exclude_entities="no-E05"),    "v3.1（五主体，含 E05）"),
    ("v3.1b", dict(version="v3.1", entities="no-E05"),            "v3.1（四主体，无 E05）"),
    ("v3.2", dict(version="v3.2"),                                "v3.2（四…五主体，精简字段）"),
]

for fname in [f.strip() + ".jpg" for f in args.files.split(",")]:
    print("=" * 78)
    print(fname)
    print("=" * 78)
    for _, kw, label in VARIANTS:
        o = read_raw(fname, **kw)
        if not o:
            print(f"\n  {label:<26} (无日志)")
            continue
        bn = o.get("buyer_name") or o.get("buyer_observed_name")
        bt = o.get("buyer_tax_id") or o.get("buyer_observed_tax_id")
        bid = o.get("buyer_entity_id")
        print(f"\n  {label}")
        print(f"     购买方 : {bn}" + (f"   [ID={bid}]" if bid else ""))
        print(f"     销售方 : {o.get('seller_name')}")
        ev = o.get("party_evidence")
        if ev:
            print(f"     判据   : {ev}")
    print()
