#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 ocr_probe_out 里某一档的结果：ok / null / wrong 三态统计。

null 和 wrong 必须分开：前者是模型不作答，后者是模型答错，
产品含义完全不同（缺失可检测，错值可能静默流入）。

    python eval/ocr_summary.py --n 8192 --rot 1
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

EVAL = Path(__file__).resolve().parent

FIELD_MAP = {
    "发票代码": "invoice_code",
    "发票号码": "invoice_number",
    "开票日期": "issue_date",
    "购买方名称": "buyer_name",
    "购买方纳税人识别号": "buyer_tax_id",
    "销售方名称": "seller_name",
    "销售方纳税人识别号": "seller_tax_id",
    "金额": "amount",
    "税额": "tax_amount",
    "价税合计": "total_with_tax",
    "税率": "tax_rate",
}
NUMERIC = {"amount", "tax_amount", "total_with_tax"}


def canon(field, value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None
    if field in NUMERIC:
        m = re.search(r"\d+(?:\.\d+)?", s.replace(",", "").replace("，", ""))
        return f"{float(m.group()):.2f}" if m else s
    if field == "issue_date":
        m = re.search(r"(\d{4})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})", s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return s
    if field.endswith("tax_id") or field in ("invoice_code", "invoice_number"):
        return re.sub(r"[\s\-]", "", s.upper())
    if field == "tax_rate":
        return s.replace(" ", "")
    return re.sub(r"\s+", "", s)


ap = argparse.ArgumentParser()
ap.add_argument("--n", default="8192")
ap.add_argument("--rot", default="1")
ap.add_argument("--dir", default=str(EVAL / "ocr_probe_out"))
ap.add_argument("--gt", default=str(EVAL / "ground_truth.json"))
args = ap.parse_args()

GT = json.loads(Path(args.gt).read_text(encoding="utf-8"))["constant_fields"]
rows = [json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(Path(args.dir).glob(f"*_px{args.n}_rot{args.rot}_*.json"))]
if not rows:
    raise SystemExit(f"没有匹配文件: *_px{args.n}_rot{args.rot}_*.json")

scored = [en for en in FIELD_MAP.values() if en != "invoice_number" and en in GT]


def judge(kv, cn, en):
    v = canon(en, (kv or {}).get(cn))
    if v is None:
        return "null"
    return "ok" if v == canon(en, GT[en]) else "wrong"


n = len(rows)
print(f"样本 {n} 张   N={args.n}  rotate={'on' if args.rot == '1' else 'off'}\n")
print(f"{'字段':<20}{'ok':>6}{'null':>7}{'wrong':>7}")
print("-" * 42)
tot = {"ok": 0, "null": 0, "wrong": 0}
for cn, en in FIELD_MAP.items():
    if en not in scored:
        continue
    c = {"ok": 0, "null": 0, "wrong": 0}
    for j in rows:
        c[judge(j.get("kv_result"), cn, en)] += 1
        tot[judge(j.get("kv_result"), cn, en)] += 1
    print(f"{en:<20}{c['ok']:>6}{c['null']:>7}{c['wrong']:>7}")
print("-" * 42)
cells = n * len(scored)
print(f"{'合计':<20}{tot['ok']:>6}{tot['null']:>7}{tot['wrong']:>7}   "
      f"(共 {cells} 格，正确率 {tot['ok']/cells:.1%})")

full = sum(1 for j in rows
           if all(judge(j.get("kv_result"), cn, en) == "ok"
                  for cn, en in FIELD_MAP.items() if en in scored))
print(f"\n整单全对 {full}/{n} = {full/n:.1%}")

print("\n错值明细（wrong，不含 null）:")
for cn, en in FIELD_MAP.items():
    if en not in scored:
        continue
    bad = [(j["file"], (j.get("kv_result") or {}).get(cn))
           for j in rows if judge(j.get("kv_result"), cn, en) == "wrong"]
    if not bad:
        continue
    print(f"\n  {en}  (GT: {GT[en]})")
    for f, v in sorted(bad, key=lambda x: int(x[0].lstrip("b"))):
        print(f"    {f:<7}{v}")
