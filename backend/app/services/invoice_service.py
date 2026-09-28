"""Invoice processing service."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.models.invoice import Invoice, OcrResult, LlmResult, ParsingDiff, InvoiceStatus
from app.services.llm_service import get_llm_service, LLMCallContext
from app.services.qwen_ocr_service import (
    OCR_LINE_VERSION,
    TRUSTED_KEYS,
    get_qwen_ocr_service,
)
from app.services import app_settings
from app.services.fusion import PIPELINE_VERSION, OCR_KEY_MAP, fuse
from app.services.review import fusion_row_values
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# v6 不再使用本地 PaddleOCR 线路（已存档到仓库根目录 archive/paddle-ocr/）。
# 两路仲裁 _compare_and_resolve 也一并停用 —— 它会拿一路的值去填补另一路的
# 空字段，会把 fuse() 里 seller_is_entity 命中后的置空又填回去，
# 让拒绝机制在端到端流程里失效。

# Separate thread pools for OCR and LLM to maximize throughput
# OCR is CPU-bound (image processing), LLM is I/O-bound (API calls)
# Using separate pools prevents one from blocking the other
_ocr_executor = ThreadPoolExecutor(max_workers=settings.ocr_max_workers)
_llm_executor = ThreadPoolExecutor(max_workers=settings.llm_max_workers)
logger.info(f"Initialized thread pools: OCR={settings.ocr_max_workers}, LLM={settings.llm_max_workers}")

# 重新解析前要清空的识别字段
COMPARABLE_FIELDS = [
    'invoice_code',
    'invoice_number',
    'issue_date',
    'buyer_name',
    'buyer_tax_id',
    'seller_name',
    'seller_tax_id',
    'item_name',
    'total_with_tax',
    'amount',
    'tax_amount',
    'tax_rate',
]


def _run_qwen_ocr(file_data: bytes, file_type: str, invoice_id: int,
                  file_name: str) -> Dict[str, Any]:
    """v6 的 OCR 线路：qwen3.5-ocr 结构化抽取。失败不抛，返回空结果。"""
    svc = get_qwen_ocr_service()
    suffix = ".pdf" if (file_type or "").lower() == "pdf" else f".{file_type or 'jpg'}"
    res = svc.extract(file_data, suffix=suffix)
    kv = res.get("kv_result") or {}
    trusted = {k: kv.get(k) for k in TRUSTED_KEYS}
    logger.info(
        "Qwen OCR [invoice=%s file=%s]: %.1fs tokens=%s 采信字段=%s error=%s",
        invoice_id, file_name, res.get("elapsed_s", 0),
        res.get("usage", {}).get("total_tokens", "?"), trusted, res.get("error"),
    )
    return res


def _reset_extracted_fields(invoice: Invoice) -> None:
    """Reset extracted fields to avoid stale values on reprocess."""
    for field_name in COMPARABLE_FIELDS:
        setattr(invoice, field_name, None)


# v6 已移除 _run_ocr（本地 PaddleOCR 线路），改用 _run_qwen_ocr。


def _run_llm_vision(
    file_data: bytes,
    file_type: str,
    invoice_id: Optional[int] = None,
    file_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run LLM vision parsing in a separate thread.

    Args:
        file_data: Raw file bytes
        file_type: File type (pdf, jpg, png, etc.)
        invoice_id: Invoice id, used only to label LLM logs and dump files
        file_name: Uploaded file name, same purpose

    Returns:
        Dictionary of extracted fields
    """
    ctx = LLMCallContext(
        invoice_id=invoice_id, file_name=file_name, file_type=file_type
    )
    llm_service = get_llm_service()

    if not llm_service.is_available or not llm_service.supports_vision():
        return {}

    # Determine MIME type
    mime_map = {
        'pdf': 'application/pdf',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
    }
    mime_type = mime_map.get(file_type, 'image/png')

    # For PDF, we need to convert first page to image
    if file_type == 'pdf':
        try:
            from pdf2image import convert_from_bytes
            from io import BytesIO

            # Use higher DPI (300) for better text recognition in PDFs
            # Some PDFs have text rendered as graphics which need higher resolution
            images = convert_from_bytes(file_data, dpi=300, first_page=1, last_page=1)
            if images:
                # Convert PIL Image to bytes with high quality
                buffer = BytesIO()
                images[0].save(buffer, format='PNG', optimize=False)
                file_data = buffer.getvalue()
                mime_type = 'image/png'
                logger.info(f"PDF converted to image: {images[0].size[0]}x{images[0].size[1]} pixels, {len(file_data)} bytes")
            else:
                logger.warning("Failed to convert PDF to image for LLM vision")
                return {}
        except Exception as e:
            logger.error(f"PDF to image conversion failed: {e}")
            return {}

    return llm_service.parse_invoice_from_image(file_data, mime_type, ctx)


def _has_meaningful_fields(fields: Dict[str, Any]) -> bool:
    """Check if parsed fields contain any meaningful values."""
    if not fields:
        return False
    for value in fields.values():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


async def process_invoice(invoice_id: int, db: AsyncSession) -> bool:
    """Process an invoice: run OCR and LLM vision in parallel, then compare results.

    This function runs OCR and LLM vision parsing in parallel for better performance
    and more accurate results. The LLM analyzes the image directly instead of
    relying on OCR text.

    Args:
        invoice_id: ID of the invoice to process
        db: Database session

    Returns:
        True if processing succeeded, False otherwise
    """
    try:
        # Get invoice
        query = select(Invoice).where(Invoice.id == invoice_id)
        result = await db.execute(query)
        invoice = result.scalar_one_or_none()

        if not invoice:
            logger.error(f"Invoice {invoice_id} not found")
            return False

        # Delete existing OCR, LLM, and diff results for reprocessing
        await db.execute(delete(ParsingDiff).where(ParsingDiff.invoice_id == invoice_id))
        await db.execute(delete(LlmResult).where(LlmResult.invoice_id == invoice_id))
        await db.execute(delete(OcrResult).where(OcrResult.invoice_id == invoice_id))
        logger.info(f"Cleared existing processing results for invoice {invoice_id}")

        # Run OCR and LLM vision in PARALLEL using separate thread pools
        loop = asyncio.get_running_loop()

        # Create tasks for parallel execution
        # OCR uses CPU-bound pool, LLM uses I/O-bound pool
        ocr_task = loop.run_in_executor(
            _ocr_executor, _run_qwen_ocr, invoice.file_data, invoice.file_type,
            invoice_id, invoice.file_name,
        )
        llm_task = loop.run_in_executor(
            _llm_executor,
            _run_llm_vision,
            invoice.file_data,
            invoice.file_type,
            invoice_id,
            invoice.file_name,
        )

        # Wait for both tasks to complete
        logger.info(f"Running Qwen OCR and LLM vision in parallel for invoice {invoice_id}")
        ocr_result_data, llm_fields = await asyncio.gather(ocr_task, llm_task)

        ocr_kv = (ocr_result_data or {}).get("kv_result") or {}
        ocr_fields = {en: ocr_kv.get(cn) for cn, en in OCR_KEY_MAP.items()}
        has_llm = _has_meaningful_fields(llm_fields)

        logger.info(f"Qwen OCR completed: {sum(1 for v in ocr_kv.values() if v)} fields returned")
        logger.info(f"LLM vision completed: {len(llm_fields)} fields extracted (has_llm={has_llm})")

        # Save OCR result
        ocr_result = OcrResult(
            invoice_id=invoice_id,
            raw_text=json.dumps(ocr_kv, ensure_ascii=False) if ocr_kv else None,
            invoice_code=ocr_fields.get('invoice_code'),
            invoice_number=ocr_fields.get('invoice_number'),
            issue_date=ocr_fields.get('issue_date'),
            buyer_name=ocr_fields.get('buyer_name'),
            buyer_tax_id=ocr_fields.get('buyer_tax_id'),
            seller_name=ocr_fields.get('seller_name'),
            seller_tax_id=ocr_fields.get('seller_tax_id'),
            item_name=ocr_fields.get('item_name'),
            total_with_tax=ocr_fields.get('total_with_tax'),
            amount=ocr_fields.get('amount'),
            tax_amount=ocr_fields.get('tax_amount'),
            tax_rate=ocr_fields.get('tax_rate'),
        )
        db.add(ocr_result)

        # Save LLM result if available
        if has_llm:
            llm_result = LlmResult(
                invoice_id=invoice_id,
                invoice_code=llm_fields.get('invoice_code'),
                invoice_number=llm_fields.get('invoice_number'),
                issue_date=llm_fields.get('issue_date'),
                buyer_name=llm_fields.get('buyer_name'),
                buyer_tax_id=llm_fields.get('buyer_tax_id'),
                seller_name=llm_fields.get('seller_name'),
                seller_tax_id=llm_fields.get('seller_tax_id'),
                item_name=llm_fields.get('item_name'),
                total_with_tax=llm_fields.get('total_with_tax'),
                amount=llm_fields.get('amount'),
                tax_amount=llm_fields.get('tax_amount'),
                tax_rate=llm_fields.get('tax_rate'),
            )
            db.add(llm_result)
        else:
            logger.info(f"LLM vision not available - invoice {invoice_id} using OCR-only flow")

        # v6 融合：三路（当前二维码未启用，实为两路）逐字段选源。
        # 传给 fuse() 的必须是 VLM 的原始响应（含 buyer_entity_id），
        # 而不是 llm_service 已经查表回填过的 fields。
        vlm_raw = llm_fields.get("_raw") or llm_fields
        cfg = app_settings.current()
        fusion = fuse(qr=None, ocr=ocr_kv, vlm=vlm_raw,
                      entities=cfg.entities, rules=cfg.amount_rules)
        final_fields = fusion.values
        logger.info(
            "Fusion [invoice=%s]: verdict=%s low=%s failed=%s degraded=%s",
            invoice_id, fusion.verdict, fusion.low_confidence,
            fusion.failed_rules, fusion.degraded,
        )

        # Clear extracted fields so missing values don't keep stale data
        _reset_extracted_fields(invoice)

        # 每字段的融合明细写回 ParsingDiff，前端据此做三路对比。
        # 这里复用现有表结构：ocr_value / llm_value 存各路原始值，
        # source 存融合后的来源（ocr / vlm / lookup / computed / qr）。
        for fname, fr in fusion.fields.items():
            db.add(ParsingDiff(invoice_id=invoice_id, **fusion_row_values(fname, fr)))

        # Update invoice with final data
        _update_invoice_from_fields(invoice, final_fields)

        # v6：审核状态直接由融合层的 verdict 决定。
        # rejected 是 seller_is_entity 命中（销售方填成了本公司），必须人工；
        # warn 包含“值存疑”和“来源单一”两种，当前合并处理。
        needs_review = fusion.verdict != 'ok'
        invoice.status = (
            InvoiceStatus.REVIEWING if needs_review else InvoiceStatus.CONFIRMED
        )
        invoice.verdict = fusion.verdict
        invoice.checks = [c.as_dict() for c in fusion.checks]
        invoice.degraded = list(fusion.degraded)
        invoice.nulled = list(fusion.nulled)
        invoice.pipeline_version = PIPELINE_VERSION
        if fusion.nulled:
            logger.warning(
                f"Invoice {invoice_id} 被拒绝，已置空字段: {fusion.nulled}"
            )

        await db.commit()
        logger.info(f"Invoice {invoice_id} processed successfully (needs_review={needs_review})")
        return True

    except Exception as e:
        logger.error(f"Failed to process invoice {invoice_id}: {e}")
        await db.rollback()
        return False


# v6 已移除 _compare_and_resolve（两路仲裁），改用 fusion.fuse() 三路融合。


def _update_invoice_from_fields(invoice: Invoice, fields: dict) -> None:
    """Update invoice fields from extracted data."""
    from datetime import datetime
    from decimal import Decimal

    # 列宽 20。超长的代码一定是识别错了，跳过而不是让整次入库因为 flush 失败而回滚
    if fields.get('invoice_code') and len(fields['invoice_code']) <= 20:
        invoice.invoice_code = fields['invoice_code']

    if fields.get('invoice_number'):
        invoice.invoice_number = fields['invoice_number']

    if fields.get('issue_date'):
        try:
            invoice.issue_date = datetime.strptime(fields['issue_date'], '%Y-%m-%d').date()
        except ValueError:
            pass

    if fields.get('buyer_name'):
        invoice.buyer_name = fields['buyer_name']

    if fields.get('buyer_tax_id'):
        invoice.buyer_tax_id = fields['buyer_tax_id']

    if fields.get('seller_name'):
        invoice.seller_name = fields['seller_name']

    if fields.get('seller_tax_id'):
        invoice.seller_tax_id = fields['seller_tax_id']

    if fields.get('item_name'):
        invoice.item_name = fields['item_name']

    if fields.get('total_with_tax'):
        try:
            invoice.total_with_tax = Decimal(fields['total_with_tax'])
        except (ValueError, TypeError):
            pass

    if fields.get('amount'):
        try:
            invoice.amount = Decimal(fields['amount'])
        except (ValueError, TypeError):
            pass

    if fields.get('tax_amount'):
        try:
            invoice.tax_amount = Decimal(fields['tax_amount'])
        except (ValueError, TypeError):
            pass

    if fields.get('tax_rate'):
        invoice.tax_rate = fields['tax_rate']
