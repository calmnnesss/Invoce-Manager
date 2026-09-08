#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计 _normalize_field_value 的实际行为：逐字段对比日志里的 [1] 原始响应 与 [3] 值清洗后。

分类：
  dropped     raw 非空 -> clean 变 None（信息丢失）
  rewritten   raw 与 clean 都非空但不相等（格式改写）
  unchanged   完全没动
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_logs import (  # noqa: E402
    DEFAULT_LOG_DIR, EVAL_DIR, parse_log, canon,
)

gt_cfg = json.loads((EVAL_DIR / "ground_truth.json").read_text(encoding="utf-8"))
GT = gt_cfg["constant_fields"]
exclude = set(gt_cfg.get("exclude_files", []))

samples = {}
for p in sorted(Path(DEFAULT_LOG_DIR).glob("*.log")):
    s = parse_log(p)
    if not s or not s["file"] or s["file"] in exclude or s["status"] != "OK":
        continue
    cur = samples.get(s["file"])
    if cur is None or s["ts"] > cur["ts"]:
        samples[s["file"]] = s

rows = list(samples.values())
print(f"样本 {len(rows)} 张\n")

ALL_FIELDS = [
    "invoice_number", "invoice_code", "issue_date",
    "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id",
    "item_name", "amount", "tax_amount", "total_with_tax", "tax_rate",
]

stats = defaultdict(Counter)
dropped_examples = defaultdict(list)
rewritten_examples = defaultdict(list)

for s in rows:
    raw, clean = s["raw"], s["clean"]
    for f in ALL_FIELDS:
        rv, cv = raw.get(f), clean.get(f)
        rs = None if rv is None else str(rv).strip()
        cs = None if cv is None else str(cv).strip()
        if rs is None and cs is None:
            stats[f]["both_null"] += 1
        elif rs is not None and cs is None:
            stats[f]["dropped"] += 1
            dropped_examples[f].append((s["file"], rs))
        elif rs != cs:
            stats[f]["rewritten"] += 1
            rewritten_examples[f].append((s["file"], rs, cs))
        else:
            stats[f]["unchanged"] += 1

print(f"{'字段':<18}{'原样通过':>9}{'改写':>7}{'吞掉':>7}{'两边空':>8}")
for f in ALL_FIELDS:
    c = stats[f]
    print(f"{f:<18}{c['unchanged']:>9}{c['rewritten']:>7}{c['dropped']:>7}{c['both_null']:>8}")

print("\n=== 被吞掉的值（raw 非空，clean 变 None）===")
for f, items in dropped_examples.items():
    print(f"\n[{f}]  共 {len(items)} 条")
    gtv = canon(f, GT.get(f)) if f in GT else None
    for fn, v in items:
        verdict = ""
        if gtv is not None:
            verdict = "  <-- raw 本来就是对的！" if canon(f, v) == gtv else f"  (正确值应为 {gtv})"
        print(f"    {fn:<10} {v!r}{verdict}")

print("\n=== 被改写的值 ===")
for f, items in rewritten_examples.items():
    print(f"\n[{f}]  共 {len(items)} 条")
    for fn, a, b in items[:12]:
        print(f"    {fn:<10} {a!r} -> {b!r}")
    if len(items) > 12:
        print(f"    ... 另有 {len(items) - 12} 条")
