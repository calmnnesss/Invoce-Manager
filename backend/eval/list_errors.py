#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出某次评测里所有未全对的样本，以及每张图各自错在哪。

只看 raw（模型原始输出），不涉及清洗后的值。

    python eval/list_errors.py                      # 默认读 results/v2_prompt.json
    python eval/list_errors.py --result baseline
    python eval/list_errors.py --result v2_prompt --group direction
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"

PARTY_FIELDS = ["buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id"]

KIND_LABEL = {
    "swapped": "与对方栏完全对调",
    "entity_polluted": "被填成主体清单里的公司",
    "swapped_near_miss": "对调+字形错",
    "near_miss": "字形错",
    "unrelated": "幻觉/串栏",
    "missing": "空值",
}

# 闭集模式下，购买方由 buyer_entity_id 独立决定，不受销售方栏影响。
# 此时销售方栏出现本公司，只能说明销售方那一栏错了，
# 不能叫“对调”—— 真对调要求购买方一侧也拿到了销售方的信息。
_ENTITIES_PATH = Path(__file__).resolve().parent.parent / "app" / "services" / "buyer_entities.json"
try:
    _ents = json.loads(_ENTITIES_PATH.read_text(encoding="utf-8"))["entities"]
    ENTITY_NAMES = {e["name"] for e in _ents}
    ENTITY_TAXES = {e["tax_id"] for e in _ents}
except OSError:
    ENTITY_NAMES, ENTITY_TAXES = set(), set()

FIELD_CN = {
    "invoice_code": "发票代码",
    "issue_date": "开票日期",
    "buyer_entity_id": "购方主体ID",
    "buyer_name": "购买方名称",
    "buyer_tax_id": "购买方税号",
    "seller_name": "销售方名称",
    "seller_tax_id": "销售方税号",
    "amount": "金额",
    "tax_amount": "税额",
    "total_with_tax": "价税合计",
    "tax_rate": "税率",
}


def char_diff(gt: str | None, raw: str | None) -> str:
    """标出两个等长字符串的差异位置；长度不同就只报长度差。"""
    if gt is None or raw is None:
        return ""
    if len(gt) != len(raw):
        return f"（长度 {len(gt)} → {len(raw)}）"
    diffs = [(i, a, b) for i, (a, b) in enumerate(zip(gt, raw)) if a != b]
    if not diffs:
        return ""
    parts = [f"第{i + 1}位 {a}→{b}" for i, a, b in diffs]
    return "（" + "，".join(parts) + "）"


def sort_key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="v2_prompt", help="results/<name>.json")
    ap.add_argument("--group", default="type", choices=["type", "file"],
                    help="type=按错误性质分组（默认），file=纯按文件名排序")
    args = ap.parse_args()

    path = RESULTS_DIR / f"{args.result}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["per_sample"]
    bad = [r for r in rows if not r["exact_match"]]

    print(f"评测结果 : {path.name}")
    print(f"样本总数 : {len(rows)}    全对 {len(rows) - len(bad)}    有问题 {len(bad)}")
    print()

    # 给每张有问题的图归一个"主要问题"
    # 闭集模式下 buyer_name/buyer_tax_id 不参与评分，只看实际存在的字段
    def kinds_of(r):
        return [r["fields"][f].get("kind") for f in PARTY_FIELDS if f in r["fields"]]

    def closed_set(r):
        return "buyer_entity_id" in r["fields"]

    def buyer_ok(r):
        d = r["fields"].get("buyer_entity_id")
        return d["raw_ok"] if d else None

    def seller_is_entity(r):
        """销售方栏被填成了主体清单里的公司？"""
        sn = (r["fields"].get("seller_name") or {}).get("raw")
        st = (r["fields"].get("seller_tax_id") or {}).get("raw")
        return sn in ENTITY_NAMES or st in ENTITY_TAXES

    def main_issue(r):
        k = kinds_of(r)
        if closed_set(r):
            # 购买方独立判定：先看 ID 对不对，再看销售方
            if not buyer_ok(r):
                return "F. 主体选错"
            if seller_is_entity(r):
                return "G. 清单污染（销售方栏被填成清单主体）"
            if "unrelated" in k:
                return "C. 销售方幻觉/串栏"
            if "near_miss" in k or "swapped" in k or "swapped_near_miss" in k:
                return "D. 销售方字形错误"
            return "E. 仅数值/日期字段出错"
        # 非闭集（v1/v2）：买卖双方都由模型自由抽取，对调判定成立
        if not r["direction_ok"]:
            if "unrelated" in set(k):
                return "A. 方向错 + 有幻觉串栏"
            return "B. 买卖方对调"
        if "unrelated" in k:
            return "C. 主体字段幻觉/串栏"
        if "near_miss" in k:
            return "D. 主体字段字形错误"
        return "E. 仅数值/日期字段出错"

    buckets = defaultdict(list)
    for r in bad:
        buckets[main_issue(r)].append(r)

    order = args.group == "type"
    groups = sorted(buckets.items()) if order else [("全部", sorted(bad, key=lambda r: sort_key(r["file"])))]

    for gname, items in groups:
        if order:
            print("=" * 76)
            print(f"{gname}    共 {len(items)} 张")
            print("=" * 76)
        for r in sorted(items, key=lambda r: sort_key(r["file"])):
            orient = {"landscape": "横", "portrait": "竖"}.get(r["orientation"], "?")
            flag = "" if r["direction_ok"] else "  [方向错]"
            print(f"\n{r['file']}   ({orient}图){flag}")
            for f, d in r["fields"].items():
                if d["raw_ok"]:
                    continue
                kind = d.get("kind")
                # 闭集下“swapped”只意味着值等于对方栏的 GT，不代表发生了对调
                if kind in ("swapped", "swapped_near_miss") and "buyer_entity_id" in r["fields"]:
                    kind = "entity_polluted"
                tag = f"  [{KIND_LABEL[kind]}]" if kind and kind in KIND_LABEL else ""
                diff = char_diff(d["gt"], d["raw"]) if kind in ("near_miss", None) else ""
                print(f"    {FIELD_CN.get(f, f):<10} 应为 {d['gt']}")
                print(f"    {'':<10} 实为 {d['raw']}{tag}{diff}")
        print()

    print("=" * 76)
    print("问题分布")
    print("=" * 76)
    for gname, items in sorted(buckets.items()):
        print(f"  {gname:<24}{len(items):>3} 张")
    print()
    c = Counter()
    for r in bad:
        for f, d in r["fields"].items():
            if not d["raw_ok"]:
                c[FIELD_CN.get(f, f)] += 1
    print("出错次数最多的字段：")
    for f, n in c.most_common():
        print(f"  {f:<12}{n:>3} 次")


if __name__ == "__main__":
    main()
