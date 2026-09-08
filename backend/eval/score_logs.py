#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 线离线评测器。

读 backend/logs/llm/*.log 里已经落盘的 dump，对照 ground_truth.json 打分。
不发起任何 API 调用，零成本、可重复运行，用于同一批图在不同 prompt /
不同图像预处理下的 A/B 对比。

典型用法
--------
    # 零参数直接跑（IDE 里可直接 Run）：读全部日志，结果存到 results/run.json
    python eval/score_logs.py

    # 跑一次并把结果存为基线
    python eval/score_logs.py --label baseline

    # 改完 prompt 重跑一遍发票后，只评 v2 的日志，并和基线对比
    python eval/score_logs.py --prompt-version v2 --label v2_prompt --compare baseline

所有参数都有默认值，路径基于 __file__ 解析，不依赖工作目录。
但只重跑了部分发票时务必加 --prompt-version，否则"每个文件取最新一条"
会把新旧两个版本的结果混在一起统计。

指标说明
--------
direction_accuracy  买卖方没有颠倒的样本占比。这是本数据集最关键的指标。
exact_match         10 个受评字段全部正确的样本占比。
field_accuracy      分字段准确率，同时给出 raw（模型原始输出）和
                    clean（经 _normalize_field_value 清洗后）两列。
cleaner_dropped     清洗器把一个非空的错值直接吞成 None 的次数。
                    这类是"信息丢失"：下游连模型给过什么都不知道。
cleaner_fixed       清洗器把错值修正成正确值的次数。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
DEFAULT_LOG_DIR = BACKEND_DIR / "logs" / "llm"
RESULTS_DIR = EVAL_DIR / "results"

NUMERIC_FIELDS = {"amount", "tax_amount", "total_with_tax"}
PARTY_FIELDS = ["buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id"]

# 日志文件名有两种形态：带上传文件名的、和早期不带的
LOG_RE_FULL = re.compile(
    r"^(?P<ts>[\d\-]+)_invoice-(?P<iid>\d+)_(?P<fname>.+)_(?P<provider>[^_]+)_(?P<status>[^_.]+)\.log$"
)
LOG_RE_SHORT = re.compile(
    r"^(?P<ts>[\d\-]+)_invoice-(?P<iid>\d+)_(?P<provider>[^_]+)_(?P<status>[^_.]+)\.log$"
)

MARK_RAW = "[1] 原始响应"
MARK_CLEAN = "[3] 值清洗后"
MARK_MODEL = "Model"


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def extract_json_after(text: str, marker: str):
    """从 marker 之后取第一个完整的 {...} 块并解析。"""
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(text)):
        ch = text[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[j : k + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_log(path: Path):
    m = LOG_RE_FULL.match(path.name) or LOG_RE_SHORT.match(path.name)
    if not m:
        return None
    g = m.groupdict()
    text = path.read_text(encoding="utf-8", errors="replace")

    model = None
    mm = re.search(r"^Model\s*:\s*(.+)$", text, re.M)
    if mm:
        model = mm.group(1).strip()

    # v2 起，dump 里会记录本次调用使用的 prompt 版本。
    # 更早的日志没有这一行，一律归入 v1。
    prompt_version = "v1"
    pm = re.search(r"^Prompt\s*:\s*(\S+)\s*$", text, re.M)
    if pm and pm.group(1) != "(unknown)":
        prompt_version = pm.group(1).strip()

    return {
        "log": path.name,
        "ts": g["ts"],
        "invoice_id": int(g["iid"]),
        "file": g.get("fname"),
        "provider": g["provider"],
        "status": g["status"],
        "model": model,
        "prompt_version": prompt_version,
        "raw": extract_json_after(text, MARK_RAW) or {},
        "clean": extract_json_after(text, MARK_CLEAN) or {},
    }


# --------------------------------------------------------------------------
# 归一化与比对
# --------------------------------------------------------------------------
def canon(field: str, value) -> str | None:
    """把一个字段值规约成可比对的形式。GT 和预测走同一条路。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    if field in NUMERIC_FIELDS:
        s = s.replace(",", "").replace("，", "")
        mm = re.search(r"\d+(?:\.\d+)?", s)
        return f"{float(mm.group()):.2f}" if mm else None

    if field == "issue_date":
        mm = re.search(r"(\d{4})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})", s)
        if mm:
            y, mo, d = mm.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        return s

    if field == "tax_rate":
        s = s.replace(" ", "")
        return s

    if field in ("buyer_tax_id", "seller_tax_id", "invoice_code", "invoice_number"):
        return re.sub(r"[\s\-]", "", s.upper())

    return re.sub(r"\s+", "", s)


def sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def which_side(name, tax_id, gt) -> str | None:
    """判断一组(名称,税号)更像购买方还是销售方。用于识别买卖方颠倒。"""
    n = canon("buyer_name", name)
    t = canon("buyer_tax_id", tax_id)
    sb = max(sim(n, canon("buyer_name", gt["buyer_name"])),
             sim(t, canon("buyer_tax_id", gt["buyer_tax_id"])))
    ss = max(sim(n, canon("seller_name", gt["seller_name"])),
             sim(t, canon("seller_tax_id", gt["seller_tax_id"])))
    if max(sb, ss) < 0.5:
        return None            # 两边都不像 —— 幻觉或串栏
    return "buyer" if sb > ss else "seller"


def classify_party(field: str, value, gt) -> str:
    """四个主体字段的错误类型。"""
    own = canon(field, gt[field])
    other_field = field.replace("buyer", "seller") if "buyer" in field else field.replace("seller", "buyer")
    other = canon(other_field, gt[other_field])
    v = canon(field, value)

    if v is None:
        return "missing"
    if v == own:
        return "exact"
    if v == other:
        return "swapped"
    if sim(v, own) >= 0.6:
        return "near_miss"
    if sim(v, other) >= 0.6:
        return "swapped_near_miss"
    return "unrelated"


# --------------------------------------------------------------------------
# 评分
# --------------------------------------------------------------------------
def score(samples, gt_cfg, image_meta):
    gt = gt_cfg["constant_fields"]
    fields = gt_cfg["scored_fields"]

    per_sample = []
    for s in samples:
        raw, clean = s["raw"], s["clean"]
        rec = {
            "file": s["file"],
            "invoice_id": s["invoice_id"],
            "log": s["log"],
            "fields": {},
        }

        for f in fields:
            gtv = canon(f, gt[f])
            rv = canon(f, raw.get(f))
            cv = canon(f, clean.get(f))
            rec["fields"][f] = {
                "gt": gtv,
                "raw": rv,
                "clean": cv,
                "raw_ok": rv == gtv,
                "clean_ok": cv == gtv,
                "cleaner_dropped": (rv is not None and cv is None and rv != gtv),
                "cleaner_fixed": (rv != gtv and cv == gtv),
            }
            if f in PARTY_FIELDS:
                rec["fields"][f]["kind"] = classify_party(f, raw.get(f), gt)

        buyer_side = which_side(raw.get("buyer_name"), raw.get("buyer_tax_id"), gt)
        seller_side = which_side(raw.get("seller_name"), raw.get("seller_tax_id"), gt)
        rec["buyer_side"] = buyer_side
        rec["seller_side"] = seller_side
        rec["direction_ok"] = (buyer_side == "buyer" and seller_side == "seller")
        rec["direction_swapped"] = (buyer_side == "seller" and seller_side == "buyer")

        rec["exact_match"] = all(rec["fields"][f]["raw_ok"] for f in fields)

        # 勾稽：金额 + 税额 == 价税合计
        try:
            a = float(canon("amount", raw.get("amount")))
            t = float(canon("tax_amount", raw.get("tax_amount")) or 0)
            tot = float(canon("total_with_tax", raw.get("total_with_tax")))
            rec["arith_ok"] = abs(a + t - tot) < 0.02
        except (TypeError, ValueError):
            rec["arith_ok"] = None

        dims = (image_meta or {}).get(s["file"])
        rec["orientation"] = None
        if dims:
            rec["orientation"] = "landscape" if dims[0] >= dims[1] else "portrait"

        per_sample.append(rec)

    return per_sample


def summarize(per_sample, gt_cfg):
    fields = gt_cfg["scored_fields"]
    n = len(per_sample)
    out = {"n": n, "fields": {}}

    for f in fields:
        raw_ok = sum(1 for r in per_sample if r["fields"][f]["raw_ok"])
        clean_ok = sum(1 for r in per_sample if r["fields"][f]["clean_ok"])
        out["fields"][f] = {
            "raw_ok": raw_ok,
            "raw_acc": raw_ok / n if n else 0.0,
            "clean_ok": clean_ok,
            "clean_acc": clean_ok / n if n else 0.0,
            "cleaner_dropped": sum(1 for r in per_sample if r["fields"][f]["cleaner_dropped"]),
            "cleaner_fixed": sum(1 for r in per_sample if r["fields"][f]["cleaner_fixed"]),
        }

    out["direction_ok"] = sum(1 for r in per_sample if r["direction_ok"])
    out["direction_acc"] = out["direction_ok"] / n if n else 0.0
    out["direction_swapped"] = sum(1 for r in per_sample if r["direction_swapped"])
    out["exact_match"] = sum(1 for r in per_sample if r["exact_match"])
    out["exact_match_acc"] = out["exact_match"] / n if n else 0.0
    total_cells = n * len(fields)
    out["field_acc_micro"] = (
        sum(out["fields"][f]["raw_ok"] for f in fields) / total_cells if total_cells else 0.0
    )
    out["arith_fail"] = sum(1 for r in per_sample if r["arith_ok"] is False)

    kinds = Counter()
    for r in per_sample:
        for f in PARTY_FIELDS:
            kinds[r["fields"][f].get("kind", "?")] += 1
    out["party_error_kinds"] = dict(kinds)

    by_or = {}
    for r in per_sample:
        o = r["orientation"] or "unknown"
        b = by_or.setdefault(o, {"n": 0, "direction_ok": 0})
        b["n"] += 1
        b["direction_ok"] += int(r["direction_ok"])
    for o, b in by_or.items():
        b["direction_acc"] = b["direction_ok"] / b["n"] if b["n"] else 0.0
    out["by_orientation"] = by_or

    return out


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def pct(x):
    return f"{x * 100:5.1f}%"


def report(summary, per_sample, meta):
    L = []
    L.append("=" * 74)
    L.append(f"LLM 线评测报告   run={meta['label']}")
    L.append("=" * 74)
    L.append(f"日志目录 : {meta['log_dir']}")
    L.append(f"筛选     : after={meta['after'] or '-'}  样本={summary['n']} 张（同名文件取最新一次）")
    L.append(f"模型     : {meta['provider']} / {meta['model']}")
    L.append(f"Prompt   : {'/'.join(meta.get('prompt_versions') or ['?'])}")
    L.append("口径     : 所有核心指标与 --compare 对比一律基于 raw（模型原始输出）；")
    L.append("           分字段表里的 clean 列只作清洗器诊断，不参与任何结论")
    L.append("")
    L.append("—— 核心指标 ——")
    L.append(f"  方向正确率 (买卖方未颠倒)   {summary['direction_ok']:>3}/{summary['n']}  = {pct(summary['direction_acc'])}")
    L.append(f"  其中明确颠倒的样本          {summary['direction_swapped']:>3}/{summary['n']}")
    L.append(f"  Exact Match (10 字段全对)   {summary['exact_match']:>3}/{summary['n']}  = {pct(summary['exact_match_acc'])}")
    L.append(f"  字段级微平均准确率                     {pct(summary['field_acc_micro'])}")
    L.append("")
    L.append("—— 分字段准确率 ——")
    L.append(f"  {'字段':<18}{'raw':>8}{'clean':>9}{'清洗吞值':>10}{'清洗修复':>10}")
    for f, d in summary["fields"].items():
        L.append(
            f"  {f:<18}{pct(d['raw_acc']):>8}{pct(d['clean_acc']):>9}"
            f"{d['cleaner_dropped']:>10}{d['cleaner_fixed']:>10}"
        )
    L.append("")
    L.append("—— 主体字段错误类型分布（4 字段 × 样本数）——")
    order = ["exact", "swapped", "swapped_near_miss", "near_miss", "unrelated", "missing"]
    label = {
        "exact": "完全正确",
        "swapped": "与对方栏完全对调",
        "swapped_near_miss": "对调且有字形错误",
        "near_miss": "本栏正确但字形有错",
        "unrelated": "与两栏都不沾边（幻觉/串栏）",
        "missing": "空值",
    }
    tot = sum(summary["party_error_kinds"].values())
    for k in order:
        v = summary["party_error_kinds"].get(k, 0)
        if v:
            L.append(f"  {label[k]:<28}{v:>4}  ({v / tot * 100:4.1f}%)")
    L.append("")
    if len(summary["by_orientation"]) > 1 or "unknown" not in summary["by_orientation"]:
        L.append("—— 按图片方向 ——")
        name = {"landscape": "横向（发票正常方向）", "portrait": "竖向（被旋转 90/270°）", "unknown": "未知"}
        for o, b in sorted(summary["by_orientation"].items()):
            L.append(f"  {name.get(o, o):<24}{b['n']:>3} 张   方向正确 {b['direction_ok']:>2}  = {pct(b['direction_acc'])}")
        L.append("")
    L.append("—— 勾稽校验 ——")
    L.append(f"  金额 + 税额 ≠ 价税合计 : {summary['arith_fail']} 条")
    L.append("")
    bad = [r for r in per_sample if not r["direction_ok"]]
    L.append(f"—— 方向出错的文件（{len(bad)}）——")
    L.append("  " + " ".join(sorted((r["file"] or f"id{r['invoice_id']}") for r in bad)))
    return "\n".join(L)


def compare_report(cur, base, base_label):
    L = ["", "=" * 74, f"与 {base_label} 对比", "=" * 74]
    rows = [
        ("方向正确率", base["direction_acc"], cur["direction_acc"]),
        ("Exact Match", base["exact_match_acc"], cur["exact_match_acc"]),
        ("字段级微平均", base["field_acc_micro"], cur["field_acc_micro"]),
    ]
    for f in cur["fields"]:
        if f in base["fields"]:
            rows.append((f, base["fields"][f]["raw_acc"], cur["fields"][f]["raw_acc"]))
    L.append(f"  {'指标':<20}{'baseline':>10}{'current':>10}{'Δ':>10}")
    for name, b, c in rows:
        d = (c - b) * 100
        L.append(f"  {name:<20}{pct(b):>10}{pct(c):>10}{d:>+9.1f}pt")
    return "\n".join(L)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="LLM 线离线评测")
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--gt", default=str(EVAL_DIR / "ground_truth.json"))
    ap.add_argument("--image-meta", default=str(EVAL_DIR / "image_meta.json"))
    ap.add_argument("--after", default=None, help="只统计时间戳 >= 该前缀的日志，如 20260908-1000")
    ap.add_argument("--prompt-version", default=None,
                    help="只统计使用该 prompt 版本的日志，如 v2。比 --after 更可靠")
    ap.add_argument("--label", default="run", help="本次结果的名字，存到 eval/results/<label>.json")
    ap.add_argument("--compare", default=None, help="与 eval/results/<name>.json 对比")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    gt_cfg = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    image_meta = {}
    p = Path(args.image_meta)
    if p.exists():
        image_meta = json.loads(p.read_text(encoding="utf-8"))

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        sys.exit(f"日志目录不存在: {log_dir}")

    exclude = set(gt_cfg.get("exclude_files", []))
    parsed = []
    for f in sorted(log_dir.glob("*.log")):
        s = parse_log(f)
        if not s or not s["file"]:
            continue                       # 早期没带文件名的日志无法对齐，跳过
        if s["file"] in exclude:
            continue
        if args.after and s["ts"] < args.after:
            continue
        if args.prompt_version and s["prompt_version"] != args.prompt_version:
            continue
        if s["status"] != "OK":
            continue
        parsed.append(s)

    # 同一文件可能被跑过多次，只保留最新一次
    latest = {}
    for s in parsed:
        cur = latest.get(s["file"])
        if cur is None or s["ts"] > cur["ts"]:
            latest[s["file"]] = s
    samples = sorted(latest.values(), key=lambda s: s["file"])

    if not samples:
        sys.exit("没有匹配到任何样本，检查 --log-dir / --after / --prompt-version")

    # 只重跑了一部分发票时，"每个文件取最新一条" 会把两个版本的结果
    # 拌在一起，算出来的数字看着正常但完全不可比。这里显式拦一下。
    versions = Counter(s["prompt_version"] for s in samples)
    if len(versions) > 1 and not args.prompt_version:
        print("[警告] 本次统计混入了多个 prompt 版本，结果不可比：")
        for v, c in sorted(versions.items()):
            print(f"         {v}: {c} 张")
        print("         请加 --prompt-version 指定其中一个版本后重跑。\n")

    per_sample = score(samples, gt_cfg, image_meta)
    summary = summarize(per_sample, gt_cfg)

    meta = {
        "label": args.label,
        "log_dir": str(log_dir),
        "after": args.after,
        "provider": samples[0]["provider"],
        "model": samples[0]["model"],
        "prompt_versions": sorted({s["prompt_version"] for s in samples}),
    }
    print(report(summary, per_sample, meta))

    if args.compare:
        bp = RESULTS_DIR / f"{args.compare}.json"
        if bp.exists():
            base = json.loads(bp.read_text(encoding="utf-8"))["summary"]
            print(compare_report(summary, base, args.compare))
        else:
            print(f"\n[warn] 找不到对比基线 {bp}")

    if not args.no_save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"{args.label}.json"
        out.write_text(
            json.dumps({"meta": meta, "summary": summary, "per_sample": per_sample},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()
