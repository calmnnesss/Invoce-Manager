#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 与 v3.1 的「买卖方混淆」对照。

两版输出结构不同，用同一套判定统一口径：
  v2   购买方一侧 = buyer_name / buyer_tax_id（模型自由抽取的结果）
  v3.1 购买方一侧 = buyer_observed_name / buyer_observed_tax_id（模型自报读到的原文）
  两版销售方一侧都是 seller_name / seller_tax_id

对每一侧判断它指向的是「本公司（购买方）」还是「供应商（销售方）」，
再交叉出四种状态。
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

EVAL = Path(__file__).resolve().parent
LOGS = EVAL.parent / "logs" / "llm"

BUYER_NAME, BUYER_TAX = "深圳市购机汇网络有限公司", "440300083885931"
SELLER_NAME, SELLER_TAX = "广州晶东贸易有限公司", "91440101664041243T"


def sim(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, str(a), str(b)).ratio()


def side_of(name, tax):
    """这组值指向本公司还是供应商？两边都不像则返回 None。"""
    b = max(sim(name, BUYER_NAME), sim(tax, BUYER_TAX))
    s = max(sim(name, SELLER_NAME), sim(tax, SELLER_TAX))
    if max(b, s) < 0.65:
        return None
    return "buyer" if b > s else "seller"


def read_raw(fname, version):
    for p in sorted(LOGS.rglob(f"*_{fname}_*.log"), reverse=True):
        t = p.read_text(encoding="utf-8", errors="replace")
        if f"Prompt     : {version}" not in t:
            continue
        i = t.find("[1] 原始响应")
        j = t.find("{", i)
        depth = 0
        for k in range(j, len(t)):
            if t[k] == "{":
                depth += 1
            elif t[k] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[j:k + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


def key(f):
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


files = sorted(
    json.loads((EVAL / "file_ids.json").read_text(encoding="utf-8")),
    key=key,
)
files = [f for f in files if f != "b195.jpg"]

STATES = {
    "ok": "两侧都正确",
    "full_swap": "完全对调（购买方栏是供应商，销售方栏是本公司）",
    "seller_polluted": "仅销售方栏被填成本公司",
    "buyer_polluted": "仅购买方栏被填成供应商",
    "unknown": "至少一侧无法判定（串栏/幻觉）",
}

result = {}
for version in ("v2", "v3.1"):
    tally = {k: [] for k in STATES}
    for f in files:
        raw = read_raw(f, version)
        if not raw:
            continue
        if version == "v2":
            bn, bt = raw.get("buyer_name"), raw.get("buyer_tax_id")
        else:
            bn, bt = raw.get("buyer_observed_name"), raw.get("buyer_observed_tax_id")
        sn, st = raw.get("seller_name"), raw.get("seller_tax_id")
        bs, ss = side_of(bn, bt), side_of(sn, st)

        if bs is None or ss is None:
            state = "unknown"
        elif bs == "buyer" and ss == "seller":
            state = "ok"
        elif bs == "seller" and ss == "buyer":
            state = "full_swap"
        elif ss == "buyer":
            state = "seller_polluted"
        else:
            state = "buyer_polluted"
        tally[state].append(f)
    result[version] = tally

n = len(files)
print("=" * 74)
print(f"买卖方混淆状态对照   n={n}")
print("=" * 74)
print(f"  {'状态':<34}{'v2':>12}{'v3.1':>12}")
for k, label in STATES.items():
    a, b = len(result["v2"][k]), len(result["v3.1"][k])
    print(f"  {label:<32}{a:>4} ({a/n*100:4.1f}%){b:>5} ({b/n*100:4.1f}%)")

print()
print("=" * 74)
print("逐状态文件清单")
print("=" * 74)
for k, label in STATES.items():
    if k == "ok":
        continue
    for v in ("v2", "v3.1"):
        fs = result[v][k]
        if fs:
            print(f"\n  [{v}] {label}  {len(fs)} 张")
            print("      " + " ".join(x.replace(".jpg", "") for x in sorted(fs, key=key)))

print()
print("=" * 74)
print("迁移：v2 有问题 -> v3.1 的去向")
print("=" * 74)
v2_bad = {f for k, fs in result["v2"].items() if k != "ok" for f in fs}
v31_state = {f: k for k, fs in result["v3.1"].items() for f in fs}
fixed = sorted([f for f in v2_bad if v31_state.get(f) == "ok"], key=key)
still = sorted([f for f in v2_bad if v31_state.get(f) not in (None, "ok")], key=key)
new_bad = sorted([f for f, k in v31_state.items() if k != "ok" and f not in v2_bad], key=key)
print(f"  v2 有问题、v3.1 修好   {len(fixed):>2} 张: " + " ".join(x.replace('.jpg','') for x in fixed))
print(f"  v2 有问题、v3.1 仍有   {len(still):>2} 张: " + " ".join(x.replace('.jpg','') for x in still))
print(f"  v2 正常、v3.1 变坏     {len(new_bad):>2} 张: " + " ".join(x.replace('.jpg','') for x in new_bad))
