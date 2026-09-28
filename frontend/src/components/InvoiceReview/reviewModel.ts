/**
 * 发票核对页的视图模型：把后端的识别明细（来源、置信、各路候选值、规则结果）
 * 翻译成用户能直接行动的状态和文案。
 *
 * 原则
 * - 不向用户解释识别过程（几路识别、是否一致、置信高低），需要人看的一律只说「需要复核」；
 *   可信的依据只在悬浮提示里用业务语言说明，例如「与本公司主体信息一致」「金额勾稽校验通过」。
 * - 只用结构化字段判定状态，不解析 note 文本。
 * - 所有用户可见的文案都集中在这个文件里。
 */
import type { InvoiceDetail, InvoiceStatus, ParsingDiff, RuleCheck } from '../../types/invoice';
import { diffAgainst, type DiffSegment } from './charDiff';
import { formatAmount } from './format';

// --------------------------------------------------------------------------
// 字段定义：按增值税专票的版式自上而下排列
// --------------------------------------------------------------------------
export type GroupKey = 'header' | 'buyer' | 'goods' | 'seller' | 'other';
export type InputKind = 'text' | 'id' | 'date' | 'amount' | 'rate';

export interface FieldDef {
  key: string;
  /** 分组内的短标签 */
  label: string;
  /** 脱离分组时使用的完整标签，例如在待确认清单里 */
  fullLabel: string;
  group: GroupKey;
  input: InputKind;
  /** 与后端确认发票时的必填字段一致 */
  required: boolean;
}

export const GROUPS: { key: GroupKey; title: string }[] = [
  { key: 'header', title: '票面信息' },
  { key: 'buyer', title: '购买方' },
  { key: 'goods', title: '货物与金额' },
  { key: 'seller', title: '销售方' },
  { key: 'other', title: '其他' },
];

export const FIELD_DEFS: FieldDef[] = [
  { key: 'invoice_code', label: '发票代码', fullLabel: '发票代码', group: 'header', input: 'id', required: false },
  { key: 'invoice_number', label: '发票号码', fullLabel: '发票号码', group: 'header', input: 'id', required: true },
  { key: 'issue_date', label: '开票日期', fullLabel: '开票日期', group: 'header', input: 'date', required: true },
  { key: 'buyer_name', label: '名称', fullLabel: '购买方名称', group: 'buyer', input: 'text', required: true },
  { key: 'buyer_tax_id', label: '纳税人识别号', fullLabel: '购买方税号', group: 'buyer', input: 'id', required: true },
  { key: 'item_name', label: '项目名称', fullLabel: '项目名称', group: 'goods', input: 'text', required: true },
  { key: 'amount', label: '金额（不含税）', fullLabel: '金额', group: 'goods', input: 'amount', required: false },
  { key: 'tax_rate', label: '税率', fullLabel: '税率', group: 'goods', input: 'rate', required: false },
  { key: 'tax_amount', label: '税额', fullLabel: '税额', group: 'goods', input: 'amount', required: false },
  { key: 'total_with_tax', label: '价税合计', fullLabel: '价税合计', group: 'goods', input: 'amount', required: true },
  { key: 'seller_name', label: '名称', fullLabel: '销售方名称', group: 'seller', input: 'text', required: true },
  { key: 'seller_tax_id', label: '纳税人识别号', fullLabel: '销售方税号', group: 'seller', input: 'id', required: true },
];

const DEF_BY_KEY: Record<string, FieldDef> = Object.fromEntries(FIELD_DEFS.map((d) => [d.key, d]));

export function fieldLabel(key: string): string {
  return DEF_BY_KEY[key]?.fullLabel ?? key;
}

/** 金额组：金额、税率、税额、价税合计靠两个等式互相印证，由后端一起判定 */
const AMOUNT_GROUP = new Set(['amount', 'tax_rate', 'tax_amount', 'total_with_tax']);
const MONEY = new Set(['amount', 'tax_amount', 'total_with_tax']);
/** 金额组两式勾稽的规则名（computed_vs_recognized 是旧数据里的前身） */
const AMOUNT_RULES = new Set(['amount_consistency', 'computed_vs_recognized']);

/** 税率快捷选项 */
export const COMMON_RATES = ['13%', '9%', '6%', '3%', '1%', '0%', '免税'];

// 需要人看的字段，一律只说「需要复核」，不解释识别过程
const REVIEW = '需要复核';

// --------------------------------------------------------------------------
// 视图模型
// --------------------------------------------------------------------------
export type FieldStatus =
  | 'blocked'    // 被系统排除（销售方读成了本公司），必须人工填写
  | 'pending'    // 需要你确认
  | 'attention'  // 已确认过，但仍有规则提示
  | 'edited'     // 你修改过
  | 'auto'       // 已自动核验
  | 'confirmed'; // 你已确认

/** 待确认的具体情形。只决定卡片上给出哪些操作，不决定文案。 */
export type PendingKind =
  | 'lookup'            // 查表值与票面不一致：确认本公司主体
  | 'missing'           // 必填但没有值：填写
  | 'optional-missing'  // 非必填且没有值（如数电票没有发票代码）：可直接确认
  | 'conflict'          // 有多个候选值：选择
  | 'review';           // 其他：确认或修改

export interface Candidate {
  value: string;
  /** 用于展示的文本（金额加货币格式） */
  display: string;
  /** 等于当前采用值 */
  recommended: boolean;
  /** 与其他候选的差异分段，用于高亮 */
  segments: DiffSegment[];
  /** 被系统排除、不可选（等于本公司购买方信息） */
  excluded: boolean;
}

export interface FieldIssue {
  ruleId: string;
  message: string;
}

export interface ReviewField {
  key: string;
  diffId: number;
  label: string;
  fullLabel: string;
  group: GroupKey;
  input: InputKind;
  required: boolean;
  value: string | null;
  /** 展示用文本，空值为空串 */
  display: string;
  status: FieldStatus;
  pendingKind?: PendingKind;
  /** 需要处理时给用户看的一句话 */
  reason?: string;
  /** 为什么可信 / 为什么是现在这个值（悬浮提示） */
  basis?: string;
  /** 可选的候选值（有多个候选、被排除、查表不符时） */
  options: Candidate[];
  /** 仅作参考、不可选的票面内容 */
  evidence: string[];
  issues: FieldIssue[];
  /** 人工填写的金额组字段可以恢复自动推算 */
  canRestoreAuto: boolean;
  /** 该值是人改过的 */
  edited: boolean;
}

export type PillTone = 'pass' | 'warn' | 'fail' | 'muted';

export interface Pill {
  id: string;
  tone: PillTone;
  text: string;
  /** 点击后聚焦的字段 */
  focusKey?: string;
}

export type SummaryTone =
  | 'processing' | 'empty' | 'blocked' | 'pending' | 'hints' | 'done' | 'confirmed' | 'locked';

export interface ReviewModel {
  fields: ReviewField[];
  byKey: Record<string, ReviewField>;
  /** 待处理（待确认 + 被排除）字段，按版式顺序；Enter 在这些字段间跳转 */
  pendingKeys: string[];
  /** 已确认但仍有规则提示的字段 */
  hintKeys: string[];
  /** 还缺值的必填字段，确认发票前必须补上 */
  missingRequired: string[];
  /** 不能确认发票的原因（与后端确认接口的拦截口径一致）；可以确认时为 null */
  confirmBlocker: string | null;
  total: number;
  handled: number;
  recognized: number;
  autoVerified: number;
  tone: SummaryTone;
  headline: string;
  pills: Pill[];
  readOnly: boolean;
  processing: boolean;
  hasResults: boolean;
}

export const LOCKED_STATUSES: string[] = ['已报销', '未报销'];

// --------------------------------------------------------------------------
// 工具
// --------------------------------------------------------------------------
function isEmpty(v: string | null | undefined): v is null | undefined {
  return v === null || v === undefined || String(v).trim() === '';
}

/** 比较用的规约形式：金额按数值，其余去空白、转大写（与后端 clean() 的比较口径一致） */
function norm(key: string, v: string | null | undefined): string | null {
  if (isEmpty(v)) return null;
  const s = String(v).trim();
  if (MONEY.has(key)) {
    const n = Number(s.replace(/[,，¥￥\s]/g, ''));
    return Number.isFinite(n) ? n.toFixed(2) : s;
  }
  return s.replace(/\s+/g, '').toUpperCase();
}

function displayOf(key: string, v: string | null | undefined): string {
  if (isEmpty(v)) return '';
  return MONEY.has(key) ? formatAmount(v) : String(v);
}

/** 各路给出的读数（不含查表 / 推算），按二维码、OCR、视觉模型的顺序 */
function recognizedValues(d: ParsingDiff): string[] {
  return [d.qr_value, d.ocr_value, d.llm_value].filter((v): v is string => !isEmpty(v));
}

function distinctBy(key: string, values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of values) {
    const n = norm(key, v);
    if (n === null || seen.has(n)) continue;
    seen.add(n);
    out.push(v);
  }
  return out;
}

/** 规则结果涉及的字段。旧数据的 checks 没有 fields，按 detail 文本兜底。 */
function checkFields(c: RuleCheck): string[] {
  if (c.fields) return c.fields;
  const d = c.detail || '';
  switch (c.rule_id) {
    case 'tax_id_format':
      return ['buyer_tax_id', 'seller_tax_id'].filter((k) => d.includes(k));
    case 'invoice_number_format': {
      if (d.includes('相同')) return ['invoice_code', 'invoice_number'];
      const out: string[] = [];
      if (d.includes('发票代码')) out.push('invoice_code');
      if (d.includes('发票号码')) out.push('invoice_number');
      return out;
    }
    case 'computed_vs_recognized':
      return ['tax_amount', 'total_with_tax'].filter((k) => d.includes(k));
    case 'amount_consistency':
      return [...AMOUNT_GROUP];
    case 'seller_is_entity': {
      const out: string[] = [];
      if (d.includes('名称')) out.push('seller_name');
      if (d.includes('税号')) out.push('seller_tax_id');
      return out.length ? out : ['seller_name', 'seller_tax_id'];
    }
    case 'cross_source_mismatch':
      return FIELD_DEFS.map((f) => f.key).filter((k) => d.includes(`${k}:`));
    default:
      return [];
  }
}

// --------------------------------------------------------------------------
// 单字段
// --------------------------------------------------------------------------
function buildIssues(key: string, failed: RuleCheck[], nulled: string[]): FieldIssue[] {
  const issues: FieldIssue[] = [];
  for (const c of failed) {
    if (!checkFields(c).includes(key)) continue;
    if (c.rule_id === 'tax_id_format') {
      issues.push({ ruleId: c.rule_id, message: `${REVIEW}：税号格式有误` });
    } else if (c.rule_id === 'invoice_number_format') {
      issues.push({ ruleId: c.rule_id, message: `${REVIEW}：发票代码或号码格式有误` });
    } else if (AMOUNT_RULES.has(c.rule_id)) {
      issues.push({ ruleId: c.rule_id, message: `${REVIEW}：金额、税率、税额、价税合计对不上` });
    } else if (c.rule_id === 'seller_is_entity' && !nulled.includes(key)) {
      // 被系统清空的情况由 blocked 状态说明；这里是人填的值等于本公司
      issues.push({ ruleId: c.rule_id, message: `${REVIEW}：销售方不能是本公司` });
    }
  }
  return issues;
}

/** 至少两路读数与当前值一致 */
function agreesTwice(key: string, d: ParsingDiff): boolean {
  const target = norm(key, d.final_value);
  return target !== null && recognizedValues(d).filter((v) => norm(key, v) === target).length >= 2;
}

/** 自动核验的依据（悬浮提示）。null 表示不是自动核验的。 */
function autoBasis(key: string, d: ParsingDiff): string | null {
  if (d.confidence !== 'certain' && d.confidence !== 'high') return null;
  if (d.source === 'qr') return '从发票二维码读取';
  if (d.source === 'lookup') return '与本公司主体信息一致';
  if (d.source === 'computed') return '由其余金额自动推算';
  if (AMOUNT_GROUP.has(key)) return '金额勾稽校验通过';
  return agreesTwice(key, d) ? '已自动核验' : null;
}

function buildOptions(key: string, values: string[], current: string | null, excludedNorms: Set<string>): Candidate[] {
  const currentNorm = norm(key, current);
  const ordered = [...values].sort((a, b) => {
    const ra = norm(key, a) === currentNorm ? 0 : 1;
    const rb = norm(key, b) === currentNorm ? 0 : 1;
    return ra - rb;
  });
  return ordered.map((v, i) => {
    const others = ordered.filter((_, j) => j !== i);
    const reference = others.find((o) => norm(key, o) === currentNorm) ?? others[0];
    const display = displayOf(key, v);
    return {
      value: v,
      display,
      recommended: currentNorm !== null && norm(key, v) === currentNorm,
      segments: reference !== undefined ? diffAgainst(display, displayOf(key, reference)) : [{ text: display, changed: false }],
      excluded: excludedNorms.has(norm(key, v) ?? ''),
    };
  });
}

function buildField(
  def: FieldDef,
  d: ParsingDiff,
  ctx: { nulled: string[]; failed: RuleCheck[]; rowsByKey: Record<string, ParsingDiff> },
): ReviewField {
  const { key } = def;
  const value = isEmpty(d.final_value) ? null : d.final_value;
  const distinct = distinctBy(key, recognizedValues(d));
  const issues = buildIssues(key, ctx.failed, ctx.nulled);
  const edited = d.source === 'manual';

  const base: ReviewField = {
    key,
    diffId: d.id,
    label: def.label,
    fullLabel: def.fullLabel,
    group: def.group,
    input: def.input,
    required: def.required,
    value,
    display: displayOf(key, value),
    status: 'confirmed',
    options: [],
    evidence: [],
    issues,
    canRestoreAuto: AMOUNT_GROUP.has(key) && edited,
    edited,
  };

  // 被系统排除：销售方读成了本公司，已清空。等于本公司的候选不可选，其余仍可选
  if (ctx.nulled.includes(key) && value === null) {
    const buyerKey = key === 'seller_name' ? 'buyer_name' : 'buyer_tax_id';
    const buyerNorm = norm(key, ctx.rowsByKey[buyerKey]?.final_value);
    return {
      ...base,
      status: 'blocked',
      reason: `${REVIEW}：销售方不能是本公司`,
      options: buildOptions(key, distinct, null, new Set(buyerNorm ? [buyerNorm] : [])),
    };
  }

  // 还没人处理。卡片上只放一句话：有规则提示就用它（更具体），否则就是「需要复核」
  if (!d.resolved) {
    const pending = (kind: PendingKind, extra: Partial<ReviewField> = {}): ReviewField => ({
      ...base, status: 'pending', pendingKind: kind, reason: issues[0]?.message ?? REVIEW, ...extra,
    });

    if (d.source === 'lookup') {
      // 主选项是本公司主体；票面读到的不同写法只作参考，不作为可选项
      return pending('lookup', {
        options: value !== null ? buildOptions(key, [value], value, new Set()) : [],
        evidence: distinct.filter((v) => norm(key, v) !== norm(key, value)).map((v) => displayOf(key, v)),
      });
    }
    if (value === null) {
      return def.required
        ? pending('missing', { reason: `${REVIEW}，请补填` })
        : pending('optional-missing', { reason: `${REVIEW}，票面没有这一项可直接确认` });
    }
    if (distinct.length > 1) {
      return pending('conflict', { options: buildOptions(key, distinct, value, new Set()) });
    }
    return pending('review');
  }

  // 已处理
  const originals = distinct.filter((v) => norm(key, v) !== norm(key, value));
  const editedBasis = originals.length
    ? `你修改过 · 原值：${originals.map((v) => displayOf(key, v)).join(' / ')}`
    : '你修改过';
  if (issues.length > 0) return { ...base, status: 'attention', basis: edited ? editedBasis : undefined };
  if (edited) return { ...base, status: 'edited', basis: editedBasis };
  const auto = autoBasis(key, d);
  if (auto) return { ...base, status: 'auto', basis: auto };
  return { ...base, status: 'confirmed', basis: '你已确认' };
}

// --------------------------------------------------------------------------
// 摘要与校验药丸
// --------------------------------------------------------------------------
function buildPills(
  checks: RuleCheck[],
  byKey: Record<string, ReviewField>,
  rowsByKey: Record<string, ParsingDiff>,
): Pill[] {
  const pills: Pill[] = [];
  const check = (id: string) => checks.find((c) => c.rule_id === id);
  const firstField = (c: RuleCheck | undefined) => (c ? checkFields(c)[0] : undefined);

  // 购买方：与本公司主体
  const buyer = byKey.buyer_name;
  if (buyer?.status === 'pending' || buyer?.status === 'attention') {
    pills.push({ id: 'buyer', tone: 'warn', text: '购买方需要复核', focusKey: 'buyer_name' });
  } else if (rowsByKey.buyer_name?.source === 'lookup') {
    pills.push({ id: 'buyer', tone: 'pass', text: '购买方已匹配本公司主体' });
  }

  // 销售方不是本公司
  const seller = check('seller_is_entity');
  if (seller) {
    if (seller.status === 'pass') pills.push({ id: 'seller', tone: 'pass', text: '销售方不是本公司' });
    else if (seller.status === 'fail') pills.push({ id: 'seller', tone: 'fail', text: '销售方与本公司相同', focusKey: 'seller_name' });
  }

  // 金额组两式勾稽
  const amounts = checks.find((c) => AMOUNT_RULES.has(c.rule_id));
  if (amounts?.status === 'pass') {
    pills.push({ id: 'amounts', tone: 'pass', text: '金额勾稽一致' });
  } else if (amounts?.status === 'fail') {
    pills.push({ id: 'amounts', tone: 'warn', text: '金额需要复核', focusKey: firstField(amounts) ?? 'amount' });
  }

  const taxId = check('tax_id_format');
  if (taxId?.status === 'pass') pills.push({ id: 'taxid', tone: 'pass', text: '税号格式正确' });
  else if (taxId?.status === 'fail') pills.push({ id: 'taxid', tone: 'warn', text: '税号需要复核', focusKey: firstField(taxId) });

  const number = check('invoice_number_format');
  if (number?.status === 'pass') pills.push({ id: 'number', tone: 'pass', text: '代码与号码格式正确' });
  else if (number?.status === 'fail') pills.push({ id: 'number', tone: 'warn', text: '代码或号码需要复核', focusKey: firstField(number) });

  return pills;
}

function buildHeadline(m: {
  tone: SummaryTone; recognized: number; autoVerified: number; pending: number; hints: number;
  allAuto: boolean;
}): string {
  switch (m.tone) {
    case 'processing':
      return 'AI 正在识别发票…';
    case 'empty':
      return '还没有识别结果';
    case 'locked':
      return '发票已进入报销流程，信息不可修改';
    case 'blocked':
      return '销售方与本公司相同，请对照原件复核销售方';
    case 'pending':
      return `AI 已识别 ${m.recognized} 项信息，${m.autoVerified} 项已自动核验，还有 ${m.pending} 项需要复核`;
    case 'hints':
      return `全部核对完成，仍有 ${m.hints} 项需要复核`;
    case 'done':
      return '全部核对完成，可以确认发票了';
    case 'confirmed':
      return m.allAuto ? 'AI 已自动核验全部信息，发票已确认' : '发票已确认';
    default:
      return '';
  }
}

// --------------------------------------------------------------------------
// 入口
// --------------------------------------------------------------------------
export function buildReviewModel(
  invoice: InvoiceDetail,
  opts: { processing?: boolean } = {},
): ReviewModel {
  const status = invoice.status as InvoiceStatus | string;
  const rows = invoice.parsing_diffs ?? [];
  const rowsByKey: Record<string, ParsingDiff> = Object.fromEntries(rows.map((d) => [d.field_name, d]));
  const checks = invoice.checks ?? [];
  const failed = checks.filter((c) => c.status === 'fail');
  const ctx = { nulled: invoice.nulled ?? [], failed, rowsByKey };

  const fields = FIELD_DEFS
    .filter((def) => rowsByKey[def.key])
    .map((def) => buildField(def, rowsByKey[def.key], ctx));
  const byKey: Record<string, ReviewField> = Object.fromEntries(fields.map((f) => [f.key, f]));

  const pendingKeys = fields.filter((f) => f.status === 'pending' || f.status === 'blocked').map((f) => f.key);
  const hintKeys = fields.filter((f) => f.status === 'attention').map((f) => f.key);
  const missingRequired = FIELD_DEFS
    .filter((def) => def.required && rowsByKey[def.key] && isEmpty(rowsByKey[def.key].final_value))
    .map((def) => def.key);
  const recognized = fields.filter((f) => f.value !== null).length;
  const autoVerified = fields.filter((f) => f.status === 'auto').length;

  const readOnly = LOCKED_STATUSES.includes(status);
  const processing = Boolean(opts.processing) || status === '解析中';
  const hasResults = rows.length > 0;
  // 销售方等于本公司：被系统清空待填写，或人填成了本公司。后端确认接口会拦下，这里提前说明
  const sellerConflict = fields.some((f) => f.status === 'blocked')
    || failed.some((c) => c.rule_id === 'seller_is_entity');

  let confirmBlocker: string | null = null;
  if (missingRequired.length) confirmBlocker = `请先填写：${missingRequired.map(fieldLabel).join('、')}`;
  else if (sellerConflict) confirmBlocker = '销售方与本公司相同，请先复核销售方';

  let tone: SummaryTone;
  if (processing) tone = 'processing';
  else if (!hasResults) tone = 'empty';
  else if (readOnly) tone = 'locked';
  else if (status === '已确认' && pendingKeys.length === 0 && !sellerConflict) tone = 'confirmed';
  else if (sellerConflict) tone = 'blocked';
  else if (pendingKeys.length > 0) tone = 'pending';
  else if (hintKeys.length > 0) tone = 'hints';
  else tone = 'done';

  return {
    fields,
    byKey,
    pendingKeys,
    hintKeys,
    missingRequired,
    confirmBlocker,
    total: fields.length,
    handled: fields.length - pendingKeys.length,
    recognized,
    autoVerified,
    tone,
    headline: buildHeadline({
      tone,
      recognized,
      autoVerified,
      pending: pendingKeys.length,
      hints: hintKeys.length,
      allAuto: fields.length > 0 && autoVerified === fields.length,
    }),
    pills: hasResults ? buildPills(checks, byKey, rowsByKey) : [],
    readOnly,
    processing,
    hasResults,
  };
}
