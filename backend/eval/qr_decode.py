#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解码发票左上角的二维码，输出结构化字段。

增值税发票二维码通常是逗号分隔串：
    01,<票种>,<发票代码>,<发票号码>,<不含税金额>,<开票日期YYYYMMDD>,<校验码后6位>,<随机码>

这条线路是确定性的：要么解出来，要么解不出来，不存在"读了个像的"。
用 cv2.QRCodeDetector；原图很大，先缩放再检测，失败时逐级放大重试。

    python /tmp/qr_decode.py --image-dir /tmp/ocr_imgs --out /tmp/qr_result.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2

try:
    from pyzbar import pyzbar
except Exception:  # noqa: BLE001
    pyzbar = None


def parse_payload(text: str) -> dict:
    """把二维码原文解析成字段。解析不出来就只返回原文。"""
    out = {"qr_raw": text}
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 6:
        return out
    # 兼容前面可能多出的字段：从右往左定位更稳，但这里按标准位置取
    code, number, amount, date = parts[2], parts[3], parts[4], parts[5]
    if re.fullmatch(r"\d{8,12}", code):
        out["invoice_code"] = code
    if re.fullmatch(r"\d{6,10}", number):
        out["invoice_number"] = number
    if re.fullmatch(r"\d+(\.\d+)?", amount or ""):
        out["amount"] = f"{float(amount):.2f}"
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", date or "")
    if m:
        out["issue_date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return out


def decode(path: Path) -> tuple[str | None, str]:
    """返回 (payload, 说明)。先用 pyzbar（zbar 对小而密的发票二维码明显更强），
    不行再回退到 cv2.QRCodeDetector。两者都逐级放大重试。"""
    img = cv2.imread(str(path))
    if img is None:
        return None, "图片读取失败"
    h, w = img.shape[:2]

    for scale in (0.35, 0.5, 0.75, 1.0):
        small = cv2.resize(img, (int(w * scale), int(h * scale)),
                           interpolation=cv2.INTER_AREA) if scale < 1.0 else img
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if pyzbar is not None:
            for variant, im in (("", small), ("gray", gray)):
                try:
                    found = pyzbar.decode(im)
                except Exception:  # noqa: BLE001
                    found = []
                for d in found:
                    if d.type != "QRCODE":
                        continue
                    try:
                        return d.data.decode("utf-8"), f"zbar scale={scale} {variant}".strip()
                    except UnicodeDecodeError:
                        return d.data.decode("gbk", "replace"), f"zbar scale={scale} gbk"

        det = cv2.QRCodeDetector()
        for variant, im in (("", small), ("gray", gray)):
            try:
                text, _pts, _ = det.detectAndDecode(im)
            except cv2.error:
                continue
            if text:
                return text, f"cv2 scale={scale} {variant}".strip()

    return None, "未检出"


ap = argparse.ArgumentParser()
ap.add_argument("--image-dir", default="/tmp/ocr_imgs")
ap.add_argument("--out", default="/tmp/qr_result.json")
args = ap.parse_args()

files = sorted(Path(args.image_dir).glob("b*.jpg"),
               key=lambda p: int(p.stem.lstrip("b")) if p.stem.lstrip("b").isdigit() else 9999)
results = {}
ok = 0
for p in files:
    if not p.stem.lstrip("b").isdigit():
        continue
    payload, how = decode(p)
    if payload:
        ok += 1
        rec = parse_payload(payload)
        rec["_how"] = how
    else:
        rec = {"_how": how}
    results[p.stem + ".jpg"] = rec
    got = [k for k in ("invoice_code", "invoice_number", "amount", "issue_date") if k in rec]
    print(f"  {p.stem:<7}{'OK ' if payload else '失败'}  {how:<16}{'/'.join(got)}")

Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
n = len(results)
print(f"\n解码成功 {ok}/{n} = {ok/n:.1%}")
full = sum(1 for r in results.values()
           if all(k in r for k in ("invoice_code", "invoice_number", "amount", "issue_date")))
print(f"四字段齐全 {full}/{n} = {full/n:.1%}")
print(f"已写入 {args.out}")
