from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Numeric,
    Text, LargeBinary, ForeignKey, JSON, Enum as SQLEnum
)
from sqlalchemy.orm import relationship

from app.database import Base

# Import AuditLog / AppSetting to ensure they're registered with Base.metadata
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.app_setting import AppSetting  # noqa: F401


class InvoiceStatus(str, Enum):
    UPLOADED = "已上传"      # File uploaded, waiting for OCR processing
    PROCESSING = "解析中"    # OCR/LLM processing in progress
    PENDING = "待处理"       # Processing complete, no conflicts (legacy, kept for compatibility)
    REVIEWING = "待审核"     # Has conflicts or missing fields, needs manual review
    CONFIRMED = "已确认"     # Manually reviewed and confirmed
    REIMBURSED = "已报销"    # Reimbursement completed
    NOT_REIMBURSED = "未报销" # Not yet reimbursed


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)

    # File storage
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(20), nullable=False)  # pdf, jpg, png
    file_data = Column(LargeBinary, nullable=False)

    # Required fields (NOT NULL)
    # 发票代码：专票 10-12 位。v6 融合层产出该字段（二维码优先，降级到视觉模型）。
    invoice_code = Column(String(20), nullable=True)
    invoice_number = Column(String(50), nullable=True)  # 发票号码
    issue_date = Column(Date, nullable=True)  # 开票日期
    buyer_name = Column(String(255), nullable=True)  # 购买方名称
    buyer_tax_id = Column(String(50), nullable=True)  # 购买方纳税人识别号
    seller_name = Column(String(255), nullable=True)  # 销售方名称
    seller_tax_id = Column(String(50), nullable=True)  # 销售方纳税人识别号
    item_name = Column(String(500), nullable=True)  # 项目名称/货物名称
    total_with_tax = Column(Numeric(12, 2), nullable=True)  # 价税合计金额

    # Optional fields (can be NULL)
    specification = Column(String(255), nullable=True)  # 规格型号
    unit = Column(String(50), nullable=True)  # 单位
    quantity = Column(Numeric(12, 4), nullable=True)  # 数量
    unit_price = Column(Numeric(12, 4), nullable=True)  # 单价
    amount = Column(Numeric(12, 2), nullable=True)  # 金额(不含税)
    tax_rate = Column(String(20), nullable=True)  # 税率
    tax_amount = Column(Numeric(12, 2), nullable=True)  # 税额

    # Status management
    status = Column(SQLEnum(InvoiceStatus), default=InvoiceStatus.PENDING, nullable=False)
    owner = Column(String(100), nullable=True)  # 归属人

    # --- v6 融合管线的产物 ---
    # verdict 由融合层给出：ok / warn / rejected。
    # rejected 表示 seller_is_entity 命中（销售方被填成了本公司），销售方已置空。
    verdict = Column(String(20), nullable=True)
    # checks 存五条校验规则的结果与 detail（detail 里带算式，前端可直接展示）
    checks = Column(JSON, nullable=True)
    # degraded 记哪些字段走了降级路径，例如二维码未启用时的代码/号码/日期/金额
    degraded = Column(JSON, nullable=True)
    # nulled 记被拒绝规则置空的字段，前端要显式提示“已拒绝”而不是留白
    nulled = Column(JSON, nullable=True)
    pipeline_version = Column(String(20), nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships (cascade delete to clean up related records)
    ocr_result = relationship("OcrResult", back_populates="invoice", uselist=False, cascade="all, delete-orphan")
    llm_result = relationship("LlmResult", back_populates="invoice", uselist=False, cascade="all, delete-orphan")
    parsing_diffs = relationship("ParsingDiff", back_populates="invoice", cascade="all, delete-orphan")


class OcrResult(Base):
    __tablename__ = "ocr_results"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, unique=True)

    # Parsed fields from OCR
    raw_text = Column(Text, nullable=True)  # 原始OCR文本
    invoice_code = Column(String(20), nullable=True)
    invoice_number = Column(String(50), nullable=True)
    issue_date = Column(String(50), nullable=True)
    buyer_name = Column(String(255), nullable=True)
    buyer_tax_id = Column(String(50), nullable=True)
    seller_name = Column(String(255), nullable=True)
    seller_tax_id = Column(String(50), nullable=True)
    item_name = Column(String(500), nullable=True)
    total_with_tax = Column(String(50), nullable=True)
    specification = Column(String(255), nullable=True)
    unit = Column(String(50), nullable=True)
    quantity = Column(String(50), nullable=True)
    unit_price = Column(String(50), nullable=True)
    amount = Column(String(50), nullable=True)
    tax_rate = Column(String(20), nullable=True)
    tax_amount = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    invoice = relationship("Invoice", back_populates="ocr_result")


class LlmResult(Base):
    __tablename__ = "llm_results"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, unique=True)

    # Parsed fields from LLM
    invoice_code = Column(String(20), nullable=True)
    invoice_number = Column(String(50), nullable=True)
    issue_date = Column(String(50), nullable=True)
    buyer_name = Column(String(255), nullable=True)
    buyer_tax_id = Column(String(50), nullable=True)
    seller_name = Column(String(255), nullable=True)
    seller_tax_id = Column(String(50), nullable=True)
    item_name = Column(String(500), nullable=True)
    total_with_tax = Column(String(50), nullable=True)
    specification = Column(String(255), nullable=True)
    unit = Column(String(50), nullable=True)
    quantity = Column(String(50), nullable=True)
    unit_price = Column(String(50), nullable=True)
    amount = Column(String(50), nullable=True)
    tax_rate = Column(String(20), nullable=True)
    tax_amount = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    invoice = relationship("Invoice", back_populates="llm_result")


class ParsingDiff(Base):
    __tablename__ = "parsing_diffs"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)

    field_name = Column(String(100), nullable=False)  # 字段名
    # v6：三路各自的原始值。qr_value 目前恒为空 —— 二维码线路默认不跑，
    # 由结果页的“用二维码重试”手动触发。
    qr_value = Column(Text, nullable=True)
    ocr_value = Column(Text, nullable=True)  # qwen3.5-ocr 原始值
    llm_value = Column(Text, nullable=True)  # qwen-vl-max 原始值
    final_value = Column(Text, nullable=True)  # 融合后的值
    # source: qr / ocr / vlm / lookup / computed —— 后两者不来自任何模型
    source = Column(String(20), nullable=True)
    # confidence: certain / high / low / missing
    confidence = Column(String(20), nullable=True)
    note = Column(String(255), nullable=True)  # 选源理由，如“多路不一致，取优先线”
    resolved = Column(Integer, default=0)  # 0=需复核, 1=已确认

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    invoice = relationship("Invoice", back_populates="parsing_diffs")
