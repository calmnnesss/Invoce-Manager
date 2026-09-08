#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比同一份结果里几种不同口径的"正确率"，避免混淆。"""
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
PARTY = ["buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id"]

for label in ["baseline", "v2_prompt"]:
    p = RESULTS / f"{label}.json"
    if not p.exists():
        continue
    rows = json.loads(p.read_text(encoding="utf-8"))["per_sample"]
    n = len(rows)
    fields = list(rows[0]["fields"].keys())

    party_cells_ok = sum(1 for r in rows for f in PARTY if r["fields"][f]["raw_ok"])
    all_cells_ok = sum(1 for r in rows for f in fields if r["fields"][f]["raw_ok"])
    party_rows_ok = sum(1 for r in rows if all(r["fields"][f]["raw_ok"] for f in PARTY))
    all_rows_ok = sum(1 for r in rows if r["exact_match"])
    dir_ok = sum(1 for r in rows if r["direction_ok"])

    print(f"=== {label}  （{n} 张）===")
    print(f"  [字段格] 4 个主体字段        {party_cells_ok:>4}/{n * len(PARTY):<4} = {party_cells_ok / (n * len(PARTY)):6.1%}")
    print(f"  [字段格] 全部 {len(fields)} 个受评字段  {all_cells_ok:>4}/{n * len(fields):<4} = {all_cells_ok / (n * len(fields)):6.1%}")
    print(f"  [ 张 ] 方向未颠倒            {dir_ok:>4}/{n:<4} = {dir_ok / n:6.1%}")
    print(f"  [ 张 ] 4 个主体字段全对       {party_rows_ok:>4}/{n:<4} = {party_rows_ok / n:6.1%}")
    print(f"  [ 张 ] {len(fields)} 个字段全对 (Exact)  {all_rows_ok:>4}/{n:<4} = {all_rows_ok / n:6.1%}")
    print()
