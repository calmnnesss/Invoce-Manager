#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按图片方向拆解主体字段的各类错误，重点看字形错误是否受方向影响。

版面理解不受方向影响，不代表字符识别不受影响 —— 旋转+重采样理论上会
损失笔画细节。这是当前最大瓶颈（D 类字形错误），值得单独验证。
"""
import json
from math import sqrt
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
PARTY = ["buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id"]
KINDS = ["exact", "swapped", "swapped_near_miss", "near_miss", "unrelated", "missing"]
CN = {"exact": "完全正确", "swapped": "对调", "swapped_near_miss": "对调+字形错",
      "near_miss": "字形错", "unrelated": "幻觉/串栏", "missing": "空值"}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    return (((p + z * z / (2 * n)) - z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d,
            ((p + z * z / (2 * n)) + z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d)


rows = json.loads((R / "v2_prompt.json").read_text(encoding="utf-8"))["per_sample"]
groups = {"横向": [r for r in rows if r["orientation"] == "landscape"],
          "竖向": [r for r in rows if r["orientation"] == "portrait"]}

print("v2 主体字段错误类型 × 图片方向（单位：字段格）\n")
print(f"{'错误类型':<16}{'横向 (28 格)':<26}{'竖向 (176 格)'}")
print("-" * 68)
for k in KINDS:
    line = f"{CN[k]:<16}"
    for g in ["横向", "竖向"]:
        items = groups[g]
        tot = len(items) * len(PARTY)
        c = sum(1 for r in items for f in PARTY if r["fields"][f].get("kind") == k)
        line += f"{c:>3}/{tot:<4}{c / tot:>7.1%}          " if tot else "     -    "
    print(line)

print("\n" + "-" * 68)
print("字形类错误合计（near_miss + swapped_near_miss）：")
for g in ["横向", "竖向"]:
    items = groups[g]
    tot = len(items) * len(PARTY)
    c = sum(1 for r in items for f in PARTY
            if r["fields"][f].get("kind") in ("near_miss", "swapped_near_miss"))
    lo, hi = wilson(c, tot)
    print(f"  {g}  {c:>3}/{tot:<4} = {c / tot:6.1%}   [{lo:.0%}–{hi:.0%}]")

print("\n按张统计 —— 含至少一个字形错误的图片：")
for g in ["横向", "竖向"]:
    items = groups[g]
    c = sum(1 for r in items if any(
        r["fields"][f].get("kind") in ("near_miss", "swapped_near_miss") for f in PARTY))
    lo, hi = wilson(c, len(items))
    print(f"  {g}  {c:>2}/{len(items):<3} = {c / len(items):6.1%}   [{lo:.0%}–{hi:.0%}]")
