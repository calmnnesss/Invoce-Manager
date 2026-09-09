#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看 v2 的 party_evidence 字段实际写了什么，以及它和销售方正确率的关系。"""
import json
import re
from collections import Counter
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ARCHIVE = EVAL.parent / "logs" / "llm" / "archive" / "2026-09-07_v1-v2"
GT_SELLER = "广州晶东贸易有限公司"

LOG_RE = re.compile(r"^[\d\-]+_invoice-\d+_(?P<fname>.+)_[^_]+_[^_.]+\.log$")


def raw_json(text):
    i = text.find("[1] 原始响应")
    j = text.find("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[j:k + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


latest = {}
for p in sorted(ARCHIVE.glob("*.log")):
    m = LOG_RE.match(p.name)
    if not m:
        continue
    t = p.read_text(encoding="utf-8", errors="replace")
    if "Prompt     : v2" not in t:
        continue
    latest[m.group("fname")] = raw_json(t)

print(f"v2 样本 {len(latest)} 条\n")

# 模型提到了哪些锚点
mark = {"印章": ("印章", "专用章"), "栏位标签": ("标签", "销售方栏", "栏位"),
        "收款人行": ("收款人", "开票人", "复核"), "位置": ("下方", "上方", "底部", "左下")}
tally = Counter()
ok_by_mark = Counter()
tot_by_mark = Counter()

for f, o in latest.items():
    ev = o.get("party_evidence") or ""
    ok = o.get("seller_name") == GT_SELLER
    hit = False
    for name, kws in mark.items():
        if any(k in ev for k in kws):
            tally[name] += 1
            tot_by_mark[name] += 1
            ok_by_mark[name] += ok
            hit = True
    if not hit:
        tally["未提及锚点"] += 1

print("模型在 party_evidence 里提到的判据（可多选）：")
for k, v in tally.most_common():
    if k in tot_by_mark:
        print(f"  {k:<10}{v:>3} 次   其中销售方填对 {ok_by_mark[k]:>2} = {ok_by_mark[k]/tot_by_mark[k]:5.1%}")
    else:
        print(f"  {k:<10}{v:>3} 次")

print("\n—— 几条实际输出 ——")
shown = 0
for f in sorted(latest, key=lambda x: int(x.lstrip("b").split(".")[0])):
    o = latest[f]
    ev = o.get("party_evidence")
    if not ev:
        continue
    ok = "对" if o.get("seller_name") == GT_SELLER else "错"
    print(f"\n{f}  [销售方{ok}]  seller_name={o.get('seller_name')}")
    print(f"   {ev}")
    shown += 1
    if shown >= 6:
        break
