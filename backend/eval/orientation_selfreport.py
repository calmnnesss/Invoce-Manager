#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估 v2 prompt 里模型自报的 image_orientation 字段准不准。

只能做部分判定：
    图片是竖向(高>宽) -> 真实旋转角必然是 90 或 270
    图片是横向(宽>高) -> 真实旋转角必然是 0 或 180
所以只能判"明确错"，无法在 0/180 或 90/270 之间进一步区分。

顺带看一个更关键的问题：自报方向准不准，和买卖方有没有填对，相关吗？

    python eval/orientation_selfreport.py --log-dir logs/llm/archive/2026-09-07_v1-v2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))
from score_logs import parse_log, MARK_RAW, extract_json_after  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--log-dir", default=str(EVAL_DIR.parent / "logs" / "llm"))
ap.add_argument("--result", default="v2_prompt")
args = ap.parse_args()

meta = json.loads((EVAL_DIR / "image_meta.json").read_text(encoding="utf-8"))
res = json.loads((EVAL_DIR / "results" / f"{args.result}.json").read_text(encoding="utf-8"))
direction_ok = {r["file"]: r["direction_ok"] for r in res["per_sample"]}

latest = {}
for p in sorted(Path(args.log_dir).glob("*.log")):
    s = parse_log(p)
    if not s or not s["file"] or s["prompt_version"] != "v2" or s["status"] != "OK":
        continue
    if s["file"] not in direction_ok:
        continue
    cur = latest.get(s["file"])
    if cur is None or s["ts"] > cur["ts"]:
        latest[s["file"]] = s

rows = []
for fname, s in sorted(latest.items()):
    dims = meta.get(fname)
    if not dims:
        continue
    true_group = "0/180" if dims[0] >= dims[1] else "90/270"
    said = str(s["raw"].get("image_orientation", "")).strip().rstrip("°")
    said_group = "0/180" if said in ("0", "180") else ("90/270" if said in ("90", "270") else "?")
    verdict = "无法判定" if said_group == "?" else ("组内" if said_group == true_group else "明确错")
    rows.append({
        "file": fname,
        "真实组": true_group,
        "自报": said,
        "判定": verdict,
        "买卖方对": direction_ok.get(fname),
        "evidence": str(s["raw"].get("party_evidence", ""))[:60],
    })

n = len(rows)
print(f"样本 {n} 张\n")

print("模型自报的旋转角分布：")
for v, c in Counter(r["自报"] for r in rows).most_common():
    print(f"  {v or '(空)':<8}{c:>3} 次")
print()

print("自报方向 vs 图片真实长宽比：")
for v, c in Counter(r["判定"] for r in rows).most_common():
    print(f"  {v:<10}{c:>3} 张  ({c / n:.1%})")
print()

print("交叉：自报方向判定 × 买卖方是否填对")
tab = Counter((r["判定"], bool(r["买卖方对"])) for r in rows)
print(f"  {'':<10}{'买卖方对':>10}{'买卖方错':>10}")
for verdict in ["组内", "明确错", "无法判定"]:
    ok = tab.get((verdict, True), 0)
    bad = tab.get((verdict, False), 0)
    if ok + bad:
        print(f"  {verdict:<10}{ok:>10}{bad:>10}   (正确率 {ok / (ok + bad):.1%})")
print()

wrong = [r for r in rows if r["判定"] == "明确错"]
print(f"自报方向明确错的 {len(wrong)} 张里，买卖方仍填对的有 "
      f"{sum(1 for r in wrong if r['买卖方对'])} 张")
print()

print("样例（前 12 张）：")
print(f"  {'文件':<10}{'真实':<9}{'自报':<7}{'判定':<10}{'买卖方'}")
for r in rows[:12]:
    print(f"  {r['file']:<10}{r['真实组']:<9}{r['自报']:<7}{r['判定']:<10}"
          f"{'对' if r['买卖方对'] else '错'}")
