#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seller_tax_id 上 VLM 与 OCR 的交叉列联表。

优先级规则只在「两路都有值且不一致」时才起作用。先量化这类样本有多少，
以及不一致时谁对，再决定优先级怎么定。
"""
import json
import re
from pathlib import Path

EVAL = Path(__file__).resolve().parent
GT = json.loads((EVAL / "ground_truth.json").read_text(encoding="utf-8"))["constant_fields"]
FIELD = "seller_tax_id"
CN = "销售方纳税人识别号"
ECHO = {"销售方纳税人识别号", "纳税人识别号"}


def canon(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "null" or s in ECHO:
        return None
    return re.sub(r"[\s\-]", "", s.upper())


TRUTH = canon(GT[FIELD])

# VLM：v3.1 四主体那次的评测结果
vlm_rows = json.loads((EVAL / "results" / "v3_1_no_e05.json").read_text(encoding="utf-8"))["per_sample"]
vlm = {r["file"]: canon(r["fields"].get(FIELD, {}).get("raw")) for r in vlm_rows}

# OCR：11 字段 schema、rotate off
ocr = {}
for p in sorted((EVAL / "ocr_probe_out").glob("*_px8192_rot0_*.json")):
    j = json.loads(p.read_text(encoding="utf-8"))
    ocr[j["file"] + ".jpg"] = canon((j.get("kv_result") or {}).get(CN))

files = sorted(set(vlm) & set(ocr), key=lambda f: int(f.lstrip("b").split(".")[0]))
print(f"共同样本 {len(files)} 张   GT = {TRUTH}\n")

buckets = {
    "两路一致且正确": [],
    "两路一致但都错": [],
    "不一致：VLM 对": [],
    "不一致：OCR 对": [],
    "不一致：都错": [],
    "OCR 空，VLM 对": [],
    "OCR 空，VLM 错": [],
    "VLM 空": [],
    "两路都空": [],
}

for f in files:
    a, b = vlm[f], ocr[f]
    if a is None and b is None:
        buckets["两路都空"].append((f, a, b))
    elif a is None:
        buckets["VLM 空"].append((f, a, b))
    elif b is None:
        k = "OCR 空，VLM 对" if a == TRUTH else "OCR 空，VLM 错"
        buckets[k].append((f, a, b))
    elif a == b:
        k = "两路一致且正确" if a == TRUTH else "两路一致但都错"
        buckets[k].append((f, a, b))
    else:
        if a == TRUTH:
            buckets["不一致：VLM 对"].append((f, a, b))
        elif b == TRUTH:
            buckets["不一致：OCR 对"].append((f, a, b))
        else:
            buckets["不一致：都错"].append((f, a, b))

n = len(files)
print(f"{'情况':<20}{'张数':>5}")
print("-" * 30)
for k, v in buckets.items():
    if v:
        print(f"{k:<20}{len(v):>5}")

print("\n明细（不含两路一致且正确）:")
for k, v in buckets.items():
    if not v or k == "两路一致且正确":
        continue
    print(f"\n  [{k}]")
    for f, a, b in v:
        print(f"    {f:<9} VLM={a}   OCR={b}")

# 两种优先级下的最终结果
print("\n" + "=" * 60)
print("两种优先级的最终正确率")
print("=" * 60)
for name, first in (("VLM 优先", "vlm"), ("OCR 优先", "ocr")):
    ok = flagged = silent_wrong = 0
    for f in files:
        a, b = vlm[f], ocr[f]
        val = (a if a is not None else b) if first == "vlm" else (b if b is not None else a)
        disagree = (a is not None and b is not None and a != b)
        single = (a is None) != (b is None)
        mark = disagree or single
        if val == TRUTH:
            ok += 1
        elif not mark:
            silent_wrong += 1
        if mark:
            flagged += 1
    print(f"  {name:<10} 正确 {ok}/{n}   标记复核 {flagged}   静默错误 {silent_wrong}")
