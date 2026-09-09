#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检验闭集选择的 100% 是「真选对」还是「没得可错」。

方法：看模型自报读到的购买方名称/税号是否真的等于所选主体的规范值。
若它读到的是别的东西（甚至是销售方的内容）却仍选中 E01，
那就是被迫落到最近选项上，而不是判断正确。

    python eval/check_entity_default.py --entities 2026-09-08-no-E05
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
LOGS = EVAL.parent / "logs" / "llm"


def read_raw(fname: str, version: str, entities: str) -> dict:
    for p in sorted(LOGS.rglob(f"*_{fname}_*.log"), reverse=True):
        t = p.read_text(encoding="utf-8", errors="replace")
        if f"Prompt     : {version}" not in t:
            continue
        if entities and entities not in t:
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


def key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


ap = argparse.ArgumentParser()
ap.add_argument("--version", default="v3.1")
ap.add_argument("--entities", default="2026-09-08-no-E05")
args = ap.parse_args()

ents = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                  .read_text(encoding="utf-8"))["entities"]
ENT = {e["id"]: e for e in ents}

files = [f for f in json.loads((EVAL / "file_ids.json").read_text(encoding="utf-8"))
         if f != "b195.jpg"]
files.sort(key=key)

clean, defaulted, missing = [], [], []
for f in files:
    o = read_raw(f, args.version, args.entities)
    if not o:
        missing.append(f)
        continue
    pid = (o.get("buyer_entity_id") or "").strip().upper()
    e = ENT.get(pid)
    on = o.get("buyer_observed_name") or ""
    ot = o.get("buyer_observed_tax_id") or ""
    if e and on == e["name"] and ot == e["tax_id"]:
        clean.append(f)
    else:
        defaulted.append((f, pid, on, ot, o.get("seller_name")))

n = len(clean) + len(defaulted)
print(f"样本 {n} 张（另有 {len(missing)} 张未找到匹配日志）\n")
print(f"  观测值与所选主体完全一致（可信）      {len(clean):>2}/{n} = {len(clean)/n:.1%}")
print(f"  观测值对不上却仍选中该主体（存疑）    {len(defaulted):>2}/{n} = {len(defaulted)/n:.1%}")

if defaulted:
    print("\n" + "=" * 74)
    print("存疑样本明细")
    print("=" * 74)
    for f, pid, on, ot, sn in defaulted:
        print(f"\n{f:<10} 选中 {pid}")
        print(f"{'':<10} 读到名称 : {on}")
        print(f"{'':<10} 读到税号 : {ot}")
        print(f"{'':<10} 销售方填 : {sn}")
