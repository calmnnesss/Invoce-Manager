export enum InvoiceStatus {
  UPLOADED = '已上传',      // File uploaded, waiting for OCR processing
  PROCESSING = '解析中',    // OCR/LLM processing in progress
  PENDING = '待处理',       // Processing complete, no conflicts
  REVIEWING = '待审核',     // Has conflicts or missing fields, needs manual review
  CONFIRMED = '已确认',     // Manually reviewed and confirmed
  REIMBURSED = '已报销',    // Reimbursement completed
  NOT_REIMBURSED = '未报销', // Not yet reimbursed
}

export interface Invoice {
  id: number;
  file_name: string;
  file_type: string;
  invoice_code: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  seller_name: string | null;
  seller_tax_id: string | null;
  item_name: string | null;
  total_with_tax: number | null;
  specification: string | null;
  unit: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount: number | null;
  tax_rate: string | null;
  tax_amount: number | null;
  status: InvoiceStatus;
  owner: string | null;

  // --- v6 融合管线的产物 ---
  verdict: Verdict | null;
  checks: RuleCheck[] | null;
  /** 走了降级路径的字段。二维码未启用时恒含代码/号码/日期/金额四项 */
  degraded: string[] | null;
  /** 被拒绝规则置空的字段，前端要显式提示而不是留白 */
  nulled: string[] | null;
  pipeline_version: string | null;

  created_at: string;
  updated_at: string;
}

/** 融合层的单据级判定 */
export type Verdict = 'ok' | 'warn' | 'rejected';

/** 字段值的来源。lookup 与 computed 不来自任何模型 */
export type FieldSource = 'qr' | 'ocr' | 'vlm' | 'lookup' | 'computed' | 'manual';

export type Confidence = 'certain' | 'high' | 'low' | 'missing';

export interface RuleCheck {
  rule_id: string;
  status: 'pass' | 'fail' | 'skipped';
  detail: string;
  /** 结果涉及的字段：失败时是出问题的字段，通过时是参与校验的字段。旧数据没有该键 */
  fields?: string[];
}

/** 详情页对单个字段的操作。选中某个候选值也是 set，值即该候选 */
export interface FieldUpdateBody {
  action: 'confirm' | 'set' | 'auto';
  value?: string | null;
}

export interface OcrResult {
  id: number;
  invoice_id: number;
  raw_text: string | null;
  invoice_code: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  seller_name: string | null;
  seller_tax_id: string | null;
  item_name: string | null;
  total_with_tax: string | null;
  specification: string | null;
  unit: string | null;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
  tax_rate: string | null;
  tax_amount: string | null;
  created_at: string;
}

export interface LlmResult {
  id: number;
  invoice_id: number;
  invoice_code: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  seller_name: string | null;
  seller_tax_id: string | null;
  item_name: string | null;
  total_with_tax: string | null;
  specification: string | null;
  unit: string | null;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
  tax_rate: string | null;
  tax_amount: string | null;
  created_at: string;
}

export interface ParsingDiff {
  id: number;
  invoice_id: number;
  field_name: string;
  /** 二维码原始值。当前线路未启用，恒为 null，由结果页手动重试填充 */
  qr_value: string | null;
  /** qwen3.5-ocr 原始值 */
  ocr_value: string | null;
  /** qwen-vl-max 原始值 */
  llm_value: string | null;
  final_value: string | null;
  source: FieldSource | null;
  confidence: Confidence | null;
  /** 选源理由，如“多路不一致，取优先线” */
  note: string | null;
  resolved: number;
}

export interface QrRetryResponse {
  invoice_id: number;
  decoded: boolean;
  fields: Record<string, string>;
  message: string;
}

export interface InvoiceDetail extends Invoice {
  ocr_result: OcrResult | null;
  llm_result: LlmResult | null;
  parsing_diffs: ParsingDiff[];
}

export interface InvoiceListResponse {
  items: Invoice[];
  total: number;
  page: number;
  page_size: number;
}

export interface Statistics {
  count: number;
  total_amount: number;
  total_tax: number;
  total_with_tax: number;
}

export interface UploadResponse {
  id: number;
  file_name: string;
  status: string;
  message: string;
}

// LLM Configuration Types
export interface LLMProviderInfo {
  name: string;
  display_name: string;
  is_configured: boolean;
  model: string | null;
  base_url: string | null;
}

export interface LLMStatusResponse {
  is_configured: boolean;
  active_provider: string | null;
  active_provider_display: string | null;
  configured_providers: string[];
  available_providers: LLMProviderInfo[];
}

export interface LLMConfigRequest {
  provider: string;
  api_key: string;
  model?: string;
  base_url?: string;
}

export interface LLMConfigResponse {
  success: boolean;
  message: string;
  provider: string | null;
}

export interface LLMTestResponse {
  success: boolean;
  provider: string;
  provider_display: string;
  message: string;
  response: string;
}

// Model Registry Types
export interface ModelInfo {
  id: string;
  name: string;
  vision: boolean;
  context_length?: number;
  pricing?: {
    prompt?: string;
    completion?: string;
  };
}

export interface ModelsResponse {
  models: ModelInfo[];
  source: 'openrouter' | 'fallback';
}

// 业务配置（设置页）：购买方主体清单与金额校验规则
export interface BuyerEntity {
  id: string;
  name: string;
  tax_id: string;
}

/** 保存时的一行：已有主体带上原 id，新增的不带，由后端分配 */
export interface BuyerEntityInput {
  id?: string;
  name: string;
  tax_id: string;
}

export interface AmountRules {
  add_tolerance: number;
  mul_tolerance: number;
  rounding_tolerance: number;
  /** 百分数，13 表示 13% */
  standard_rates: number[];
}

export interface ReviewSettingsValues {
  buyer_entities: BuyerEntity[];
  amount_rules: AmountRules;
}

export interface ReviewSettings extends ReviewSettingsValues {
  /** 出厂值，「恢复默认」用 */
  defaults: ReviewSettingsValues;
}
