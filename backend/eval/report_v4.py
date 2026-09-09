#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Postprocess v4 完整报告。

在历史日志的 [1] 原始响应 上离线重放 v4 后处理，产出三份文件：

  reports/v4_fields.csv       逐字段：GT / 原始值 / 后处理值 / 两个匹配列
  reports/v4_validation.csv   逐样本：判定、三条规则各自状态、是否静默错误
  reports/v4_summary.txt      汇总指标

为什么离线重放而不是重跑模型：
  v4 改的是后处理层，prompt 与主体清单未变。模型是非确定性的，重跑会拿到
  一批不同的 raw，后处理的效果就和「模型这次发挥如何」混在一起，归因作废。
  正确做法是固定 raw、只换后处理。

    python eval/report_v4.py --prompt-version v3.1 --entities 2026-09-08-no-E05
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent))

from score_logs import DEFAULT_LOG_DIR, canon, parse_log  # noqa: E402
from app.services.validation import validate  # noqa: E402

RULES = ["seller_is_entity", "amount_sum", "tax_rate_consistency"]


def key(f: str) -> int:
    try:
        return int(f.lstrip("b").split(".")[0])
    except ValueError:
        return 9999


def apply_buyer_fill(raw: dict, by_id: dict) -> dict:
    """复刻 llm_service 里的购买方填充：按 buyer_entity_id 查表写入规范值。"""
    out = dict(raw)
    eid = raw.get("buyer_entity_id")
    if eid is not None:
        e = by_id.get(str(eid).strip().upper())
        if e:
            out["buyer_name"] = e["name"]
            out["buyer_tax_id"] = e["tax_id"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--prompt-version", default="v3.1")
    ap.add_argument("--entities", default=None)
    ap.add_argument("--gt", default=str(EVAL / "ground_truth.json"))
    ap.add_argument("--out-dir", default=str(EVAL / "reports"))
    ap.add_argument("--label", default="v4")
    args = ap.parse_args()

    gt_cfg = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    GT = gt_cfg["constant_fields"]
    exclude = set(gt_cfg.get("exclude_files", []))
    scored = gt_cfg.get("scored_fields_closedset") or gt_cfg["scored_fields"]

    ents = json.loads((EVAL.parent / "app" / "services" / "buyer_entities.json")
                      .read_text(encoding="utf-8"))["entities"]
    by_id = {e["id"]: e for e in ents}

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
        sys.exit("没有匹配到样本，检查 --log-dir / --prompt-version / --entities")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    field_rows, val_rows = [], []
    for s in samples:
        raw = s["raw"]
        filled = apply_buyer_fill(raw, by_id)
        res = validate(filled, ents)
        post = res.fields

        has_error = any(canon(f, raw.get(f)) != canon(f, GT[f])
                        for f in scored if f in GT)

        status = {c.rule_id: c.status for c in res.checks}
        detail = {c.rule_id: c.detail for c in res.checks}
        val_rows.append({
            "file": s["file"],
            "verdict": res.verdict,
            **{r: status.get(r, "") for r in RULES},
            "nulled": ",".join(res.nulled),
            "has_error": "Y" if has_error else "N",
            "silent_error": "Y" if (has_error and res.verdict == "ok") else "N",
            "false_alarm": "Y" if (not has_error and res.verdict != "ok") else "N",
            "detail": " | ".join(f"{k}: {v}" for k, v in detail.items() if v),
        })

        keys = [k for k in GT if k in raw or k in post] + \
               [k for k in raw if k not in GT]
        seen = set()
        for k in keys:
            if k in seen:
                continue
            seen.add(k)
            rv, pv, gv = raw.get(k), post.get(k), GT.get(k)

            def verdict(v):
                if gv is None:
                    return "-"
                if str(v) == str(gv):
                    return "✓"
                if canon(k, v) == canon(k, gv):
                    return "✓(归一后)"
                return "✗"

            field_rows.append({
                "file": s["file"],
                "field": k,
                "gt": "" if gv is None else gv,
                "raw": "" if rv is None else rv,
                "post": "" if pv is None else pv,
                "match_raw": verdict(rv),
                "match_post": verdict(pv),
            })

    f1 = out_dir / f"{args.label}_fields.csv"
    with open(f1, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file", "field", "gt", "raw", "post",
                                          "match_raw", "match_post"])
        w.writeheader()
        w.writerows(field_rows)

    f2 = out_dir / f"{args.label}_validation.csv"
    with open(f2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file", "verdict", *RULES, "nulled",
                                          "has_error", "silent_error",
                                          "false_alarm", "detail"])
        w.writeheader()
        w.writerows(val_rows)

    n = len(val_rows)
    err = [r for r in val_rows if r["has_error"] == "Y"]
    caught = [r for r in err if r["verdict"] != "ok"]
    silent = [r for r in err if r["verdict"] == "ok"]
    fa = [r for r in val_rows if r["false_alarm"] == "Y"]
    vc = Counter(r["verdict"] for r in val_rows)

    L = []
    L.append("=" * 70)
    L.append(f"Postprocess v4 报告   prompt={args.prompt_version}  "
             f"entities={args.entities or '(不限)'}")
    L.append("=" * 70)
    L.append(f"样本 {n} 张（离线重放，未重新调用模型）")
    L.append("")
    L.append("—— 判定分布 ——")
    for v in ("ok", "warn", "rejected"):
        L.append(f"  {v:<10}{vc[v]:>3}/{n} = {vc[v]/n:6.1%}")
    L.append("")
    L.append("—— 各规则命中 ——")
    for r in RULES:
        c = Counter(x[r] for x in val_rows)
        hits = [x["file"].replace(".jpg", "") for x in val_rows if x[r] == "fail"]
        L.append(f"  {r:<24}pass {c['pass']:>3}  fail {c['fail']:>3}  skipped {c['skipped']:>3}")
        if hits:
            L.append(f"    命中: {' '.join(sorted(hits, key=lambda x: key(x)))}")
    L.append("")
    L.append("—— 错误可见性 ——")
    L.append(f"  实际有错        {len(err):>3}/{n} = {len(err)/n:6.1%}")
    L.append(f"  被规则拦下       {len(caught):>3}/{len(err) or 1} = {len(caught)/(len(err) or 1):6.1%}  拦截率")
    L.append(f"  静默通过        {len(silent):>3}/{n} = {len(silent)/n:6.1%}  静默错误率")
    L.append(f"  正确却报警       {len(fa):>3}/{n - len(err) or 1} = {len(fa)/((n - len(err)) or 1):6.1%}  误伤率")
    L.append("")
    L.append("  静默错误样本: " + (" ".join(
        x["file"].replace(".jpg", "") for x in sorted(silent, key=lambda r: key(r["file"]))) or "(无)"))
    L.append("  误伤样本    : " + (" ".join(
        x["file"].replace(".jpg", "") for x in sorted(fa, key=lambda r: key(r["file"]))) or "(无)"))
    L.append("")
    L.append("—— 字段级 ——")
    # buyer_name / buyer_tax_id 在闭集模式下本就不由模型输出（只给 id），
    # 它们在 raw 里恒为空、在 post 里恒为正确，两边直接比会把
    # “后处理填了 102 个空字段”当成改善，掉进自己挖的坑。所以分开统计。
    BUYER_FILLED = {"buyer_name", "buyer_tax_id"}
    for tag in ("match_raw", "match_post"):
        ok = sum(1 for r in field_rows if r[tag] in ("✓", "✓(归一后)"))
        bad = sum(1 for r in field_rows if r[tag] == "✗")
        L.append(f"  {tag:<12} 匹配 {ok:>4}   不匹配 {bad:>4}   (含购买方填充字段)")
    L.append("")
    L.append("  排除 buyer_name / buyer_tax_id 后（可比口径）：")
    for tag in ("match_raw", "match_post"):
        sub = [r for r in field_rows if r["field"] not in BUYER_FILLED]
        ok = sum(1 for r in sub if r[tag] in ("✓", "✓(归一后)"))
        bad = sum(1 for r in sub if r[tag] == "✗")
        L.append(f"    {tag:<12} 匹配 {ok:>4}   不匹配 {bad:>4}")
    L.append("")
    L.append("  注：这两个口径下 match_post 都不会高于 match_raw —— 后处理不会把")
    L.append("      错值改对，只会把 seller_is_entity 命中的销售方置空（共 "
             f"{sum(1 for r in val_rows if r['nulled'])} 张 x 2 个字段）。")
    L.append("      置空不算退化：这些记录已标为 rejected 转人工。")

    text = "\n".join(L)
    print(text)
    f3 = out_dir / f"{args.label}_summary.txt"
    f3.write_text(text + "\n", encoding="utf-8")
    print(f"\n已写入:\n  {f1}\n  {f2}\n  {f3}")


if __name__ == "__main__":
    main()
