#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""触发一轮全量重跑，并等待结果落盘。

为什么不从前端重新导入图片：
    重新导入会在库里生成 51 条新记录，invoice_id 全部变化，
    image_meta.json 里的 文件名->id 映射失效，旧记录还留在库里需要额外去重。
    这个脚本走 /api/invoices/batch-reprocess，在原有记录上重跑，id 不变。

用法：
    python eval/run_batch.py --dry-run              # 只列出将要重跑的发票，不发请求
    python eval/run_batch.py                        # 真跑，并轮询到全部落盘
    python eval/run_batch.py --ids 96,84,75         # 只重跑指定几张（小规模验证）

跑完之后：
    python eval/score_logs.py --prompt-version v2 --label v2_prompt --compare baseline
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import load_file_ids  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
LOG_DIR = BACKEND_DIR / "logs" / "llm"

LOG_RE = re.compile(r"^(?P<ts>[\d\-]+)_invoice-(?P<iid>\d+)_(?P<fname>.+)_[^_]+_[^_.]+\.log$")
PROMPT_RE = re.compile(r"^Prompt\s*:\s*(\S+)\s*$", re.M)


def discover_targets(log_dir: Path, exclude: set[str]):
    """取 文件名 -> {invoice_id...} 的映射。

    优先用固化的 eval/file_ids.json，这样日志归档后仍能正常工作。
    """
    by_file = defaultdict(set)
    for fname, iids in load_file_ids(log_dir).items():
        if fname in exclude:
            continue
        by_file[fname].update(iids)
    return by_file


def count_done(log_dir: Path, since: str, version: str) -> set[str]:
    """统计 since 之后落盘的、指定 prompt 版本的日志覆盖了哪些文件。"""
    done = set()
    for p in log_dir.glob("*.log"):
        m = LOG_RE.match(p.name)
        if not m or m.group("ts") < since:
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:800]
        except OSError:
            continue
        pm = PROMPT_RE.search(head)
        if version and (not pm or pm.group(1) != version):
            continue
        done.add(m.group("fname"))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:18080")
    ap.add_argument("--log-dir", default=str(LOG_DIR))
    ap.add_argument("--gt", default=str(EVAL_DIR / "ground_truth.json"))
    ap.add_argument("--ids", default=None, help="逗号分隔，只重跑这些 invoice_id")
    ap.add_argument("--expect-version", default="v2", help="等待哪个 prompt 版本的日志落盘")
    ap.add_argument("--timeout", type=int, default=1800, help="最长等待秒数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gt_cfg = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    exclude = set(gt_cfg.get("exclude_files", []))
    log_dir = Path(args.log_dir)

    by_file = discover_targets(log_dir, exclude)

    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        expected_files = {f for f, s in by_file.items() if s & set(ids)}
    else:
        # 同一文件在库里可能有多条记录（重复上传），只重跑 id 最小的那条
        ids, expected_files, dupes = [], set(), []
        for fname, idset in sorted(by_file.items()):
            chosen = min(idset)
            ids.append(chosen)
            expected_files.add(fname)
            if len(idset) > 1:
                dupes.append((fname, sorted(idset), chosen))
        if dupes:
            print("注意：以下文件在库里有多条记录，只重跑 id 最小的那条")
            for fname, allids, chosen in dupes:
                print(f"  {fname}: {allids} -> 用 {chosen}")
            print()

    ids = sorted(set(ids))
    print(f"待重跑 {len(ids)} 条，覆盖 {len(expected_files)} 个文件")
    print(f"  invoice_ids = {ids}")
    if exclude:
        print(f"  已排除: {sorted(exclude)}")

    if args.dry_run:
        print("\n--dry-run，未发送请求。")
        return

    since = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    url = f"{args.base_url}/api/invoices/batch-reprocess"
    body = json.dumps({"invoice_ids": ids}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"\n已提交: HTTP {r.status}  {r.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"提交失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")

    print(f"\n等待落盘（prompt={args.expect_version}）…  Ctrl-C 可随时中断，日志不会丢")
    start = time.time()
    last = -1
    while time.time() - start < args.timeout:
        done = count_done(log_dir, since, args.expect_version)
        n = len(done & expected_files)
        if n != last:
            bar = "#" * int(n / max(len(expected_files), 1) * 40)
            print(f"  {n:>3}/{len(expected_files)}  {bar}")
            last = n
        if n >= len(expected_files):
            print(f"\n完成，用时 {time.time() - start:.0f}s")
            break
        time.sleep(5)
    else:
        done = count_done(log_dir, since, args.expect_version)
        missing = sorted(expected_files - done)
        print(f"\n超时。仍缺 {len(missing)} 个: {missing}")

    print("\n下一步：")
    print(f"  python eval/score_logs.py --prompt-version {args.expect_version} "
          f"--label {args.expect_version}_prompt --compare baseline")


if __name__ == "__main__":
    main()
