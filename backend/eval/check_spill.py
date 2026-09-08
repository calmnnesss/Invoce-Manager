#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核查 A/C 类错误的具体形态：串栏时抓到的到底是哪一行。"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
PARTY = ["buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id"]

# 发票上购买方信息块的四行：名称 / 纳税人识别号 / 地址、电话 / 开户行及账号
ADDR_MARK = ("龙华", "民治", "街道", "大道", "科技大厦", "工业园", "路", "区")
BANK_MARK = ("银行", "支行", "工行", "开户")

rows = json.loads((R / "v2_prompt.json").read_text(encoding="utf-8"))["per_sample"]

print("所有 [幻觉/串栏] 字段（kind=unrelated）：\n")
tally = {"地址电话行": 0, "开户行账号行": 0, "其他": 0}
side = {"buyer": 0, "seller": 0}
for r in sorted(rows, key=lambda r: int(r["file"].lstrip("b").split(".")[0])):
    for f in PARTY:
        d = r["fields"][f]
        if d.get("kind") != "unrelated":
            continue
        v = d["raw"] or ""
        if any(m in v for m in BANK_MARK):
            cat = "开户行账号行"
        elif any(m in v for m in ADDR_MARK) or v.isdigit():
            cat = "地址电话行"
        else:
            cat = "其他"
        # 纯数字且很长的，判为开户行账号
        if v.isdigit() and len(v) > 15:
            cat = "开户行账号行"
        tally[cat] += 1
        side["buyer" if f.startswith("buyer") else "seller"] += 1
        print(f"  {r['file']:<9} {f:<14} [{cat}]")
        print(f"  {'':<9} {v}")

print(f"\n按串到的行分类: {tally}")
print(f"按栏位分类    : {side}")
