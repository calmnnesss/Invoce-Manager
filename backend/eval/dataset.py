#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据集身份信息：图片文件名 -> invoice_id 的映射。

为什么单独抽出来：
    这个映射原本是从 logs/llm/ 的日志文件名里反推的，等于让"数据集的身份"
    寄生在调试日志上。日志一归档，run_batch / fetch_image_meta 就全废了。
    现在固化到 eval/file_ids.json，日志随便搬。

    python eval/dataset.py --rebuild      # 从现有日志重建映射并落盘
    python eval/dataset.py                # 打印当前映射
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
DEFAULT_LOG_DIR = BACKEND_DIR / "logs" / "llm"
FILE_IDS_PATH = EVAL_DIR / "file_ids.json"

LOG_RE = re.compile(r"^[\d\-]+_invoice-(?P<iid>\d+)_(?P<fname>.+)_[^_]+_[^_.]+\.log$")


def derive_from_logs(log_dir: Path | str = DEFAULT_LOG_DIR) -> dict[str, list[int]]:
    """扫描日志文件名，还原 文件名 -> [invoice_id...]。"""
    mapping: dict[str, list[int]] = {}
    for p in sorted(Path(log_dir).glob("*.log")):
        m = LOG_RE.match(p.name)
        if not m:
            continue
        fname = m.group("fname")
        iid = int(m.group("iid"))
        mapping.setdefault(fname, [])
        if iid not in mapping[fname]:
            mapping[fname].append(iid)
    return {k: sorted(v) for k, v in sorted(mapping.items())}


def load_file_ids(log_dir: Path | str = DEFAULT_LOG_DIR,
                  path: Path | str = FILE_IDS_PATH) -> dict[str, list[int]]:
    """优先读固化的映射；没有就临时从日志推。"""
    p = Path(path)
    if p.exists():
        return {k: list(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    return derive_from_logs(log_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--out", default=str(FILE_IDS_PATH))
    ap.add_argument("--rebuild", action="store_true", help="从日志重建并写入 file_ids.json")
    args = ap.parse_args()

    if args.rebuild:
        mapping = derive_from_logs(args.log_dir)
        Path(args.out).write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已从 {args.log_dir} 重建映射，共 {len(mapping)} 个文件 -> {args.out}")
    else:
        mapping = load_file_ids(args.log_dir, args.out)
        print(f"来源: {'file_ids.json' if Path(args.out).exists() else '日志（未固化）'}")
        print(f"共 {len(mapping)} 个文件")

    dupes = {k: v for k, v in mapping.items() if len(v) > 1}
    if dupes:
        print("\n对应多条发票记录的文件（重复上传）：")
        for k, v in dupes.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
