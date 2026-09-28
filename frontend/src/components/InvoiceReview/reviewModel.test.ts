import { describe, expect, it } from 'vitest';
import type { InvoiceDetail, ParsingDiff } from '../../types/invoice';
import { diffAgainst } from './charDiff';
import { formatAmount } from './format';
import { buildReviewModel, type ReviewModel } from './reviewModel';
import invoice3 from './__fixtures__/invoice3.json';

/** 库中发票 3（评测集 b10）在金额组新判定之前的真实接口返回（旧数据） */
function fixture(): InvoiceDetail {
  return JSON.parse(JSON.stringify(invoice3)) as InvoiceDetail;
}

function withRow(inv: InvoiceDetail, field: string, patch: Partial<ParsingDiff>): InvoiceDetail {
  inv.parsing_diffs = inv.parsing_diffs.map((d) => (d.field_name === field ? { ...d, ...patch } : d));
  return inv;
}

/** 同一张票按金额组新判定入库的样子：四项自洽，第一层放行 */
function newLogicFixture(): InvoiceDetail {
  const inv = fixture();
  withRow(inv, 'amount', { confidence: 'high', resolved: 1, note: '金额勾稽校验通过' });
  withRow(inv, 'tax_amount', { source: 'vlm', note: '金额勾稽校验通过' });
  withRow(inv, 'total_with_tax', { source: 'vlm', note: '金额勾稽校验通过' });
  inv.checks = inv.checks!.map((c) => (c.rule_id === 'computed_vs_recognized'
    ? { rule_id: 'amount_consistency', status: 'pass' as const, detail: '',
      fields: ['amount', 'tax_rate', 'tax_amount', 'total_with_tax'] }
    : c));
  return inv;
}

/** 用户能看到的所有文案 */
function visibleText(m: ReviewModel): string[] {
  const out = [m.headline, ...m.pills.map((p) => p.text)];
  for (const f of m.fields) {
    out.push(f.label, f.fullLabel, f.display, f.reason ?? '', f.basis ?? '');
    out.push(...f.evidence, ...f.options.map((o) => o.display), ...f.issues.map((i) => i.message));
  }
  return out;
}

// 引擎名、置信、识别过程这些不该让用户看到的词
const JARGON = /OCR|ocr|vlm|VLM|视觉模型|置信|管线|降级|lookup|computed|confidence|E0\d|两次识别|识别结果不一致|只识别到一次|交叉核验|识别为/;

describe('金额组新判定：四项自洽的票，金额不再需要人看', () => {
  const m = buildReviewModel(newLogicFixture());

  it('待复核只剩项目名称与销售方名称', () => {
    expect(m.pendingKeys).toEqual(['item_name', 'seller_name']);
    expect(m.headline).toBe('AI 已识别 12 项信息，10 项已自动核验，还有 2 项需要复核');
  });

  it('金额组四项显示为已自动核验，依据是金额勾稽', () => {
    for (const k of ['amount', 'tax_rate', 'tax_amount', 'total_with_tax']) {
      expect(m.byKey[k].status).toBe('auto');
      expect(m.byKey[k].basis).toBe('金额勾稽校验通过');
    }
    expect(m.pills.map((p) => p.text)).toContain('金额勾稽一致');
  });

  it('推导补全的值说明是自动推算的', () => {
    const inv = withRow(newLogicFixture(), 'tax_rate', { source: 'computed', final_value: '17%' });
    expect(buildReviewModel(inv).byKey.tax_rate.basis).toBe('由其余金额自动推算');
  });

  it('金额组对不上：涉及的字段都标为需要复核，只说「需要复核」', () => {
    const inv = newLogicFixture();
    for (const k of ['amount', 'tax_rate', 'tax_amount', 'total_with_tax']) {
      withRow(inv, k, { confidence: 'low', resolved: 0 });
    }
    inv.checks = inv.checks!.map((c) => (c.rule_id === 'amount_consistency'
      ? { ...c, status: 'fail' as const, detail: '金额 + 税额 与价税合计相差 1.00 元' }
      : c));
    const m2 = buildReviewModel(inv);
    expect(m2.pendingKeys).toEqual(['item_name', 'amount', 'tax_rate', 'tax_amount', 'total_with_tax', 'seller_name']);
    expect(m2.byKey.amount.reason).toBe('需要复核：金额、税率、税额、价税合计对不上');
    expect(m2.byKey.item_name.reason).toBe('需要复核');
    expect(m2.pills.find((p) => p.id === 'amounts')?.text).toBe('金额需要复核');
  });

  it('人把金额组都确认了、整组仍对不上：仍然提示，不会显示「全部核对完成，可以确认」', () => {
    const inv = newLogicFixture();
    withRow(inv, 'amount', { source: 'manual', final_value: '3000.00' });
    for (const k of ['tax_rate', 'tax_amount', 'total_with_tax']) withRow(inv, k, { confidence: 'low', resolved: 1 });
    withRow(inv, 'item_name', { resolved: 1 });
    withRow(inv, 'seller_name', { resolved: 1 });
    inv.checks = inv.checks!.map((c) => (c.rule_id === 'amount_consistency'
      ? { ...c, status: 'fail' as const, fields: ['amount', 'tax_rate', 'tax_amount', 'total_with_tax'] }
      : c));
    const m2 = buildReviewModel(inv);
    expect(m2.pendingKeys).toEqual([]);
    expect(m2.hintKeys).toEqual(['amount', 'tax_rate', 'tax_amount', 'total_with_tax']);
    expect(m2.headline).toBe('全部核对完成，仍有 4 项需要复核');
  });
});

describe('旧数据（发票 3 改版前的接口返回）照样能用', () => {
  const m = buildReviewModel(fixture());

  it('待复核的是 项目名称、金额、销售方名称，按票面版式排序', () => {
    expect(m.pendingKeys).toEqual(['item_name', 'amount', 'seller_name']);
    expect(m.byKey.seller_name.pendingKind).toBe('conflict');
    expect(m.byKey.amount.pendingKind).toBe('review');
    expect(m.headline).toBe('AI 已识别 12 项信息，9 项已自动核验，还有 3 项需要复核');
  });

  it('需要人看的一律只说「需要复核」，不解释识别过程', () => {
    for (const k of m.pendingKeys) expect(m.byKey[k].reason).toBe('需要复核');
  });

  it('不一致的销售方给出两个选项，当前采用值排第一并高亮差异', () => {
    const [first, second] = m.byKey.seller_name.options;
    expect(first.value).toBe('广州晶东贸易有限公司');
    expect(first.recommended).toBe(true);
    expect(second.value).toBe('广州品乐贸易有限公司');
    expect(first.segments.filter((s) => s.changed).map((s) => s.text)).toEqual(['晶东']);
  });

  it('可信字段只在提示里给出业务依据', () => {
    expect(m.byKey.buyer_name.basis).toBe('与本公司主体信息一致');
    expect(m.byKey.invoice_code.basis).toBe('已自动核验');
    expect(m.byKey.tax_amount.basis).toBe('由其余金额自动推算');
  });

  it('页面文案里不出现引擎名、置信、识别过程这些技术细节', () => {
    for (const model of [m, buildReviewModel(newLogicFixture())]) {
      for (const text of visibleText(model)) expect(text).not.toMatch(JARGON);
    }
  });
});

describe('状态判定的边界情况', () => {
  it('查表值与票面不一致：只让人确认本公司主体，票面写法仅作参考、不可选', () => {
    const inv = withRow(fixture(), 'buyer_name', {
      confidence: 'low', resolved: 0, ocr_value: '深圳市购机汇网络科技有限公司',
    });
    const f = buildReviewModel(inv).byKey.buyer_name;
    expect(f.pendingKind).toBe('lookup');
    expect(f.reason).toBe('需要复核');
    expect(f.options.map((o) => o.value)).toEqual(['深圳市购机汇网络有限公司']);
    expect(f.evidence).toEqual(['深圳市购机汇网络科技有限公司']);
  });

  it('已核验但规则失败的字段标为需要复核，不进待确认队列', () => {
    const inv = newLogicFixture();
    inv.checks = inv.checks!.map((c) => (c.rule_id === 'tax_id_format'
      ? { ...c, status: 'fail' as const, detail: 'seller_tax_id 位数为 16，应为 15 或 18', fields: ['seller_tax_id'] }
      : c));
    const m = buildReviewModel(inv);
    expect(m.byKey.seller_tax_id.status).toBe('attention');
    expect(m.byKey.seller_tax_id.issues[0].message).toBe('需要复核：税号格式有误');
    expect(m.pendingKeys).not.toContain('seller_tax_id');
    expect(m.hintKeys).toEqual(['seller_tax_id']);
  });

  it('旧数据的规则结果没有 fields 时按 detail 兜底落到字段', () => {
    const inv = fixture();
    inv.checks = [{ rule_id: 'tax_id_format', status: 'fail', detail: 'seller_tax_id 位数为 16，应为 15 或 18' }];
    const m = buildReviewModel(inv);
    expect(m.byKey.seller_tax_id.status).toBe('attention');
    expect(m.byKey.buyer_tax_id.status).toBe('auto');
  });

  it('非必填字段没识别到（数电票没有发票代码）：可直接确认，不算缺必填', () => {
    const inv = withRow(fixture(), 'invoice_code', {
      final_value: null, ocr_value: null, llm_value: null, confidence: 'missing', resolved: 0,
    });
    const m = buildReviewModel(inv);
    expect(m.byKey.invoice_code.pendingKind).toBe('optional-missing');
    expect(m.missingRequired).toEqual([]);
  });

  it('销售方被系统排除：等于本公司的候选不可选，其余候选仍可选', () => {
    const inv = fixture();
    inv.nulled = ['seller_name', 'seller_tax_id'];
    withRow(inv, 'seller_name', {
      final_value: null, confidence: 'missing', resolved: 0,
      ocr_value: '深圳市购机汇网络有限公司', llm_value: '广州晶东贸易有限公司',
    });
    const m = buildReviewModel(inv);
    const f = m.byKey.seller_name;
    expect(f.status).toBe('blocked');
    expect(f.reason).toBe('需要复核：销售方不能是本公司');
    expect(f.options.find((o) => o.value === '深圳市购机汇网络有限公司')?.excluded).toBe(true);
    expect(f.options.find((o) => o.value === '广州晶东贸易有限公司')?.excluded).toBe(false);
    expect(m.tone).toBe('blocked');
    expect(m.missingRequired).toContain('seller_name');
  });

  it('人把销售方选成了本公司：提示并拦下确认（与后端确认接口同口径），值原样保留', () => {
    const inv = withRow(fixture(), 'seller_name', { final_value: '东莞市购机汇网络有限公司', resolved: 1 });
    inv.checks = [{
      rule_id: 'seller_is_entity', status: 'fail',
      detail: '名称命中清单: 东莞市购机汇网络有限公司', fields: ['seller_name'],
    }];
    const m = buildReviewModel(inv);
    expect(m.byKey.seller_name.status).toBe('attention');
    expect(m.byKey.seller_name.value).toBe('东莞市购机汇网络有限公司');
    expect(m.tone).toBe('blocked');
    expect(m.confirmBlocker).toBe('销售方与本公司相同，请先复核销售方');
  });

  it('缺必填项时拦下确认并说明缺什么', () => {
    const inv = withRow(fixture(), 'item_name', { final_value: null, llm_value: null, confidence: 'missing' });
    expect(buildReviewModel(inv).confirmBlocker).toBe('请先填写：项目名称');
  });

  it('人确认过的低置信字段显示「你已确认」，改过的显示原值', () => {
    const inv = withRow(fixture(), 'item_name', { resolved: 1 });
    withRow(inv, 'seller_name', { resolved: 1, source: 'manual', final_value: '广州晶东贸易有限公司（改）' });
    const m = buildReviewModel(inv);
    expect(m.byKey.item_name.status).toBe('confirmed');
    expect(m.byKey.seller_name.status).toBe('edited');
    expect(m.byKey.seller_name.basis).toContain('广州品乐贸易有限公司');
  });

  it('人填写过的金额组字段可以恢复自动推算', () => {
    const inv = withRow(newLogicFixture(), 'tax_amount', { source: 'manual', final_value: '500.00' });
    const f = buildReviewModel(inv).byKey.tax_amount;
    expect(f.status).toBe('edited');
    expect(f.canRestoreAuto).toBe(true);
  });

  it('全部处理完：摘要改为可以确认；已确认后说明结果', () => {
    const inv = newLogicFixture();
    inv.parsing_diffs = inv.parsing_diffs.map((d) => ({ ...d, resolved: 1 }));
    inv.checks = inv.checks!.map((c) => ({ ...c, status: 'pass' as const }));
    expect(buildReviewModel(inv).headline).toBe('全部核对完成，可以确认发票了');
    inv.status = '已确认' as InvoiceDetail['status'];
    expect(buildReviewModel(inv).tone).toBe('confirmed');
  });

  it('已报销的发票只读', () => {
    const inv = fixture();
    inv.status = '已报销' as InvoiceDetail['status'];
    const m = buildReviewModel(inv);
    expect(m.readOnly).toBe(true);
    expect(m.tone).toBe('locked');
  });
});

describe('工具函数', () => {
  it('差异高亮只标出不同的字', () => {
    expect(diffAgainst('广州晶东贸易有限公司', '广州品乐贸易有限公司')).toEqual([
      { text: '广州', changed: false },
      { text: '晶东', changed: true },
      { text: '贸易有限公司', changed: false },
    ]);
  });

  it('两个值根本不像（识别成了另一家公司）时不做高亮，免得误导', () => {
    expect(diffAgainst('广州晶东贸易有限公司', '东莞市购机汇网络有限公司')).toEqual([
      { text: '广州晶东贸易有限公司', changed: false },
    ]);
    expect(diffAgainst('91440101664041243T', '441900083885933')).toEqual([
      { text: '91440101664041243T', changed: false },
    ]);
  });

  it('税号只错一位时精确标出那一位', () => {
    expect(diffAgainst('91440101664041243T', '91440101664041248T')
      .filter((s) => s.changed).map((s) => s.text)).toEqual(['3']);
  });

  it('金额格式化', () => {
    expect(formatAmount('2987.18')).toBe('¥2,987.18');
    expect(formatAmount('-100')).toBe('-¥100.00');
    expect(formatAmount('')).toBe('');
    expect(formatAmount('abc')).toBe('abc');
  });
});
