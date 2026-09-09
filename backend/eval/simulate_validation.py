#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在历史日志上离线回放 Postprocess v4 校验规则，零 API 调用。

用来在接进生产代码之前，先确认三条规则各自命中了哪些样本、
有没有误伤完全正确的记录。

    python eval/simulate_validation.py --prompt-version v3.1 --entities 2026-09-08-no-E05
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent))

from score_logs import DEFAULT_LOG_DIR, canon, parse_log  # noqa: E402
from app.services.validation import validate  # noqa: E402


def key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


ap = argparse.ArgumentParser()
ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
ap.add_argument("--prompt-version", default="v3.1")
ap.add_argument("--entities", default=None)
ap.add_argument("--gt", default=str(EVAL / "ground_truth.json"))
args = ap.parse_args()

gt_cfg = json.loads(Path(args.gt).read_text(encoding="utf-8"))
GT = gt_cfg["constant_fields"]
exclude = set(gt_cfg.get("exclude_files", []))
entities = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                      .read_text(encoding="utf-8"))["entities"]

latest = {}
for p in sorted(Path(args.log_dir).glob("*.log")):
    s = parse_log(p)
    if not s or not s["file"] or s["file"] in exclude or s["status"] != "OK":
        continue
    if args.prompt_version and s["prompt_version"] != args.prompt_version:
        continue
    if args.entities and args.entities not in (s["entities_version"] or ""):
        continue
    cur = latest.get(s["file"])
    if cur is None or s["ts"] > cur["ts"]:
        latest[s["file"]] = s

samples = sorted(latest.values(), key=lambda s: key(s["file"]))
if not samples:
    sys.exit("没有匹配到样本")

# 判断这条记录「实际是否有错」：用受评字段对照 GT
scored = gt_cfg.get("scored_fields_closedset") or gt_cfg["scored_fields"]


def has_error(raw):
    for f in scored:
        if f not in GT:
            continue
        if canon(f, raw.get(f)) != canon(f, GT[f]):
            return True
    return False


rows = []
for s in samples:
    raw = s["raw"]
    res = validate(raw, entities)
    rows.append({
        "file": s["file"],
        "verdict": res.verdict,
        "failed": res.failed,
        "skipped": res.skipped,
        "has_error": has_error(raw),
        "checks": res.checks,
        "nulled": res.nulled,
    })

n = len(rows)
print(f"样本 {n} 张   prompt={args.prompt_version}   entities={args.entities or '(不限)'}\n")

print("=" * 74)
print("各规则命中情况")
print("=" * 74)
for rid in ("seller_is_entity", "amount_sum", "tax_rate_consistency"):
    c = Counter()
    hits = []
    for r in rows:
        st = next(x.status for x in r["checks"] if x.rule_id == rid)
        c[st] += 1
        if st == "fail":
            hits.append(r["file"])
    print(f"\n  {rid}")
    print(f"    pass {c['pass']:>3}   fail {c['fail']:>3}   skipped {c['skipped']:>3}")
    if hits:
        print("    命中: " + " ".join(x.replace(".jpg", "") for x in sorted(hits, key=key)))

print()
print("=" * 74)
print("判定分布")
print("=" * 74)
vc = Counter(r["verdict"] for r in rows)
for v in ("ok", "warn", "rejected"):
    print(f"  {v:<10}{vc[v]:>3}/{n} = {vc[v]/n:5.1%}")

print()
print("=" * 74)
print("核心指标：错误可见性")
print("=" * 74)
err = [r for r in rows if r["has_error"]]
clean = [r for r in rows if not r["has_error"]]
caught = [r for r in err if r["verdict"] != "ok"]
silent = [r for r in err if r["verdict"] == "ok"]
false_alarm = [r for r in clean if r["verdict"] != "ok"]

print(f"  实际有错          {len(err):>3}/{n} = {len(err)/n:5.1%}")
print(f"  其中被规则拦下     {len(caught):>3}/{len(err) or 1} = {len(caught)/(len(err) or 1):5.1%}   <- 拦截率")
print(f"  其中静默通过       {len(silent):>3}/{n} = {len(silent)/n:5.1%}   <- 静默错误率（核心）")
print(f"  完全正确却报警     {len(false_alarm):>3}/{len(clean) or 1} = {len(false_alarm)/(len(clean) or 1):5.1%}   <- 误伤率")

print()
print("  静默通过的错误样本:")
print("    " + (" ".join(x["file"].replace(".jpg", "") for x in sorted(silent, key=lambda r: key(r["file"]))) or "(无)"))
print("  被误伤的正确样本:")
print("    " + (" ".join(x["file"].replace(".jpg", "") for x in sorted(false_alarm, key=lambda r: key(r["file"]))) or "(无)"))

print()
print("=" * 74)
print("触发规则的样本明细")
print("=" * 74)
for r in sorted([x for x in rows if x["verdict"] != "ok"], key=lambda x: key(x["file"])):
    print(f"\n{r['file']:<10} {r['verdict'].upper()}"
          + ("   [实际有错]" if r["has_error"] else "   [实际正确 -> 误伤]"))
    for c in r["checks"]:
        if c.status == "fail":
            print(f"    {c.rule_id}: {c.detail}")
    if r["nulled"]:
        print(f"    已置空: {', '.join(r['nulled'])}")
