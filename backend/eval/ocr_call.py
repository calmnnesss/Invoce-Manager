#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单张图调用 qwen3.5-ocr，把请求和完整原始响应都打到控制台。

这是个"看得见"的调试脚本：不做评分、不做汇总、不和 GT 比对，
只如实打印发出去什么、收回来什么。schema 放在外部 JSON 里，随便改。

    python /tmp/ocr_call.py --image /tmp/ocr_imgs/b20.jpg
    python /tmp/ocr_call.py --image /tmp/ocr_imgs/b20.jpg --rotate --n 2048
    python /tmp/ocr_call.py --image /tmp/ocr_imgs/b20.jpg --task none \
        --prompt "请只输出这张发票的价税合计小写金额"

参数
----
--task  key_information_extraction  用 schema 做结构化抽取（默认）
        advanced_recognition        高精识别，返回每行文字 + 坐标
        text_recognition            通用文字识别，返回纯文本
        none                        不设内置任务，只用 --prompt
--n     max_pixels = 32*32*N。原图 3120x4208 ≈ N=12824，N 更大即不压缩
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def load_api_key(explicit):
    if explicit:
        return explicit
    for k in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        if os.getenv(k):
            return os.getenv(k)
    for p in (Path("/app/.env"), Path(".env")):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("QWEN_API_KEY="):
                    return line.split("=", 1)[1].strip()
    sys.exit("找不到 API Key：设置 DASHSCOPE_API_KEY 或用 --api-key 传入")


def to_plain(obj):
    """把 dashscope 的响应对象转成可 json.dumps 的普通结构。"""
    return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--model", default="qwen3.5-ocr")
    ap.add_argument("--task", default="key_information_extraction",
                    choices=["key_information_extraction", "advanced_recognition",
                             "text_recognition", "table_parsing",
                             "document_parsing", "none"])
    ap.add_argument("--schema-file", default="/tmp/ocr_schema.json")
    ap.add_argument("--prompt", default=None,
                    help="额外的 User Message 文本。qwen3.5-ocr 起，它与内置任务"
                         "提示词叠加而非覆盖")
    ap.add_argument("--n", type=int, default=8192, help="max_pixels = 32*32*N")
    ap.add_argument("--min-n", type=int, default=3, help="min_pixels = 32*32*N")
    ap.add_argument("--rotate", action="store_true", help="开启 enable_rotate")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--save", default=None, help="把完整响应另存为 JSON")
    args = ap.parse_args()

    import dashscope
    from dashscope import MultiModalConversation

    if args.base_url:
        dashscope.base_http_api_url = args.base_url
    api_key = load_api_key(args.api_key)

    img = Path(args.image)
    if not img.exists():
        sys.exit(f"图片不存在: {img}")

    content = [{
        "image": f"file://{img.resolve()}",
        "min_pixels": 32 * 32 * args.min_n,
        "max_pixels": 32 * 32 * args.n,
        "enable_rotate": args.rotate,
    }]
    if args.prompt:
        content.append({"text": args.prompt})

    messages = [{"role": "user", "content": content}]

    kwargs = {}
    if args.task != "none":
        opts = {"task": args.task}
        if args.task == "key_information_extraction":
            schema = json.loads(Path(args.schema_file).read_text(encoding="utf-8"))
            opts["task_config"] = {"result_schema": schema}
        kwargs["ocr_options"] = opts

    bar = "=" * 78
    print(bar)
    print("请求")
    print(bar)
    print(f"  endpoint     : {dashscope.base_http_api_url}")
    print(f"  model        : {args.model}")
    print(f"  image        : {img}  ({img.stat().st_size:,} bytes)")
    print(f"  max_pixels   : {32*32*args.n:,}   (32*32*{args.n})")
    print(f"  min_pixels   : {32*32*args.min_n:,}")
    print(f"  enable_rotate: {args.rotate}")
    print(f"  task         : {args.task}")
    if args.prompt:
        print(f"  text prompt  : {args.prompt}")
    else:
        print("  text prompt  : (未传，仅使用内置任务提示词)")
    if "ocr_options" in kwargs:
        print("\n  ocr_options:")
        print(json.dumps(kwargs["ocr_options"], ensure_ascii=False, indent=4))

    t0 = time.time()
    resp = MultiModalConversation.call(
        api_key=api_key, model=args.model, messages=messages, **kwargs
    )
    dt = time.time() - t0

    print()
    print(bar)
    print(f"完整原始响应   （耗时 {dt:.2f}s）")
    print(bar)
    plain = to_plain(resp)
    print(json.dumps(plain, ensure_ascii=False, indent=2))

    # 单独把结构化结果拎出来，省得在长 JSON 里找
    print()
    print(bar)
    print("提取出的关键部分")
    print(bar)
    try:
        parts = plain["output"]["choices"][0]["message"]["content"]
        for i, part in enumerate(parts):
            for k, v in part.items():
                print(f"\n  content[{i}].{k}:")
                if isinstance(v, (dict, list)):
                    print(json.dumps(v, ensure_ascii=False, indent=4))
                else:
                    print(f"    {v}")
    except Exception as e:  # noqa: BLE001
        print(f"  解析失败: {type(e).__name__}: {e}")

    print("\n  usage:", json.dumps(plain.get("usage", {}), ensure_ascii=False))

    if args.save:
        Path(args.save).write_text(
            json.dumps(plain, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存: {args.save}")


if __name__ == "__main__":
    main()
