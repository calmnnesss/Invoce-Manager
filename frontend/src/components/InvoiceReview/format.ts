/** 金额展示：千分位 + 两位小数，负数写成 -¥100.00。读不成数时原样返回，空值返回空串。 */
export function formatAmount(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '';
  const n = typeof value === 'number' ? value : Number(String(value).replace(/[,，¥￥\s]/g, ''));
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${n < 0 ? '-' : ''}¥${abs}`;
}
