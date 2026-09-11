#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在历史数据上离线回放 v6 融合层，零 API 调用。

三路数据来源：
    QR   eval/qr_result.json          （当前 51/51 解码失败，走降级路径）
    OCR  eval/ocr_probe_out/          （11 字段 schema, rotate=off, N=8192）
    VLM  eval/results/v3_1_no_e05.json（prompt v3.1 + 四主体清单）

    python eval/simulate_fusion.py
    python eval/simulate_fusion.py --csv          # 同时导出逐字段 CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent))

from app.services.fusion import (  # noqa: E402
    CONF_CERTAIN, CONF_HIGH, CONF_LOW, CONF_MISSING, clean, fuse,
)

SCORED = ["invoice_code", "issue_date", "buyer_name", "buyer_tax_id",
          "seller_name", "seller_tax_id", "amount", "tax_amount",
          "total_with_tax", "tax_rate"]


def key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


ap = argparse.ArgumentParser()
ap.add_argument("--csv", action="store_true")
ap.add_argument("--report", default=str(EVAL / "reports" / "v6_fusion_report.txt"),
                help="把控制台报告同时写到该文件；传空字符串则不写")
ap.add_argument("--vlm", default=str(EVAL / "results" / "v3_1_no_e05.json"))
ap.add_argument("--ocr-dir", default=str(EVAL / "ocr_probe_out"))
ap.add_argument("--qr", default=str(EVAL / "qr_result.json"))
args = ap.parse_args()


class _Tee:
    """控制台和文件同时输出，避免报告内容和屏上不一致。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


_report_fh = None
if args.report:
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    _report_fh = open(args.report, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, _report_fh)

GT = json.loads((EVAL / "ground_truth.json").read_text(encoding="utf-8"))["constant_fields"]
entities = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                      .read_text(encoding="utf-8"))["entities"]

# VLM：results json 只存了受评字段，原始响应要回日志读
LOGS = EVAL.parent / "logs" / "llm"


def vlm_raw(fname: str) -> dict:
    """从日志里取该图最新一次 v3.1 + 四主体清单的原始响应。

    注意排序：日志分散在 logs/llm 和 logs/llm/archive/*/ 下，按路径字符串排序
    会让归档目录混进来、拿到旧结果。必须按文件名里的时间戳排。
    """
    cands = []
    for p in LOGS.rglob(f"*_{fname}_*.log"):
        t = p.read_text(encoding="utf-8", errors="replace")
        if "Prompt     : v3.1" not in t or "no-E05" not in t:
            continue
        cands.append((p.name.split("_")[0], t))
    if not cands:
        return {}
    _, t = max(cands, key=lambda x: x[0])
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


ocr_by_file = {}
for p in sorted(Path(args.ocr_dir).glob("*_px8192_rot0_*.json")):
    j = json.loads(p.read_text(encoding="utf-8"))
    ocr_by_file[j["file"] + ".jpg"] = j.get("kv_result") or {}

qr_by_file = {}
if Path(args.qr).exists():
    qr_by_file = json.loads(Path(args.qr).read_text(encoding="utf-8"))

files = sorted(ocr_by_file, key=key)
print(f"样本 {len(files)} 张")
qr_ok = sum(1 for f in files if qr_by_file.get(f, {}).get("invoice_code"))
print(f"二维码可用 {qr_ok}/{len(files)}"
      + ("   -> 全部走降级路径" if qr_ok == 0 else ""))
print()

rows, field_rows = [], []
for f in files:
    r = fuse(qr=qr_by_file.get(f), ocr=ocr_by_file.get(f),
             vlm=vlm_raw(f), entities=entities)
    wrong = []
    for k in SCORED:
        fr = r.fields.get(k)
        got = clean(k, fr.value if fr else None)
        if got != clean(k, GT[k]):
            wrong.append(k)
        field_rows.append({
            "file": f, "field": k, "gt": GT[k],
            "value": fr.value if fr else "", "source": fr.source if fr else "",
            "confidence": fr.confidence if fr else CONF_MISSING,
            "candidates": json.dumps(fr.candidates, ensure_ascii=False) if fr else "",
            "match": "Y" if got == clean(k, GT[k]) else "N",
        })
    rows.append({"file": f, "res": r, "wrong": wrong})

n = len(rows)
vc = Counter(x["res"].verdict for x in rows)
print("=" * 66)
print("判定分布")
print("=" * 66)
for v in ("ok", "warn", "rejected"):
    print(f"  {v:<10}{vc[v]:>3}/{n} = {vc[v]/n:6.1%}")

print()
print("=" * 66)
print("分字段：正确率 与 置信分布")
print("=" * 66)
print(f"  {'字段':<18}{'正确':>6}{'certain':>9}{'high':>7}{'low':>6}{'missing':>9}")
for k in SCORED:
    ok = sum(1 for x in rows if k not in x["wrong"])
    c = Counter(x["res"].fields[k].confidence for x in rows)
    print(f"  {k:<18}{ok:>4}/{n}{c[CONF_CERTAIN]:>9}{c[CONF_HIGH]:>7}"
          f"{c[CONF_LOW]:>6}{c[CONF_MISSING]:>9}")

full = sum(1 for x in rows if not x["wrong"])
print(f"\n  整单 10 字段全对  {full}/{n} = {full/n:.1%}")

print()
print("=" * 66)
print("核心指标：错误可见性")
print("=" * 66)
err = [x for x in rows if x["wrong"]]
silent = [x for x in err if x["res"].verdict == "ok"]
caught = [x for x in err if x["res"].verdict != "ok"]
clean_rows = [x for x in rows if not x["wrong"]]
fa = [x for x in clean_rows if x["res"].verdict != "ok"]
print(f"  实际有错      {len(err):>3}/{n} = {len(err)/n:6.1%}")
print(f"  被拦下        {len(caught):>3}/{len(err) or 1} = {len(caught)/(len(err) or 1):6.1%}  拦截率")
print(f"  静默通过      {len(silent):>3}/{n} = {len(silent)/n:6.1%}  静默错误率")
print(f"  正确却报警    {len(fa):>3}/{len(clean_rows) or 1} = {len(fa)/(len(clean_rows) or 1):6.1%}  误伤率")
print("\n  静默错误样本: " + (" ".join(x["file"].replace(".jpg", "") for x in silent) or "(无)"))

print()
print("=" * 66)
print("规则命中")
print("=" * 66)
rule_ids = [c.rule_id for c in rows[0]["res"].checks]
for rid in rule_ids:
    c = Counter(next(x.status for x in r["res"].checks if x.rule_id == rid) for r in rows)
    hits = [r["file"].replace(".jpg", "") for r in rows
            if next(x.status for x in r["res"].checks if x.rule_id == rid) == "fail"]
    print(f"  {rid:<26}pass {c['pass']:>3}  fail {c['fail']:>3}  skip {c['skipped']:>3}")
    if hits:
        print(f"    {' '.join(sorted(hits, key=key))}")

print()
print("=" * 66)
print("仍然出错的单据")
print("=" * 66)
for x in sorted(err, key=lambda r: key(r["file"])):
    print(f"\n  {x['file']:<9}{x['res'].verdict}")
    for k in x["wrong"]:
        fr = x["res"].fields[k]
        print(f"    {k:<18}GT {GT[k]}")
        print(f"    {'':<18}得 {fr.value}   来源={fr.source} 置信={fr.confidence}")

if args.csv:
    out = EVAL / "reports" / "v6_fusion_fields.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "field", "gt", "value", "source",
                                           "confidence", "candidates", "match"])
        w.writeheader()
        w.writerows(field_rows)
    out2 = EVAL / "reports" / "v6_fusion_summary.csv"
    with open(out2, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "verdict", "wrong_fields",
                                           "low_confidence", "failed_rules", "degraded"])
        w.writeheader()
        for x in rows:
            w.writerow({
                "file": x["file"], "verdict": x["res"].verdict,
                "wrong_fields": ",".join(x["wrong"]),
                "low_confidence": ",".join(x["res"].low_confidence),
                "failed_rules": ",".join(x["res"].failed_rules),
                "degraded": ",".join(x["res"].degraded),
            })
    print(f"\n已导出:\n  {out}\n  {out2}")

if _report_fh is not None:
    sys.stdout = sys.__stdout__
    _report_fh.close()
    print(f"报告已写入: {args.report}")
