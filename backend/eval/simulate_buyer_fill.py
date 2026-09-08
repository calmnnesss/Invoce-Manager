#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线仿真：如果购买方改由系统填充，效果会怎样？

不调用任何 API，直接拿 results/<label>.json 里已有的 raw 值重放。

三种方案对比：
  S0  现状          —— 购买方、销售方都由 LLM 识别
  S1  系统填充购买方 —— 购买方不再由 LLM 提供，销售方仍取 LLM 的销售方栏
  S2  系统填充 + 对调修正
      —— 若 LLM 的"销售方栏"内容其实是本公司，判定为整体对调，
         改用 LLM 的"购买方栏"内容作为销售方

评分口径：购买方字段不再计入（系统填充恒正确），
只评 seller_name / seller_tax_id + 6 个非主体字段，共 8 个字段。
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

R = Path(__file__).resolve().parent / "results"

OUR_NAME = "深圳市购机汇网络有限公司"      # 本公司（购买方），系统已知
OUR_TAX = "440300083885931"
GT_SELLER_NAME = "广州晶东贸易有限公司"
GT_SELLER_TAX = "91440101664041243T"

OTHER = ["invoice_code", "issue_date", "amount", "tax_amount", "total_with_tax", "tax_rate"]


def sim(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def looks_like_us(name, tax) -> bool:
    """这组值指向的是不是本公司？"""
    return sim(name, OUR_NAME) >= 0.75 or (tax and sim(tax, OUR_TAX) >= 0.85)


def run(label="v2_prompt"):
    rows = json.loads((R / f"{label}.json").read_text(encoding="utf-8"))["per_sample"]
    n = len(rows)
    res = {k: {"sn": 0, "st": 0, "exact": 0, "swap_fired": 0}
           for k in ("S0", "S1", "S2")}

    detail = []
    for r in rows:
        F = r["fields"]
        bn, bt = F["buyer_name"]["raw"], F["buyer_tax_id"]["raw"]
        sn, st = F["seller_name"]["raw"], F["seller_tax_id"]["raw"]
        others_ok = all(F[f]["raw_ok"] for f in OTHER)

        # S0 现状
        res["S0"]["sn"] += sn == GT_SELLER_NAME
        res["S0"]["st"] += st == GT_SELLER_TAX
        res["S0"]["exact"] += r["exact_match"]

        # S1 系统填充购买方，销售方照抄 LLM 销售方栏
        s1n, s1t = sn, st
        res["S1"]["sn"] += s1n == GT_SELLER_NAME
        res["S1"]["st"] += s1t == GT_SELLER_TAX
        res["S1"]["exact"] += (s1n == GT_SELLER_NAME and s1t == GT_SELLER_TAX and others_ok)

        # S2 系统填充 + 对调修正
        swapped = looks_like_us(sn, st) and not looks_like_us(bn, bt)
        if swapped:
            res["S2"]["swap_fired"] += 1
            s2n, s2t = bn, bt
        else:
            s2n, s2t = sn, st
        ok_n, ok_t = s2n == GT_SELLER_NAME, s2t == GT_SELLER_TAX
        res["S2"]["sn"] += ok_n
        res["S2"]["st"] += ok_t
        res["S2"]["exact"] += (ok_n and ok_t and others_ok)

        detail.append((r["file"], swapped, ok_n and ok_t and others_ok,
                       s2n, s2t))

    print(f"=== 离线仿真（{label}，n={n}）===\n")
    print(f"{'方案':<26}{'销方名称':>10}{'销方税号':>10}{'整单全对':>10}")
    names = {
        "S0": "S0 现状",
        "S1": "S1 系统填充购买方",
        "S2": "S2 系统填充+对调修正",
    }
    for k in ("S0", "S1", "S2"):
        d = res[k]
        print(f"{names[k]:<24}{d['sn']:>6}/{n}{d['st']:>7}/{n}{d['exact']:>7}/{n}"
              f"   = {d['exact'] / n:5.1%}")
    print(f"\n对调修正规则触发次数: {res['S2']['swap_fired']}")

    print("\n=== S2 之后仍未修好的单据 ===")
    for f, sw, ok, s2n, s2t in sorted(detail, key=lambda x: int(x[0].lstrip('b').split('.')[0])):
        if ok:
            continue
        tag = " [已判为对调]" if sw else ""
        prob = []
        if s2n != GT_SELLER_NAME:
            prob.append(f"销方名称={s2n}")
        if s2t != GT_SELLER_TAX:
            prob.append(f"销方税号={s2t}")
        if not prob:
            prob.append("数值/日期字段有误")
        print(f"  {f:<9}{tag}  " + "；".join(prob))


if __name__ == "__main__":
    run("v2_prompt")
    print()
    run("baseline")
