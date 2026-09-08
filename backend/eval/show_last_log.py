#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印最近 N 个 LLM dump 日志的头部，方便冒烟测试后快速查看。

    python eval/show_last_log.py            # 最新 1 条
    python eval/show_last_log.py -n 3       # 最新 3 条
    python eval/show_last_log.py --full     # 打印整个文件
"""
import argparse
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
LOG_DIR = EVAL_DIR.parent / "logs" / "llm"

ap = argparse.ArgumentParser()
ap.add_argument("-n", type=int, default=1)
ap.add_argument("--full", action="store_true")
ap.add_argument("--lines", type=int, default=48)
ap.add_argument("--log-dir", default=str(LOG_DIR))
args = ap.parse_args()

files = sorted(Path(args.log_dir).glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
if not files:
    raise SystemExit(f"没有日志: {args.log_dir}")

for p in files[: args.n]:
    print("=" * 78)
    print("LOG:", p.name)
    print("=" * 78)
    text = p.read_text(encoding="utf-8", errors="replace")
    if args.full:
        print(text)
    else:
        print("\n".join(text.splitlines()[: args.lines]))
    print()
