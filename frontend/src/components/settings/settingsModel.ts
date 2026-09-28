/**
 * 设置页的纯逻辑：草稿校验、是否有改动、提交格式。
 *
 * 校验口径与后端 app/services/app_settings.py 一致：前端先拦一遍、就地提示，
 * 后端保存时仍会再校验一次（直接调接口也绕不过去）。
 */
import type { AmountRules, BuyerEntity, BuyerEntityInput } from '../../types/invoice';

export const MAX_ENTITIES = 50;
export const MAX_RATES = 20;

// ---------------------------------------------------------------------------
// 购买方主体清单
// ---------------------------------------------------------------------------
export interface EntityRow {
  /** 列表渲染用的 key；新增的行也有 */
  key: string;
  /** 已有主体的 id；新增的行没有，保存时由后端分配 */
  id?: string;
  name: string;
  tax_id: string;
}

export type EntityErrors = Record<string, { name?: string; tax_id?: string }>;

let rowSeq = 0;
export function newEntityRow(): EntityRow {
  rowSeq += 1;
  return { key: `new-${rowSeq}`, name: '', tax_id: '' };
}

export function toRows(entities: BuyerEntity[]): EntityRow[] {
  return entities.map((e) => ({ key: e.id, id: e.id, name: e.name, tax_id: e.tax_id }));
}

/** 税号规范化：去掉空格与连字符，转大写 */
export function normalizeTaxId(value: string): string {
  return value.replace(/[\s-]/g, '').toUpperCase();
}

/** 统一社会信用代码 18 位，旧的税务登记号 15 位 */
const TAX_ID = /^(?:[0-9A-Z]{15}|[0-9A-Z]{18})$/;

/** 逐行校验。重名、重复税号只标在后出现的那一行。 */
export function validateEntities(rows: EntityRow[]): EntityErrors {
  const errors: EntityErrors = {};
  const names = new Set<string>();
  const taxIds = new Set<string>();
  for (const row of rows) {
    const e: { name?: string; tax_id?: string } = {};
    const name = row.name.trim();
    const nameKey = name.replace(/\s+/g, '');
    const taxId = normalizeTaxId(row.tax_id);

    if (!name) e.name = '请填写名称';
    else if (name.length > 255) e.name = '名称过长，最多 255 个字符';
    else if (names.has(nameKey)) e.name = '与上面的主体重名';
    else names.add(nameKey);

    if (!taxId) e.tax_id = '请填写纳税人识别号';
    else if (!TAX_ID.test(taxId)) e.tax_id = '应为 15 或 18 位数字或大写字母';
    else if (taxIds.has(taxId)) e.tax_id = '与上面的主体税号相同';
    else taxIds.add(taxId);

    if (e.name || e.tax_id) errors[row.key] = e;
  }
  return errors;
}

/** 草稿与已保存的清单是否一致（按保存后的样子比：名称去首尾空格、税号规范化，顺序也算） */
export function entitiesEqual(rows: EntityRow[], saved: BuyerEntity[]): boolean {
  return rows.length === saved.length && rows.every((r, i) => (
    r.id === saved[i].id
    && r.name.trim() === saved[i].name
    && normalizeTaxId(r.tax_id) === saved[i].tax_id
  ));
}

export function toEntityPayload(rows: EntityRow[]): BuyerEntityInput[] {
  return rows.map((r) => ({
    ...(r.id ? { id: r.id } : {}),
    name: r.name.trim(),
    tax_id: normalizeTaxId(r.tax_id),
  }));
}

// ---------------------------------------------------------------------------
// 金额校验规则
// ---------------------------------------------------------------------------
export interface RulesDraft {
  add_tolerance: number | null;
  mul_tolerance: number | null;
  rounding_tolerance: number | null;
  /** 百分数，13 表示 13% */
  standard_rates: number[];
}

export type ThresholdKey = 'add_tolerance' | 'mul_tolerance' | 'rounding_tolerance';
export type RulesErrors = Partial<Record<keyof RulesDraft, string>>;

/** 各阈值的上限（元） */
export const THRESHOLD_MAX: Record<ThresholdKey, number> = {
  add_tolerance: 1,
  mul_tolerance: 1,
  rounding_tolerance: 5,
};

export function sortRates(rates: number[]): number[] {
  return [...rates].sort((a, b) => b - a);
}

export function toDraft(rules: AmountRules): RulesDraft {
  return { ...rules, standard_rates: sortRates(rules.standard_rates) };
}

export function validateRules(d: RulesDraft): RulesErrors {
  const errors: RulesErrors = {};
  for (const key of Object.keys(THRESHOLD_MAX) as ThresholdKey[]) {
    const v = d[key];
    if (v === null || Number.isNaN(v)) errors[key] = '请填写';
    else if (v < 0 || v > THRESHOLD_MAX[key]) errors[key] = `应在 0 到 ${THRESHOLD_MAX[key]} 元之间`;
    else if (Math.round(v * 100) / 100 !== v) errors[key] = '最多精确到分';
  }
  if (!errors.rounding_tolerance && !errors.mul_tolerance
    && (d.rounding_tolerance as number) < (d.mul_tolerance as number)) {
    errors.rounding_tolerance = '不能小于乘法校验阈值';
  }
  if (d.standard_rates.length === 0) errors.standard_rates = '至少保留一个税率';
  else if (d.standard_rates.length > MAX_RATES) errors.standard_rates = `最多 ${MAX_RATES} 个税率`;
  return errors;
}

export function rulesEqual(d: RulesDraft, saved: AmountRules): boolean {
  const a = sortRates(d.standard_rates);
  const b = sortRates(saved.standard_rates);
  return d.add_tolerance === saved.add_tolerance
    && d.mul_tolerance === saved.mul_tolerance
    && d.rounding_tolerance === saved.rounding_tolerance
    && a.length === b.length && a.every((r, i) => r === b[i]);
}

/** 解析新增税率的输入（「13」「13%」「1.5」都可以） */
export function parseRate(input: string, existing: number[]): { value?: number; error?: string } {
  const text = input.trim().replace(/[%％]$/, '').trim();
  if (!text) return { error: '请输入税率' };
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return { error: '税率格式不正确，例如 13 或 1.5' };
  const value = Number(text);
  if (!(value > 0 && value < 100)) return { error: '税率应大于 0% 且小于 100%' };
  if (existing.includes(value)) return { error: `${value}% 已在税率库中` };
  return { value };
}

export function formatRate(p: number): string {
  return `${p}%`;
}
