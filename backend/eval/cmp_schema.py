#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比不同 schema 下 OCR 对销售方两字段的表现（rotate off）。"""
import json
import re
from pathlib import Path

EVAL = Path(__file__).resolve().parent
GT = json.loads((EVAL / "ground_truth.json").read_text(encoding="utf-8"))["constant_fields"]
M = {"销售方名称": "seller_name", "销售方纳税人识别号": "seller_tax_id"}
ECHO = {"销售方名称", "销售方纳税人识别号", "名称", "纳税人识别号"}


def canon(f, v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "null":
        return None
    if s in ECHO:
        return None
    if f.endswith("tax_id"):
        return re.sub(r"[\s\-]", "", s.upper())
    return re.sub(r"\s+", "", s)


SETS = [
    ("11 字段 schema", EVAL / "ocr_probe_out_rotoff_n8192_11f"),
    ("2 字段 schema", EVAL / "ocr_probe_out"),
]

for label, d in SETS:
    files = sorted(d.glob("*_px8192_rot0_*.json")) if d.exists() else []
    if not files:
        print(f"\n{label}: 无数据 ({d})")
        continue
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    print(f"\n{'=' * 60}\n{label}   rotate=off   {len(rows)} 张\n{'=' * 60}")
    for cn, en in M.items():
        c = {"ok": 0, "null": 0, "wrong": 0}
        bad = []
        for j in rows:
            v = canon(en, (j.get("kv_result") or {}).get(cn))
            k = "null" if v is None else ("ok" if v == canon(en, GT[en]) else "wrong")
            c[k] += 1
            if k != "ok":
                bad.append((j["file"], k, (j.get("kv_result") or {}).get(cn)))
        print(f"  {en:<16} ok {c['ok']:>3}   null {c['null']:>3}   wrong {c['wrong']:>3}")
        for f, k, v in sorted(bad, key=lambda x: int(x[0].lstrip("b"))):
            print(f"      {f:<7}{k:<7}{v}")
    both = sum(1 for j in rows
               if all(canon(en, (j.get("kv_result") or {}).get(cn)) == canon(en, GT[en])
                      for cn, en in M.items()))
    tok = sum(j["usage"].get("total_tokens", 0) for j in rows)
    el = sum(j.get("elapsed_s", 0) for j in rows) / len(rows)
    print(f"  两字段都对 {both}/{len(rows)}")
    print(f"  累计 token {tok:,}   平均耗时 {el:.1f}s")
