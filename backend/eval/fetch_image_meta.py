#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取每张测试图的像素尺寸，写入 eval/image_meta.json。

尺寸用来区分横图（发票的正常方向）和竖图（被旋转 90/270°），
score_logs.py 会据此做"按方向分组"的准确率拆解。

只读 JPEG 的 SOF 段，不依赖 Pillow。需要后端在跑。

    python eval/fetch_image_meta.py --base-url http://localhost:18080
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import urllib.request
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import load_file_ids  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
DEFAULT_LOG_DIR = BACKEND_DIR / "logs" / "llm"

LOG_RE = re.compile(r"^[\d\-]+_invoice-(?P<iid>\d+)_(?P<fname>.+)_[^_]+_[^_.]+\.log$")

SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_size(data: bytes):
    """从 JPEG 字节流里读出 (宽, 高)。"""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in SOF_MARKERS:
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return [w, h]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + seg_len
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:18080")
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--out", default=str(EVAL_DIR / "image_meta.json"))
    args = ap.parse_args()

    # 文件名 -> [invoice_id...]，优先用固化的 eval/file_ids.json。
    # 同一文件可能对应多条记录（重复上传），而其中部分记录取不到文件，
    # 所以逐个试，拿到一个能读出尺寸的就停。
    mapping = load_file_ids(args.log_dir)

    out = {}
    for fname, iids in sorted(mapping.items()):
        size = None
        used = None
        for iid in iids:
            url = f"{args.base_url}/api/invoices/{iid}/file"
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = r.read()
                size = jpeg_size(data)
            except Exception:                       # noqa: BLE001
                continue
            if size:
                used = iid
                break
        if not size:
            print(f"  ! {fname} 失败（试过 id={iids}）")
            continue
        out[fname] = size
        orient = "横" if size[0] >= size[1] else "竖"
        print(f"  {fname:<12} id={used:<4} {size[0]}x{size[1]}  {orient}")

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    landscape = sum(1 for v in out.values() if v[0] >= v[1])
    print(f"\n共 {len(out)} 张：横 {landscape}，竖 {len(out) - landscape}")
    print(f"已写入 {args.out}")


if __name__ == "__main__":
    main()
