import { useEffect, useMemo, useState } from 'react';
import { Button, Input, Tooltip, message } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { errorDetail, updateBuyerEntities } from '../../services/api';
import type { BuyerEntity, ReviewSettings } from '../../types/invoice';
import SettingsSection, { SaveBar } from './SettingsSection';
import {
  MAX_ENTITIES, entitiesEqual, newEntityRow, normalizeTaxId, toEntityPayload, toRows,
  validateEntities, type EntityRow,
} from './settingsModel';
import styles from './BuyerEntitiesSection.module.css';

interface BuyerEntitiesSectionProps {
  saved: BuyerEntity[];
  defaults: BuyerEntity[];
  onSaved: (settings: ReviewSettings) => void;
}

type Field = 'name' | 'tax_id';

export default function BuyerEntitiesSection({ saved, defaults, onSaved }: BuyerEntitiesSectionProps) {
  const [rows, setRows] = useState<EntityRow[]>(() => toRows(saved));
  // 错误只在离开输入框或点过保存之后显示，刚加的空行不要一上来就标红
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [focusKey, setFocusKey] = useState<string | null>(null);

  useEffect(() => {
    setRows(toRows(saved));
    setTouched(new Set());
    setSubmitted(false);
  }, [saved]);

  const errors = useMemo(() => validateEntities(rows), [rows]);
  const dirty = !entitiesEqual(rows, saved);
  const shown = (key: string, field: Field) =>
    (submitted || touched.has(`${key}:${field}`)) ? errors[key]?.[field] : undefined;

  const update = (key: string, field: Field, value: string) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, [field]: value } : r)));

  const touch = (key: string, field: Field) => {
    setTouched((t) => new Set(t).add(`${key}:${field}`));
    if (field === 'tax_id') {
      setRows((rs) => rs.map((r) => (r.key === key ? { ...r, tax_id: normalizeTaxId(r.tax_id) } : r)));
    }
  };

  const addRow = () => {
    const row = newEntityRow();
    setRows((rs) => [...rs, row]);
    setFocusKey(row.key);
  };

  const removeRow = (key: string) => setRows((rs) => rs.filter((r) => r.key !== key));

  const reset = (entities: BuyerEntity[]) => {
    setRows(toRows(entities));
    setTouched(new Set());
    setSubmitted(false);
  };

  const handleSave = async () => {
    setSubmitted(true);
    if (Object.keys(errors).length > 0) {
      message.error('请先修正标红的内容');
      return;
    }
    setSaving(true);
    try {
      const settings = await updateBuyerEntities(toEntityPayload(rows));
      message.success('购买方主体已保存，之后识别的发票按新清单处理');
      onSaved(settings);
    } catch (error) {
      message.error(errorDetail(error, '保存失败，请重试'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      id="buyer-entities"
      title="购买方主体"
      // 中文不能跨行写在 JSX 里：换行会被折成一个空格
      description={
        '发票的购买方只从这份清单里选，名称和税号按清单填写，不会出现错字或串栏；'
        + '销售方如果是清单里的主体，这张发票会被拦下待处理。修改只影响之后识别的发票。'
      }
      footer={(
        <SaveBar
          dirty={dirty}
          saving={saving}
          summary={`共 ${saved.length} 个主体`}
          atDefaults={entitiesEqual(rows, defaults)}
          onRestoreDefaults={() => reset(defaults)}
          onDiscard={() => reset(saved)}
          onSave={handleSave}
        />
      )}
    >
      <div className={styles.table} role="table" aria-label="购买方主体">
        <div className={`${styles.row} ${styles.headRow}`} role="row">
          <span role="columnheader">编号</span>
          <span role="columnheader">名称</span>
          <span role="columnheader">纳税人识别号</span>
          <span role="columnheader" aria-label="操作" />
        </div>
        {rows.map((row) => {
          const nameError = shown(row.key, 'name');
          const taxError = shown(row.key, 'tax_id');
          return (
            <div key={row.key} className={styles.row} role="row">
              <span className={styles.entityId} role="cell">
                {row.id ?? <span className={styles.newBadge}>新增</span>}
              </span>
              <div role="cell" className={styles.cell}>
                <Input
                  value={row.name}
                  placeholder="例如 深圳市某某科技有限公司"
                  status={nameError ? 'error' : undefined}
                  maxLength={255}
                  autoFocus={focusKey === row.key}
                  aria-label="名称"
                  onChange={(e) => update(row.key, 'name', e.target.value)}
                  onBlur={() => touch(row.key, 'name')}
                />
                {nameError && <span className={styles.fieldError}>{nameError}</span>}
              </div>
              <div role="cell" className={styles.cell}>
                <Input
                  value={row.tax_id}
                  placeholder="15 或 18 位"
                  status={taxError ? 'error' : undefined}
                  maxLength={24}
                  className={styles.taxInput}
                  aria-label="纳税人识别号"
                  onChange={(e) => update(row.key, 'tax_id', e.target.value.toUpperCase())}
                  onBlur={() => touch(row.key, 'tax_id')}
                />
                {taxError && <span className={styles.fieldError}>{taxError}</span>}
              </div>
              <span role="cell" className={styles.rowAction}>
                <Tooltip title={rows.length === 1 ? '至少保留一个主体' : '删除'}>
                  <Button
                    type="text"
                    icon={<DeleteOutlined />}
                    aria-label="删除"
                    disabled={rows.length === 1 || saving}
                    onClick={() => removeRow(row.key)}
                  />
                </Tooltip>
              </span>
            </div>
          );
        })}
      </div>
      <Button
        type="dashed"
        icon={<PlusOutlined />}
        className={styles.addButton}
        disabled={rows.length >= MAX_ENTITIES || saving}
        onClick={addRow}
      >
        添加主体
      </Button>
    </SettingsSection>
  );
}
