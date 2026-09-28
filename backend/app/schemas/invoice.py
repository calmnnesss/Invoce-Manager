from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class InvoiceStatus(str, Enum):
    UPLOADED = "已上传"      # File uploaded, waiting for OCR processing
    PROCESSING = "解析中"    # OCR/LLM processing in progress
    PENDING = "待处理"       # Processing complete, no conflicts
    REVIEWING = "待审核"     # Has conflicts or missing fields, needs manual review
    CONFIRMED = "已确认"     # Manually reviewed and confirmed
    REIMBURSED = "已报销"    # Reimbursement completed
    NOT_REIMBURSED = "未报销" # Not yet reimbursed


class InvoiceBase(BaseModel):
    invoice_code: Optional[str] = Field(None, description="发票代码")
    invoice_number: Optional[str] = Field(None, description="发票号码")
    issue_date: Optional[date] = Field(None, description="开票日期")
    buyer_name: Optional[str] = Field(None, description="购买方名称")
    buyer_tax_id: Optional[str] = Field(None, description="购买方纳税人识别号")
    seller_name: Optional[str] = Field(None, description="销售方名称")
    seller_tax_id: Optional[str] = Field(None, description="销售方纳税人识别号")
    item_name: Optional[str] = Field(None, description="项目名称")
    total_with_tax: Optional[Decimal] = Field(None, description="价税合计金额")
    specification: Optional[str] = Field(None, description="规格型号")
    unit: Optional[str] = Field(None, description="单位")
    quantity: Optional[Decimal] = Field(None, description="数量")
    unit_price: Optional[Decimal] = Field(None, description="单价")
    amount: Optional[Decimal] = Field(None, description="金额")
    tax_rate: Optional[str] = Field(None, description="税率")
    tax_amount: Optional[Decimal] = Field(None, description="税额")


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(InvoiceBase):
    status: Optional[InvoiceStatus] = None
    owner: Optional[str] = None


class InvoiceResponse(InvoiceBase):
    id: int
    file_name: str
    file_type: str
    status: InvoiceStatus
    owner: Optional[str] = None
    verdict: Optional[str] = Field(None, description="融合判定：ok / warn / rejected")
    checks: Optional[List[dict]] = Field(None, description="五条校验规则的结果")
    degraded: Optional[List[str]] = Field(None, description="走了降级路径的字段")
    nulled: Optional[List[str]] = Field(None, description="被拒绝规则置空的字段")
    pipeline_version: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InvoiceListResponse(BaseModel):
    items: List[InvoiceResponse]
    total: int
    page: int
    page_size: int


class OcrResultResponse(BaseModel):
    id: int
    invoice_id: int
    raw_text: Optional[str] = None
    invoice_code: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_tax_id: Optional[str] = None
    seller_name: Optional[str] = None
    seller_tax_id: Optional[str] = None
    item_name: Optional[str] = None
    total_with_tax: Optional[str] = None
    specification: Optional[str] = None
    unit: Optional[str] = None
    quantity: Optional[str] = None
    unit_price: Optional[str] = None
    amount: Optional[str] = None
    tax_rate: Optional[str] = None
    tax_amount: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LlmResultResponse(BaseModel):
    id: int
    invoice_id: int
    invoice_code: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_tax_id: Optional[str] = None
    seller_name: Optional[str] = None
    seller_tax_id: Optional[str] = None
    item_name: Optional[str] = None
    total_with_tax: Optional[str] = None
    specification: Optional[str] = None
    unit: Optional[str] = None
    quantity: Optional[str] = None
    unit_price: Optional[str] = None
    amount: Optional[str] = None
    tax_rate: Optional[str] = None
    tax_amount: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ParsingDiffResponse(BaseModel):
    id: int
    invoice_id: int
    field_name: str
    qr_value: Optional[str] = Field(None, description="二维码原始值（当前未启用，恒为空）")
    ocr_value: Optional[str] = Field(None, description="qwen3.5-ocr 原始值")
    llm_value: Optional[str] = Field(None, description="qwen-vl-max 原始值")
    final_value: Optional[str] = None
    source: Optional[str] = Field(None, description="qr / ocr / vlm / lookup / computed")
    confidence: Optional[str] = Field(None, description="certain / high / low / missing")
    note: Optional[str] = None
    resolved: int = 0

    class Config:
        from_attributes = True


class InvoiceDetailResponse(InvoiceResponse):
    ocr_result: Optional[OcrResultResponse] = None
    llm_result: Optional[LlmResultResponse] = None
    parsing_diffs: List[ParsingDiffResponse] = []


class BatchUpdateRequest(BaseModel):
    invoice_ids: List[int]
    status: Optional[InvoiceStatus] = None
    owner: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    invoice_ids: List[int] = Field(..., description="要删除的发票ID列表")


class StatisticsResponse(BaseModel):
    count: int = Field(description="发票数量")
    total_amount: Decimal = Field(description="金额合计")
    total_tax: Decimal = Field(description="税额合计")
    total_with_tax: Decimal = Field(description="价税合计")


class UploadResponse(BaseModel):
    id: int
    file_name: str
    status: str
    message: str


class FieldUpdateRequest(BaseModel):
    """详情页对单个字段的操作。选中某个候选值也是 set，值即该候选。"""
    action: Literal["confirm", "set", "auto"] = Field(
        ..., description="confirm 确认当前值；set 改为 value；auto 税额/价税合计恢复按公式计算"
    )
    value: Optional[str] = Field(None, description="action 为 set 时的新值")


class QrRetryResponse(BaseModel):
    """结果页“用二维码重试”的返回。

    二维码线路默认不跑：它在当前测试集上 51/51 解码失败（zbar 与 cv2
    两个解码器、四档缩放、灰度变体全试过），所以做成手动触发而不是默认链路。
    """
    invoice_id: int
    decoded: bool = Field(description="是否解码成功")
    fields: dict = Field(default_factory=dict, description="解出的四个字段")
    message: str
