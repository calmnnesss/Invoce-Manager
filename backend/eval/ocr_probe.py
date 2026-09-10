#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwen3.5-ocr 的 key_information_extraction 独立探针。

完全独立的实验脚本：不 import app 里的任何服务，不写数据库，不影响现有
LLM 线。日志单独落在 --out-dir，不污染 logs/llm/。

要在容器里跑（dashscope 装在容器内）：
    docker cp eval/ocr_probe.py invoice_backend:/tmp/ocr_probe.py
    docker exec invoice_backend python /tmp/ocr_probe.py --schema s0 --rotate both

两版 schema：
    s0  中性描述 —— 只说字段是什么，不给任何锚点提示。
        测的是模型自身的能力，是干净的基线。
    s1  带锚点 —— 描述里写明栏位标签、发票专用章等判据。
        测的是模型 + 提示工程。s0 跑完再上，提升才能归因。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- schema
# 中文键名是 OCR 返回的 kv_result 的键；英文名用于对齐现有评测口径。
FIELD_MAP = {
    "发票代码": "invoice_code",
    "发票号码": "invoice_number",
    "开票日期": "issue_date",
    "购买方名称": "buyer_name",
    "购买方纳税人识别号": "buyer_tax_id",
    "销售方名称": "seller_name",
    "销售方纳税人识别号": "seller_tax_id",
    "金额": "amount",
    "税额": "tax_amount",
    "价税合计": "total_with_tax",
    "税率": "tax_rate",
}

SCHEMA_S0 = {
    "发票代码": "发票代码",
    "发票号码": "发票号码",
    "开票日期": "开票日期，格式为年-月-日，比如2016-06-12",
    "购买方名称": "购买方的公司全称",
    "购买方纳税人识别号": "购买方的纳税人识别号",
    "销售方名称": "销售方的公司全称",
    "销售方纳税人识别号": "销售方的纳税人识别号",
    "金额": "不含税金额合计，纯数字",
    "税额": "税额合计，纯数字",
    "价税合计": "价税合计的小写金额，纯数字",
    "税率": "税率，如17%",
}

SCHEMA_S1 = dict(SCHEMA_S0)
SCHEMA_S1.update({
    "购买方名称": "标注为“购买方”的信息块中“名称”一行的公司全称。"
                  "不要填写“地址、电话”或“开户行及账号”行里的内容",
    "购买方纳税人识别号": "标注为“购买方”的信息块中“纳税人识别号”一行的编码",
    "销售方名称": "标注为“销售方”的信息块中“名称”一行的公司全称。"
                  "红色发票专用章上的公司名即销售方",
    "销售方纳税人识别号": "标注为“销售方”的信息块中“纳税人识别号”一行的编码",
})

SCHEMAS = {"s0": SCHEMA_S0, "s1": SCHEMA_S1}

# ------------------------------------------------------------ 归一化
# OCR 模型是“所见即所得”取向，不太遵守 schema 里的格式要求（日期会返回
# “2016年06月12日”）。如果按严格字符串比，“格式不合”会和“识别错”混在一起，
# 看不出分辨率到底有没有帮助。这里先归一再比。
NUMERIC = {"amount", "tax_amount", "total_with_tax"}


def canon(field: str, value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None
    if field in NUMERIC:
        s2 = s.replace(",", "").replace("，", "")
        m = re.search(r"\d+(?:\.\d+)?", s2)
        return f"{float(m.group()):.2f}" if m else s
    if field == "issue_date":
        m = re.search(r"(\d{4})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})", s)
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else s
    if field in ("buyer_tax_id", "seller_tax_id", "invoice_code", "invoice_number"):
        return re.sub(r"[\s\-]", "", s.upper())
    if field == "tax_rate":
        return s.replace(" ", "")
    return re.sub(r"\s+", "", s)

# 诊断样本：覆盖三类已知失败 + 一张对照
DEFAULT_FILES = [
    "b3",    # 对照：横图，v1 起一直全对
    "b2",    # 晶东 -> 晶乐
    "b10",   # 晶东 -> 品乐
    "b25",   # 晶东 -> 品乐
    "b36",   # 晶东 -> 晶沃
    "b34",   # 深圳鼎东（地名+字号都错）
    "b21",   # 鼎东 + 税号第16/17位互换
    "b20",   # 销售方税号少一位
    "b22",   # 角色错：销售方被填成本公司
]


def load_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    for k in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        if os.getenv(k):
            return os.getenv(k)
    # 兜底：从容器里的 .env 读
    for p in (Path("/app/.env"), Path(".env")):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("QWEN_API_KEY="):
                    return line.split("=", 1)[1].strip()
    sys.exit("找不到 API Key：设置 DASHSCOPE_API_KEY 或用 --api-key 传入")


def extract_kv(resp) -> tuple[dict | None, str, dict]:
    """从 DashScope 响应里取 ocr_result.kv_result，同时返回原始文本与用量。"""
    raw = json.loads(json.dumps(resp, default=str)) if not isinstance(resp, dict) else resp
    kv, text = None, ""
    try:
        content = resp["output"]["choices"][0]["message"]["content"]
        for part in content:
            if not isinstance(part, dict):
                continue
            if "ocr_result" in part and isinstance(part["ocr_result"], dict):
                kv = part["ocr_result"].get("kv_result")
            if "text" in part:
                text = part["text"]
    except Exception:  # noqa: BLE001
        pass
    usage = {}
    try:
        usage = dict(resp["usage"])
    except Exception:  # noqa: BLE001
        pass
    return kv, text, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-dir", default="/tmp/ocr_imgs")
    ap.add_argument("--out-dir", default="/tmp/ocr_out")
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--schema", default="s0", choices=["s0", "s1"])
    ap.add_argument("--schema-file", default="/tmp/ocr_schema.json",
                    help="外部 schema JSON；存在则覆盖 --schema 选的内置版本")
    ap.add_argument("--rotate", default="off", choices=["on", "off", "both"])
    ap.add_argument("--model", default="qwen3.5-ocr")
    ap.add_argument("--pixels-levels", default="8192",
                    help="逗号分隔的 N，实际 max_pixels = 32*32*N。"
                         "原图 3120x4208=1313万像素 ≈ N=12824，N>=12824 即不压缩")
    ap.add_argument("--min-pixels", type=int, default=32 * 32 * 3)
    ap.add_argument("--base-url", default=None,
                    help="留空用 dashscope 默认端点；北京业务空间形如 "
                         "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--gt", default="/tmp/ground_truth.json")
    args = ap.parse_args()

    import dashscope
    from dashscope import MultiModalConversation

    if args.base_url:
        dashscope.base_http_api_url = args.base_url
    api_key = load_api_key(args.api_key)

    schema = SCHEMAS[args.schema]
    schema_src = f"内置 {args.schema}"
    if args.schema_file and Path(args.schema_file).exists():
        schema = json.loads(Path(args.schema_file).read_text(encoding="utf-8"))
        schema_src = args.schema_file
    rotates = {"on": [True], "off": [False], "both": [False, True]}[args.rotate]
    levels = [int(x) for x in args.pixels_levels.split(",") if x.strip()]
    files = [f.strip() for f in args.files.split(",") if f.strip()]

    gt = {}
    if Path(args.gt).exists():
        gt = json.loads(Path(args.gt).read_text(encoding="utf-8"))["constant_fields"]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_calls = len(files) * len(rotates) * len(levels)
    print(f"模型 {args.model}  schema={schema_src}  rotate={rotates}")
    print("schema 内容:")
    print(json.dumps(schema, ensure_ascii=False, indent=2))
    print(f"max_pixels 档位 (32*32*N): {levels}")
    print(f"端点 {dashscope.base_http_api_url}")
    print(f"样本 {len(files)} 张 x {len(rotates)} rotate x {len(levels)} 档 = {total_calls} 次调用\n")

    results = []
    for lvl in levels:
        max_px = 32 * 32 * lvl
        for rot in rotates:
            for name in files:
                path = Path(args.image_dir) / f"{name}.jpg"
                if not path.exists():
                    print(f"  ! 缺图 {path}")
                    continue
                messages = [{
                    "role": "user",
                    "content": [{
                        "image": f"file://{path}",
                        "min_pixels": args.min_pixels,
                        "max_pixels": max_px,
                        "enable_rotate": rot,
                    }],
                }]
                t0 = time.time()
                try:
                    resp = MultiModalConversation.call(
                        api_key=api_key,
                        model=args.model,
                        messages=messages,
                        ocr_options={
                            "task": "key_information_extraction",
                            "task_config": {"result_schema": schema},
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {name} N={lvl} 调用失败: {type(e).__name__}: {e}")
                    continue
                dt = time.time() - t0

                if getattr(resp, "status_code", 200) != 200:
                    print(f"  ! {name} N={lvl} HTTP {resp.status_code}: "
                          f"{getattr(resp, 'message', '')}")
                    continue

                kv, text, usage = extract_kv(resp)
                tag = f"{name}_px{lvl}_rot{int(rot)}_{args.schema}"
                (out_dir / f"{tag}.json").write_text(
                    json.dumps({
                        "file": name, "rotate": rot, "schema": args.schema,
                        "max_pixels_n": lvl, "max_pixels": max_px,
                        "model": args.model, "elapsed_s": round(dt, 2),
                        "kv_result": kv, "text": text, "usage": usage,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8")

                row = {"file": name, "rotate": rot, "n": lvl,
                       "elapsed": dt, "usage": usage}
                for cn, en in FIELD_MAP.items():
                    row[en] = (kv or {}).get(cn)
                results.append(row)
                nulls = sum(1 for en in FIELD_MAP.values() if row.get(en) is None)
                print(f"  N={lvl:<6}{name:<6} {dt:5.1f}s  "
                      f"img_tok={usage.get('image_tokens', '?'):<6} "
                      f"null={nulls}/{len(FIELD_MAP)}")

    (out_dir / f"_results_{args.schema}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if not results:
        sys.exit("\n没有成功的结果。")

    # ---------------------------------------------------------- 对照 GT
    # 发票号码每张不同、尚未标注，不参与判定
    skip = {"invoice_number"}
    scored = [en for en in FIELD_MAP.values() if en not in skip and en in gt]

    def judge(row, en):
        """返回 ok / null / wrong。null 和 wrong 必须分开：
        前者是“模型不知道”，后者是“模型编了个错的”，产品含义完全不同。"""
        v = canon(en, row.get(en))
        if v is None:
            return "null"
        return "ok" if v == canon(en, gt[en]) else "wrong"

    print("\n" + "=" * 78)
    print("各档位对照（已归一化；发票号码不参与）")
    print("=" * 78)
    for lvl in levels:
        sub = [r for r in results if r["n"] == lvl]
        if not sub:
            continue
        tot = len(sub) * len(scored)
        c = {"ok": 0, "null": 0, "wrong": 0}
        per_field = {en: {"ok": 0, "null": 0, "wrong": 0} for en in scored}
        for r in sub:
            for en in scored:
                v = judge(r, en)
                c[v] += 1
                per_field[en][v] += 1
        full = sum(1 for r in sub if all(judge(r, en) == "ok" for en in scored))
        img_tok = sum(r["usage"].get("image_tokens", 0) for r in sub) / len(sub)
        print(f"\n  N={lvl}  (max_pixels={32*32*lvl:,}像素, 平均图像 token {img_tok:.0f})")
        print(f"    字段格  正确 {c['ok']:>3}/{tot}   空值 {c['null']:>3}   错值 {c['wrong']:>3}")
        print(f"    整单全对 {full}/{len(sub)}")
        worst = [(en, d) for en, d in per_field.items() if d["ok"] < len(sub)]
        for en, d in worst:
            print(f"      {en:<18} ok {d['ok']:>2}  null {d['null']:>2}  wrong {d['wrong']:>2}")

    print("\n" + "=" * 78)
    print("逐张逐档：销售方名称（当前最大瓶颈）")
    print("=" * 78)
    names = sorted({r["file"] for r in results}, key=lambda x: int(x.lstrip("b")))
    header = "  " + f"{'file':<7}" + "".join(f"N={l:<11}" for l in levels)
    print(header)
    for nm in names:
        line = f"  {nm:<7}"
        for lvl in levels:
            row = next((r for r in results if r["file"] == nm and r["n"] == lvl), None)
            v = (row or {}).get("seller_name")
            line += f"{str(v)[:12]:<13}"
        print(line)

    tok = sum(r["usage"].get("total_tokens", 0) for r in results)
    print(f"\n累计 token: {tok}   平均耗时: "
          f"{sum(r['elapsed'] for r in results) / len(results):.1f}s")
    print(f"原始响应已存: {out_dir}")


if __name__ == "__main__":
    main()
