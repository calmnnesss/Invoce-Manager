#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""触发一轮全量重跑，并等待结果落盘。

为什么不从前端重新导入图片：
    重新导入会在库里生成 51 条新记录，invoice_id 全部变化，
    image_meta.json 里的 文件名->id 映射失效，旧记录还留在库里需要额外去重。
    这个脚本走 /api/invoices/batch-reprocess，在原有记录上重跑，id 不变。

为什么用文件名快照而不是时间戳：
    日志文件名里的时间戳由容器生成（UTC），而本脚本跑在 WSL 宿主上（UTC+8）。
    用 datetime.now() 算一个"起始时间"再比大小，会把刚生成的日志全判成旧的，
    进度永远停在 0。改成提交前拍一张文件名快照，之后只数新出现的文件，
    对时区、时钟漂移都免疫。

用法：
    python eval/run_batch.py --dry-run              # 只列出将要重跑的发票
    python eval/run_batch.py                        # 真跑，并轮询到全部落盘
    python eval/run_batch.py --ids 96,84,75         # 只重跑指定几张
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import load_file_ids  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
LOG_DIR = BACKEND_DIR / "logs" / "llm"

LOG_RE = re.compile(r"^(?P<ts>[\d\-]+)_invoice-(?P<iid>\d+)_(?P<fname>.+)_[^_]+_(?P<st>[^_.]+)\.log$")
PROMPT_RE = re.compile(r"^Prompt\s*:\s*(\S+)\s*$", re.M)


def discover_targets(log_dir: Path, exclude: set[str]):
    """取 文件名 -> {invoice_id...} 的映射。优先用固化的 eval/file_ids.json。"""
    by_file = defaultdict(set)
    for fname, iids in load_file_ids(log_dir).items():
        if fname in exclude:
            continue
        by_file[fname].update(iids)
    return by_file


def scan(log_dir: Path, version: str | None):
    """扫描当前目录，返回 {日志文件名: (图片名, 状态, prompt版本)}。"""
    out = {}
    for p in log_dir.glob("*.log"):
        m = LOG_RE.match(p.name)
        if not m:
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:1200]
        except OSError:
            continue
        pm = PROMPT_RE.search(head)
        ver = pm.group(1) if pm else "v1"
        out[p.name] = (m.group("fname"), m.group("st"), ver)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:18080")
    ap.add_argument("--log-dir", default=str(LOG_DIR))
    ap.add_argument("--gt", default=str(EVAL_DIR / "ground_truth.json"))
    ap.add_argument("--ids", default=None, help="逗号分隔，只重跑这些 invoice_id")
    ap.add_argument("--expect-version", default="v3.1", help="等待哪个 prompt 版本落盘")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--poll", type=int, default=5, help="轮询间隔秒")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gt_cfg = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    exclude = set(gt_cfg.get("exclude_files", []))
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    by_file = discover_targets(log_dir, exclude)

    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        expected_files = {f for f, s in by_file.items() if s & set(ids)}
    else:
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

    before = set(scan(log_dir, None))
    print(f"\n提交前目录里已有 {len(before)} 个日志文件（作为基线快照）")

    url = f"{args.base_url}/api/invoices/batch-reprocess"
    body = json.dumps({"invoice_ids": ids}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"已提交: HTTP {r.status}  {r.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"提交失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")

    print(f"\n等待落盘（prompt={args.expect_version}）… Ctrl-C 可随时中断，日志不会丢")
    start = time.time()
    last_n = -1
    stalled = 0
    while time.time() - start < args.timeout:
        cur = scan(log_dir, args.expect_version)
        new = {k: v for k, v in cur.items() if k not in before}
        done_ok = {v[0] for v in new.values() if v[1] == "OK" and v[2] == args.expect_version}
        bad = [(k, v) for k, v in new.items() if v[1] != "OK"]
        wrong_ver = {v[2] for v in new.values() if v[2] != args.expect_version}

        n = len(done_ok & expected_files)
        el = int(time.time() - start)
        if n != last_n:
            bar = "#" * int(n / max(len(expected_files), 1) * 30)
            rate = f"{el / n:.1f}s/张" if n else "-"
            print(f"  [{el:>4}s] {n:>3}/{len(expected_files)}  {bar:<30} {rate}")
            if bad:
                print(f"         失败 {len(bad)} 条: "
                      + ", ".join(f"{v[0]}({v[1]})" for _, v in bad[:5]))
            if wrong_ver:
                print(f"         注意：出现了非预期版本 {sorted(wrong_ver)}，"
                      f"检查 .env 的 PROMPT_VERSION")
            last_n = n
            stalled = 0
        else:
            stalled += args.poll
            if stalled and stalled % 60 == 0:
                miss = sorted(expected_files - done_ok,
                              key=lambda f: int(f.lstrip("b").split(".")[0]))
                print(f"  [{el:>4}s] {n}/{len(expected_files)} 已 {stalled}s 无进展；"
                      f"新日志共 {len(new)} 个，仍缺 {len(miss)}: "
                      + " ".join(x.replace('.jpg', '') for x in miss[:12])
                      + (" …" if len(miss) > 12 else ""))
                print("         排查：docker logs invoice_backend --tail 30")

        if n >= len(expected_files):
            print(f"\n完成，用时 {el}s")
            break
        time.sleep(args.poll)
    else:
        cur = scan(log_dir, args.expect_version)
        new = {k: v for k, v in cur.items() if k not in before}
        done_ok = {v[0] for v in new.values() if v[1] == "OK"}
        missing = sorted(expected_files - done_ok)
        print(f"\n超时。仍缺 {len(missing)} 个: {missing}")

    print("\n下一步：")
    print(f"  python3 eval/score_logs.py --prompt-version {args.expect_version} "
          f"--label {args.expect_version}_run --compare baseline")


if __name__ == "__main__":
    main()
