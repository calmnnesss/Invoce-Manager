import { describe, expect, it } from 'vitest';
import type { AmountRules } from '../../types/invoice';
import {
  entitiesEqual, normalizeTaxId, parseRate, rulesEqual, toEntityPayload, toRows,
  validateEntities, validateRules, type EntityRow, type RulesDraft,
} from './settingsModel';

const SAVED = [
  { id: 'E01', name: '深圳市购机汇网络有限公司', tax_id: '440300083885931' },
  { id: 'E02', name: '深圳市购机汇科技有限公司', tax_id: '440300083885942' },
];

function row(key: string, name: string, tax_id: string, id?: string): EntityRow {
  return { key, id, name, tax_id };
}

describe('购买方主体清单', () => {
  it('税号去空格与连字符并转大写', () => {
    expect(normalizeTaxId(' 91440300 ma5f-xxxx1a ')).toBe('91440300MA5FXXXX1A');
  });

  it('逐行标出缺失、格式错误与重复，重复只标在后出现的那一行', () => {
    const errors = validateEntities([
      row('a', '甲 公司', '440300083885991'),
      row('b', '', '4403000838859'),
      row('c', '甲公司', '4403-0008 3885991'),
      row('d', '乙公司', ''),
    ]);
    expect(errors.a).toBeUndefined();
    expect(errors.b).toEqual({ name: '请填写名称', tax_id: '应为 15 或 18 位数字或大写字母' });
    expect(errors.c).toEqual({ name: '与上面的主体重名', tax_id: '与上面的主体税号相同' });
    expect(errors.d).toEqual({ tax_id: '请填写纳税人识别号' });
  });

  it('按保存后的样子比较是否有改动：只多了空格不算改动，新增行算', () => {
    const rows = toRows(SAVED);
    expect(entitiesEqual(rows, SAVED)).toBe(true);
    rows[0] = { ...rows[0], name: ` ${rows[0].name} `, tax_id: rows[0].tax_id.toLowerCase() };
    expect(entitiesEqual(rows, SAVED)).toBe(true);
    expect(entitiesEqual([...rows, row('n', '丙', '440300083885999')], SAVED)).toBe(false);
    expect(entitiesEqual([...rows].reverse(), SAVED)).toBe(false);
  });

  it('提交时已有主体带 id，新增的不带', () => {
    const payload = toEntityPayload([row('E01', ' 甲 ', '440300083885991', 'E01'),
      row('new-1', '乙', '4403 0008 3885992')]);
    expect(payload).toEqual([
      { id: 'E01', name: '甲', tax_id: '440300083885991' },
      { name: '乙', tax_id: '440300083885992' },
    ]);
  });
});

describe('金额校验规则', () => {
  const saved: AmountRules = {
    add_tolerance: 0.01, mul_tolerance: 0.02, rounding_tolerance: 0.05,
    standard_rates: [13, 9, 6],
  };
  const base: RulesDraft = { ...saved };

  it('出厂规则没有错误', () => {
    expect(validateRules(base)).toEqual({});
  });

  it('阈值必填、有范围、最多到分；凑整上限不能小于乘法阈值', () => {
    expect(validateRules({ ...base, add_tolerance: null }).add_tolerance).toBe('请填写');
    expect(validateRules({ ...base, mul_tolerance: 1.5 }).mul_tolerance).toBe('应在 0 到 1 元之间');
    expect(validateRules({ ...base, rounding_tolerance: 6 }).rounding_tolerance).toBe('应在 0 到 5 元之间');
    expect(validateRules({ ...base, add_tolerance: 0.005 }).add_tolerance).toBe('最多精确到分');
    expect(validateRules({ ...base, rounding_tolerance: 0.01 }).rounding_tolerance)
      .toBe('不能小于乘法校验阈值');
    expect(validateRules({ ...base, standard_rates: [] }).standard_rates).toBe('至少保留一个税率');
  });

  it('税率库比较不看顺序', () => {
    expect(rulesEqual({ ...base, standard_rates: [6, 13, 9] }, saved)).toBe(true);
    expect(rulesEqual({ ...base, standard_rates: [6, 13] }, saved)).toBe(false);
    expect(rulesEqual({ ...base, add_tolerance: 0.02 }, saved)).toBe(false);
  });

  it('新增税率：接受 13、13%、1.5，拒绝 0、100、三位小数与重复', () => {
    expect(parseRate('13%', [])).toEqual({ value: 13 });
    expect(parseRate(' 1.5 ', [])).toEqual({ value: 1.5 });
    expect(parseRate('0', []).error).toBe('税率应大于 0% 且小于 100%');
    expect(parseRate('100', []).error).toBe('税率应大于 0% 且小于 100%');
    expect(parseRate('13.555', []).error).toBe('税率格式不正确，例如 13 或 1.5');
    expect(parseRate('abc', []).error).toBe('税率格式不正确，例如 13 或 1.5');
    expect(parseRate('13', [13]).error).toBe('13% 已在税率库中');
  });
});
