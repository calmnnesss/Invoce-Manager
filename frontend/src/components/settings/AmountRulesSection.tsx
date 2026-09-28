import { useEffect, useMemo, useRef, useState } from 'react';
import { Input, InputNumber, Tag, message, type InputRef } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { errorDetail, updateAmountRules } from '../../services/api';
import type { AmountRules, ReviewSettings } from '../../types/invoice';
import SettingsSection, { SaveBar } from './SettingsSection';
import {
  MAX_RATES, THRESHOLD_MAX, formatRate, parseRate, rulesEqual, sortRates, toDraft, validateRules,
  type RulesDraft, type ThresholdKey,
} from './settingsModel';
import styles from './AmountRulesSection.module.css';

interface AmountRulesSectionProps {
  saved: AmountRules;
  defaults: AmountRules;
  onSaved: (settings: ReviewSettings) => void;
}

const THRESHOLDS: { key: ThresholdKey; label: string; formula: string; help: string }[] = [
  {
    key: 'add_tolerance',
    label: '加法校验阈值',
    formula: '|金额 + 税额 − 价税合计|',
    help: '不超过此值，视为三项一致',
  },
  {
    key: 'mul_tolerance',
    label: '乘法校验阈值',
    formula: '|金额 × 税率 − 税额|',
    help: '不超过此值，视为税额与税率一致；由税额反推税率时也按它比对',
  },
  {
    key: 'rounding_tolerance',
    label: '多行明细凑整上限',
    formula: '加法成立时的 |金额 × 税率 − 税额|',
    help: '多行明细各自四舍五入会累积误差，差额在此范围内仍自动通过；不能小于乘法校验阈值',
  },
];

export default function AmountRulesSection({ saved, defaults, onSaved }: AmountRulesSectionProps) {
  const [draft, setDraft] = useState<RulesDraft>(() => toDraft(saved));
  const [adding, setAdding] = useState(false);
  const [rateInput, setRateInput] = useState('');
  const [saving, setSaving] = useState(false);
  const rateInputRef = useRef<InputRef>(null);
  // Esc 收起输入框时，卸载可能再触发一次失焦，那次不能把输入当成提交
  const cancelled = useRef(false);

  useEffect(() => { setDraft(toDraft(saved)); }, [saved]);
  useEffect(() => {
    if (!adding) return;
    cancelled.current = false;
    rateInputRef.current?.focus();
  }, [adding]);

  const errors = useMemo(() => validateRules(draft), [draft]);
  const dirty = !rulesEqual(draft, saved);

  const setThreshold = (key: ThresholdKey, value: number | null) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const removeRate = (rate: number) =>
    setDraft((d) => ({ ...d, standard_rates: d.standard_rates.filter((r) => r !== rate) }));

  const closeRateInput = () => {
    setRateInput('');
    setAdding(false);
  };

  /**
   * 提交新增税率。Enter：加入后输入框留着，方便连续添加；格式不对保留输入等着改。
   * 失焦：加入后收起；格式不对提示后丢弃。输入为空直接收起。
   */
  const commitRate = (fromBlur: boolean) => {
    if (!rateInput.trim()) {
      closeRateInput();
      return;
    }
    const { value, error } = parseRate(rateInput, draft.standard_rates);
    if (value === undefined) {
      message.warning(fromBlur ? `未添加：${error}` : error);
      if (fromBlur) closeRateInput();
      return;
    }
    setDraft((d) => ({ ...d, standard_rates: sortRates([...d.standard_rates, value]) }));
    if (fromBlur) closeRateInput();
    else setRateInput('');
  };

  const handleSave = async () => {
    if (Object.keys(errors).length > 0) {
      message.error('请先修正标红的内容');
      return;
    }
    setSaving(true);
    try {
      const settings = await updateAmountRules({
        add_tolerance: draft.add_tolerance as number,
        mul_tolerance: draft.mul_tolerance as number,
        rounding_tolerance: draft.rounding_tolerance as number,
        standard_rates: draft.standard_rates,
      });
      message.success('金额校验规则已保存');
      onSaved(settings);
    } catch (error) {
      message.error(errorDetail(error, '保存失败，请重试'));
    } finally {
      setSaving(false);
    }
  };

  const atDefaults = rulesEqual(draft, defaults);
  const canAddRate = draft.standard_rates.length < MAX_RATES && !saving;

  return (
    <SettingsSection
      id="amount-rules"
      title="金额校验"
      // 中文不能跨行写在 JSX 里：换行会被折成一个空格
      description={
        '自动核对金额、税率、税额、价税合计四项时的容差与税率库：四项自洽就自动通过，'
        + '只差一项时按公式补全，其余交人工复核。修改对之后识别的发票、以及之后在核对页的修改生效，'
        + '已识别的发票不会自动重算。'
      }
      footer={(
        <SaveBar
          dirty={dirty}
          saving={saving}
          summary={rulesEqual(toDraft(saved), defaults) ? '当前为出厂设置' : '已自定义'}
          atDefaults={atDefaults}
          onRestoreDefaults={() => setDraft(toDraft(defaults))}
          onDiscard={() => setDraft(toDraft(saved))}
          onSave={handleSave}
        />
      )}
    >
      <div className={styles.list}>
        {THRESHOLDS.map(({ key, label, formula, help }) => (
          <div key={key} className={styles.item}>
            <div className={styles.itemText}>
              <label htmlFor={`rule-${key}`} className={styles.label}>{label}</label>
              <span className={styles.help}>
                <code className={styles.formula}>{formula}</code> {help}
              </span>
            </div>
            <div className={styles.control}>
              <InputNumber
                id={`rule-${key}`}
                value={draft[key]}
                min={0}
                max={THRESHOLD_MAX[key]}
                step={0.01}
                precision={2}
                suffix="元"
                className={styles.numberInput}
                status={errors[key] ? 'error' : undefined}
                disabled={saving}
                onChange={(v) => setThreshold(key, v)}
              />
              {errors[key] && <span className={styles.fieldError}>{errors[key]}</span>}
            </div>
          </div>
        ))}

        <div className={`${styles.item} ${styles.ratesItem}`}>
          <div className={styles.itemText}>
            <span className={styles.label}>标准税率库</span>
            <span className={styles.help}>
              {'税率缺失或对不上时，按「税额 ÷ 金额」反推，只认这些税率。含历史税率 17%、16%、11%、10%；'
                + '0% 不参与反推（免税、零税率、不征税写法不同）。'}
            </span>
          </div>
          <div className={styles.rates}>
            {draft.standard_rates.map((rate) => (
              <Tag
                key={rate}
                className={styles.rateTag}
                closable={draft.standard_rates.length > 1 && !saving}
                onClose={(e) => { e.preventDefault(); removeRate(rate); }}
              >
                {formatRate(rate)}
              </Tag>
            ))}
            {adding ? (
              <Input
                ref={rateInputRef}
                size="small"
                className={styles.rateInput}
                value={rateInput}
                placeholder="如 8"
                suffix="%"
                aria-label="新增税率"
                onChange={(e) => setRateInput(e.target.value)}
                onPressEnter={() => commitRate(false)}
                onBlur={() => { if (!cancelled.current) commitRate(true); }}
                onKeyDown={(e) => {
                  if (e.key !== 'Escape') return;
                  cancelled.current = true;
                  closeRateInput();
                }}
              />
            ) : canAddRate && (
              <Tag className={styles.addTag} onClick={() => setAdding(true)}>
                <PlusOutlined /> 添加税率
              </Tag>
            )}
            {errors.standard_rates && <span className={styles.fieldError}>{errors.standard_rates}</span>}
          </div>
        </div>
      </div>
    </SettingsSection>
  );
}
