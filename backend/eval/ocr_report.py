#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ocr_probe 的原始输出导成 CSV：file / field / gt / raw。

只做并排列出，不做归因、不做分类。

    python eval/ocr_report.py                      # 默认档 N=8192
    python eval/ocr_report.py --n 2048             # 指定档位
    python eval/ocr_report.py --n all              # 全部档位（多一列 max_pixels_n）
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
OUT = EVAL / "ocr_probe_out"

# ocr_probe.py 里的中文键 -> 现有评测口径的英文字段名
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


def key(f: str) -> int:
    try:
        return int(f.lstrip("b"))
    except ValueError:
        return 9999


ap = argparse.ArgumentParser()
ap.add_argument("--n", default="8192", help="max_pixels 档位，或 all")
ap.add_argument("--dir", default=str(OUT))
ap.add_argument("--gt", default=str(EVAL / "ground_truth.json"))
ap.add_argument("--out", default=None)
args = ap.parse_args()

GT = json.loads(Path(args.gt).read_text(encoding="utf-8"))["constant_fields"]

files = sorted(Path(args.dir).glob("*_px*_rot*_*.json"))
recs = []
for p in files:
    j = json.loads(p.read_text(encoding="utf-8"))
    if args.n != "all" and str(j.get("max_pixels_n")) != args.n:
        continue
    recs.append(j)

if not recs:
    raise SystemExit(f"没有匹配的结果文件（--n {args.n}）")

recs.sort(key=lambda j: (j.get("max_pixels_n", 0), key(j["file"])))

rows = []
for j in recs:
    kv = j.get("kv_result") or {}
    seen = set()
    # 先按 FIELD_MAP 的顺序列出，再列模型多给的键
    for cn, en in FIELD_MAP.items():
        seen.add(cn)
        row = {
            "file": j["file"],
            "field": en,
            "field_cn": cn,
            "gt": GT.get(en, ""),
            "raw": "" if kv.get(cn) is None else kv.get(cn),
        }
        if args.n == "all":
            row["max_pixels_n"] = j.get("max_pixels_n")
        rows.append(row)
    for cn, v in kv.items():
        if cn in seen:
            continue
        row = {
            "file": j["file"],
            "field": "(模型多给的键)",
            "field_cn": cn,
            "gt": "",
            "raw": "" if v is None else v,
        }
        if args.n == "all":
            row["max_pixels_n"] = j.get("max_pixels_n")
        rows.append(row)

fields = ["file", "field", "field_cn", "gt", "raw"]
if args.n == "all":
    fields.append("max_pixels_n")

out_path = Path(args.out) if args.out else EVAL / "reports" / f"ocr_s0_n{args.n}.csv"
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f"档位 N={args.n}   样本 {len(recs)} 条   字段行 {len(rows)}")
print(f"已写入 {out_path}\n")

# 控制台也打一份，方便直接看
cur = None
for r in rows:
    if r["file"] != cur:
        cur = r["file"]
        print(f"\n===== {cur} =====")
    print(f"  {r['field']:<18} GT  {r['gt']}")
    print(f"  {'':<18} raw {r['raw']}")
