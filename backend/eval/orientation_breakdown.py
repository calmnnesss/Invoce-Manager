#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按图片方向（横/竖）拆解各项指标，并标注样本量带来的不确定性。

横图样本极少（7 张），任何比较都要连着置信区间一起看，
否则一张图的波动就会被误读成方向带来的系统性差异。

    python eval/orientation_breakdown.py
"""

from __future__ import annotations

import json
from math import sqrt
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
PARTY = ["buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 置信区间。小样本下比正态近似可靠得多。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def fmt(k: int, n: int) -> str:
    if n == 0:
        return "   -"
    lo, hi = wilson(k, n)
    return f"{k:>2}/{n:<2} = {k / n:5.1%}   [{lo:.0%}–{hi:.0%}]"


def analyse(label: str):
    p = RESULTS / f"{label}.json"
    if not p.exists():
        return
    rows = json.loads(p.read_text(encoding="utf-8"))["per_sample"]
    fields = list(rows[0]["fields"].keys())

    groups = {"横向": [], "竖向": []}
    for r in rows:
        if r["orientation"] == "landscape":
            groups["横向"].append(r)
        elif r["orientation"] == "portrait":
            groups["竖向"].append(r)

    print("=" * 70)
    print(f"{label}")
    print("=" * 70)
    print(f"{'指标':<22}{'横向 (7 张)':<28}{'竖向 (44 张)'}")
    print("-" * 70)

    metrics = [
        ("方向正确率",
         lambda g: (sum(r["direction_ok"] for r in g), len(g))),
        ("Exact Match",
         lambda g: (sum(r["exact_match"] for r in g), len(g))),
        ("字段综合准确率",
         lambda g: (sum(1 for r in g for f in fields if r["fields"][f]["raw_ok"]),
                    len(g) * len(fields))),
        ("主体4字段准确率",
         lambda g: (sum(1 for r in g for f in PARTY if r["fields"][f]["raw_ok"]),
                    len(g) * len(PARTY))),
        ("主体4字段全对(张)",
         lambda g: (sum(1 for r in g if all(r["fields"][f]["raw_ok"] for f in PARTY)),
                    len(g))),
    ]

    out = {}
    for name, fn in metrics:
        a = fn(groups["横向"])
        b = fn(groups["竖向"])
        out[name] = (a, b)
        print(f"{name:<22}{fmt(*a):<28}{fmt(*b)}")

    print("-" * 70)
    print(f"{'差值 (横 - 竖)':<22}")
    for name, (a, b) in out.items():
        pa = a[0] / a[1] if a[1] else 0
        pb = b[0] / b[1] if b[1] else 0
        la, ha = wilson(*a)
        lb, hb = wilson(*b)
        overlap = not (ha < lb or hb < la)
        mark = "区间重叠，差异不显著" if overlap else "区间不重叠"
        print(f"  {name:<20}{(pa - pb) * 100:>+7.1f}pt    {mark}")
    print()


for lb in ["baseline", "v2_prompt"]:
    analyse(lb)
