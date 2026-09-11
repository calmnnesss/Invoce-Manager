#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 logs/llm 下的日志按 prompt 版本归档到子目录。

在容器内运行（logs/llm 由容器以 root 创建，宿主用户无写权限）：
    docker cp eval/archive_logs.py invoice_backend:/tmp/archive_logs.py
    docker exec invoice_backend python /tmp/archive_logs.py --date 2026-09-08

score_logs.py 用的是非递归的 glob("*.log")，所以归档进子目录后
默认扫描范围自动变干净；要评归档数据显式加 --log-dir 即可。
"""
import argparse
import re
import shutil
from collections import defaultdict
from pathlib import Path

PROMPT_RE = re.compile(r"^Prompt\s*:\s*(\S+)\s*$", re.M)
POST_RE = re.compile(r"^Postprocess\s*:\s*(\S+)\s*$", re.M)
ENT_RE = re.compile(r"^Entities\s*:\s*(\S+?)#", re.M)

ap = argparse.ArgumentParser()
ap.add_argument("--log-dir", default="/app/logs/llm")
ap.add_argument("--date", required=True, help="归档目录的日期前缀，如 2026-09-08")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

log_dir = Path(args.log_dir)
groups = defaultdict(list)
for p in sorted(log_dir.glob("*.log")):
    head = p.read_text(encoding="utf-8", errors="replace")[:1200]
    m = PROMPT_RE.search(head)
    v = m.group(1) if m and m.group(1) != "(unknown)" else "v1"
    # 同一个 prompt 版本下，主体清单和后处理版本也会变 —— 三个维度都要进目录名，
    # 否则几批不同配置的结果会被归到一起，以后分不出来。
    em = ENT_RE.search(head)
    pm = POST_RE.search(head)
    parts = [v]
    if em:
        parts.append(em.group(1))
    parts.append(f"pp-{pm.group(1)}" if pm else "pp-none")
    groups["_".join(parts)].append(p)

if not groups:
    raise SystemExit("没有待归档的日志")

# 版本数量少的（通常是冒烟测试）单独一个目录，避免目录名误导
for v, files in sorted(groups.items()):
    tag = f"{args.date}_{v}" + ("-smoke" if len(files) <= 3 else "")
    dest = log_dir / "archive" / tag
    print(f"{v:<6} {len(files):>3} 条 -> archive/{tag}/")
    if args.dry_run:
        continue
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.move(str(p), str(dest / p.name))

left = list(log_dir.glob("*.log"))
print(f"\n归档后当前目录剩余 .log: {len(left)}")
