#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把金额组的新判定（fusion.fuse_amount_group）应用到已入库的发票，不重新调用任何模型。

融合层用到的各路读数都存在 parsing_diffs 的 qr_value / ocr_value / llm_value 里，
所以金额组这一段可以用库里的数据原样重跑。只处理 v6、待审核 / 待处理、且金额组四项
都还没人动过（没有人工填写、没有人工确认）的发票。

用的是设置页当前保存的金额规则与主体清单（app_settings 表；没保存过就是出厂值），
所以设置页改了阈值或税率库之后，也可以用它让已入库的发票按新规则重判。

    python eval/refuse_amounts.py                    # 只列出会变化的发票，不写库
    python eval/refuse_amounts.py --apply            # 写库
    python eval/refuse_amounts.py --apply --ids 3 5  # 只处理指定发票
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import async_session_maker  # noqa: E402
from app.models.invoice import Invoice, InvoiceStatus, ParsingDiff  # noqa: E402
from app.services import app_settings  # noqa: E402
from app.services.fusion import AMOUNT_GROUP, PIPELINE_VERSION, fuse_amount_group  # noqa: E402
from app.services.review import apply_to_invoice, fusion_row_values, revalidate  # noqa: E402


def untouched(rows) -> bool:
    """人没动过：没有人工填写，也没有人工确认（已核但机器置信是低 / 缺失）。"""
    return all(r.source != "manual" and not (r.resolved and r.confidence in ("low", "missing"))
               for r in rows)


def snapshot(rows):
    return {r.field_name: (r.final_value, r.source, r.confidence, r.resolved) for r in rows}


async def main(apply: bool, ids: list[int]) -> None:
    async with async_session_maker() as db:
        cfg = await app_settings.load(db)
        print(f"金额规则：{app_settings.amount_rules_to_dict(cfg.amount_rules)}")
        print(f"主体清单：{cfg.entities_version}")
        query = select(Invoice).where(
            Invoice.pipeline_version == PIPELINE_VERSION,
            Invoice.status.in_([InvoiceStatus.REVIEWING, InvoiceStatus.PENDING]),
        ).order_by(Invoice.id)
        if ids:
            query = query.where(Invoice.id.in_(ids))
        invoices = (await db.execute(query)).scalars().all()

        changed = 0
        for inv in invoices:
            diffs = (await db.execute(
                select(ParsingDiff).where(ParsingDiff.invoice_id == inv.id).order_by(ParsingDiff.id)
            )).scalars().all()
            by = {d.field_name: d for d in diffs}
            group = [by.get(k) for k in AMOUNT_GROUP]
            if None in group:
                print(f"#{inv.id} {inv.file_name}: 缺金额组的识别明细，跳过")
                continue
            if not untouched(group):
                print(f"#{inv.id} {inv.file_name}: 金额组已有人工操作，跳过")
                continue

            before = snapshot(group)
            fields, check = fuse_amount_group(
                {"amount": by["amount"].qr_value},
                {k: by[k].llm_value for k in AMOUNT_GROUP},
                {k: by[k].ocr_value for k in AMOUNT_GROUP},
                cfg.amount_rules,
            )
            for key, fr in fields.items():
                values = fusion_row_values(key, fr)
                for attr in ("final_value", "source", "confidence", "note", "resolved"):
                    setattr(by[key], attr, values[attr])
                apply_to_invoice(inv, key, fr.value)

            checks, verdict = revalidate(diffs, inv.nulled or [], cfg.entities, cfg.amount_rules)
            inv.checks = [c.as_dict() for c in checks]
            inv.verdict = verdict
            inv.degraded = [d for d in (inv.degraded or []) if d != "computed_amounts"]
            # 与 invoice_service.process_invoice 一致：判定 ok 才自动确认
            inv.status = InvoiceStatus.CONFIRMED if verdict == "ok" else InvoiceStatus.REVIEWING

            after = snapshot(group)
            if after != before:
                changed += 1
            pending = [k for k in AMOUNT_GROUP if not by[k].resolved]
            print(f"#{inv.id} {inv.file_name}: {check.rule}"
                  f"{'，待复核 ' + '、'.join(pending) if pending else '，金额组自动放行'}"
                  f"{'' if after != before else '（无变化）'}")

        if apply:
            await db.commit()
            print(f"已写库：{changed} 张发票的金额组有变化")
        else:
            await db.rollback()
            print(f"预览：{changed} 张发票的金额组会变化（加 --apply 写库）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库；不加只预览")
    ap.add_argument("--ids", type=int, nargs="*", default=[], help="只处理这些发票 id")
    args = ap.parse_args()
    asyncio.run(main(args.apply, args.ids))
