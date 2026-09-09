#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐字段原始值对照表 —— 不做任何归因、不分类、不下结论。

只做一件事：把日志 [1] 原始响应 里每个字段的值，和 ground_truth.json
里的标注值并排列出来，标一个是否匹配。归因由人来做。

与 score_logs.py 的区别：
  score_logs.py  给汇总指标和错误类型分类（会带入判断）
  match_fields.py 只给原始事实

用法
----
    # 全部字段、全部样本
    python eval/match_fields.py --prompt-version v3.1 --entities 2026-09-08-no-E05

    # 只看某几个字段，且只列不匹配的
    python eval/match_fields.py --prompt-version v3.1 --entities 2026-09-08-no-E05 \
        --fields seller_tax_id --only-errors

    # 导出 CSV 自己在表格里分析
    python eval/match_fields.py --prompt-version v3.1 --csv out.csv

说明
----
- "原始值" 列是模型输出的原文，未做任何处理。
- "匹配" 列用与 score_logs 相同的归一化规则判定（日期格式、金额去符号、
  税号去空格等）。若原文与 GT 字面不同但归一后相同，标记为 ✓(归一后)。
- ground_truth.json 里没有标注的字段（invoice_number / item_name /
  buyer_observed_* / party_evidence 等）一律列出但不判定。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_logs import (  # noqa: E402
    DEFAULT_LOG_DIR, EVAL_DIR, canon, parse_log,
)

import json  # noqa: E402


def key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--gt", default=str(EVAL_DIR / "ground_truth.json"))
    ap.add_argument("--prompt-version", default=None)
    ap.add_argument("--entities", default=None)
    ap.add_argument("--fields", default=None,
                    help="逗号分隔，只看这些字段；默认全部")
    ap.add_argument("--files", default=None,
                    help="逗号分隔，只看这些图，如 b2,b22")
    ap.add_argument("--only-errors", action="store_true", help="只列不匹配的字段")
    ap.add_argument("--csv", default=None, help="同时导出 CSV 到该路径")
    args = ap.parse_args()

    gt_cfg = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    GT = gt_cfg["constant_fields"]
    exclude = set(gt_cfg.get("exclude_files", []))

    # 收集样本，同名文件取最新
    latest = {}
    for p in sorted(Path(args.log_dir).glob("*.log")):
        s = parse_log(p)
        if not s or not s["file"] or s["file"] in exclude or s["status"] != "OK":
            continue
        if args.prompt_version and s["prompt_version"] != args.prompt_version:
            continue
        if args.entities and args.entities not in (s["entities_version"] or ""):
            continue
        cur = latest.get(s["file"])
        if cur is None or s["ts"] > cur["ts"]:
            latest[s["file"]] = s

    if not latest:
        sys.exit("没有匹配到样本，检查 --log-dir / --prompt-version / --entities")

    want_files = None
    if args.files:
        want_files = {f.strip() if f.strip().endswith(".jpg") else f.strip() + ".jpg"
                      for f in args.files.split(",")}
    want_fields = [f.strip() for f in args.fields.split(",")] if args.fields else None

    samples = sorted(latest.values(), key=lambda s: key(s["file"]))
    rows = []
    for s in samples:
        if want_files and s["file"] not in want_files:
            continue
        raw = s["raw"]
        # 字段顺序：先有标注的，再没标注的
        keys = [k for k in GT if k in raw] + [k for k in raw if k not in GT]
        for k in keys:
            if want_fields and k not in want_fields:
                continue
            val = raw.get(k)
            gtv = GT.get(k)
            if gtv is None:
                verdict = "-"
            elif str(val) == str(gtv):
                verdict = "✓"
            elif canon(k, val) == canon(k, gtv):
                verdict = "✓(归一后)"
            else:
                verdict = "✗"
            if args.only_errors and verdict != "✗":
                continue
            rows.append({
                "file": s["file"],
                "field": k,
                "gt": "" if gtv is None else gtv,
                "raw": "" if val is None else val,
                "match": verdict,
            })

    cur_file = None
    n_err = 0
    for r in rows:
        if r["file"] != cur_file:
            cur_file = r["file"]
            print(f"\n{cur_file}")
        if r["match"] == "✗":
            n_err += 1
        gt_show = r["gt"] if r["gt"] != "" else "(未标注)"
        print(f"  {r['field']:<22} {r['match']:<10}")
        print(f"  {'':<22} GT   {gt_show}")
        print(f"  {'':<22} raw  {r['raw']}")

    print()
    print("=" * 70)
    print(f"共列出 {len(rows)} 个字段值，其中不匹配 {n_err} 个，涉及 "
          f"{len({r['file'] for r in rows if r['match'] == '✗'})} 张图")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["file", "field", "gt", "raw", "match"])
            w.writeheader()
            w.writerows(rows)
        print(f"已导出 CSV: {args.csv}")


if __name__ == "__main__":
    main()
