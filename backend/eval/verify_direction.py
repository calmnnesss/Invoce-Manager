#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核查被判为「买卖方对调」的样本，实际到底是什么形态。

闭集模式下购买方由 buyer_entity_id 决定，若 ID 正确，
那么销售方栏里出现本公司/清单主体，属于「清单污染」而不是「对调」——
真正的对调要求购买方一侧也拿到了销售方的信息。
"""
from __future__ import annotations

import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
LOGS = EVAL.parent / "logs" / "llm"

ENTS = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                  .read_text(encoding="utf-8"))["entities"]
BY_NAME = {e["name"]: e["id"] for e in ENTS}
BY_TAX = {e["tax_id"]: e["id"] for e in ENTS}
GT_SELLER, GT_SELLER_TAX = "广州晶东贸易有限公司", "91440101664041243T"


def read_raw(fname, version="v3.1", entities="2026-09-08-no-E05"):
    for p in sorted(LOGS.rglob(f"*_{fname}_*.log"), reverse=True):
        t = p.read_text(encoding="utf-8", errors="replace")
        if f"Prompt     : {version}" not in t or entities not in t:
            continue
        i = t.find("[1] 原始响应")
        j = t.find("{", i)
        d = 0
        for k in range(j, len(t)):
            if t[k] == "{":
                d += 1
            elif t[k] == "}":
                d -= 1
                if d == 0:
                    try:
                        return json.loads(t[j:k + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


rows = json.loads((EVAL / "results" / "v3_1_no_e05.json").read_text(encoding="utf-8"))["per_sample"]
suspects = [r for r in rows if not r["direction_ok"]]

print("被判为「方向错 / 对调」的样本，逐张核查\n" + "=" * 74)
for r in sorted(suspects, key=lambda x: int(x["file"].lstrip("b").split(".")[0])):
    f = r["file"]
    o = read_raw(f)
    bid = o.get("buyer_entity_id")
    on, ot = o.get("buyer_observed_name"), o.get("buyer_observed_tax_id")
    sn, st = o.get("seller_name"), o.get("seller_tax_id")

    buyer_right = (bid == "E01")
    seller_is_entity = BY_NAME.get(sn) or BY_TAX.get(st)
    buyer_got_seller = (on == GT_SELLER or ot == GT_SELLER_TAX)

    if buyer_got_seller and seller_is_entity:
        verdict = "真·对调（购买方栏拿到销售方信息，且销售方栏是清单主体）"
    elif seller_is_entity and buyer_right:
        verdict = f"清单污染（购买方正确，销售方栏被填成 {seller_is_entity}）"
    elif not seller_is_entity:
        verdict = "销售方识别错误（与清单无关）"
    else:
        verdict = "其他"

    print(f"\n{f}")
    print(f"   buyer_entity_id      : {bid}   {'正确' if buyer_right else '错误'}")
    print(f"   buyer_observed_name  : {on}")
    print(f"   buyer_observed_tax   : {ot}")
    print(f"   seller_name          : {sn}")
    print(f"   seller_tax_id        : {st}")
    print(f"   >> 实际形态           : {verdict}")
