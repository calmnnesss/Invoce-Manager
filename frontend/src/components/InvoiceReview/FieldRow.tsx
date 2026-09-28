import { useEffect, useRef, useState } from 'react';
import { Button, Input, Tooltip } from 'antd';
import type { InputRef } from 'antd';
import {
  CheckCircleFilled, CheckOutlined, EditOutlined, ExclamationCircleFilled, InfoCircleOutlined,
  StopOutlined, UndoOutlined,
} from '@ant-design/icons';
import { COMMON_RATES, type ReviewField } from './reviewModel';
import styles from './FieldRow.module.css';

interface Props {
  field: ReviewField;
  /** 在待确认队列中的位置（从 1 开始），不在队列中为 0 */
  pendingIndex: number;
  pendingTotal: number;
  focused: boolean;
  editing: boolean;
  /** 这个字段的保存请求在途 */
  busy: boolean;
  /** 有任意保存在途：暂停所有操作，避免并发改写 */
  locked: boolean;
  readOnly: boolean;
  flash: boolean;
  selected: number;
  rowRef: (el: HTMLDivElement | null) => void;
  onFocus: () => void;
  onSelect: (index: number) => void;
  /** 采用第 index 个选项；不传则确认当前值 */
  onAccept: (index?: number) => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  /** 返回错误信息；成功返回 null */
  onSave: (value: string) => Promise<string | null>;
  onRestoreAuto: () => void;
  onConfirmInvoice: () => void;
}

const PLACEHOLDER: Record<ReviewField['input'], string> = {
  text: '请输入',
  id: '数字和字母',
  date: '例如 2024-01-31',
  amount: '例如 2987.18',
  rate: '例如 13%',
};

const CHIP: Record<string, string> = {
  pending: '待复核',
  blocked: '待复核',
};

/** 编辑框：Enter 保存（中文输入法组词时的 Enter 不算），Esc 取消，Ctrl+Enter 保存后确认整张发票 */
function FieldEditor({
  field, busy, onSave, onCancel, onConfirmInvoice,
}: {
  field: ReviewField;
  busy: boolean;
  onSave: (value: string) => Promise<string | null>;
  onCancel: () => void;
  onConfirmInvoice: () => void;
}) {
  const initial = field.input === 'amount' && field.value
    ? String(field.value).replace(/[,¥￥]/g, '')
    : field.value ?? '';
  const [draft, setDraft] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<InputRef>(null);

  useEffect(() => {
    inputRef.current?.focus({ cursor: 'all' });
  }, []);

  const save = async (value: string) => {
    const err = await onSave(value);
    setError(err);
    return err === null;
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      void save(draft).then((ok) => {
        if (ok && (e.ctrlKey || e.metaKey)) onConfirmInvoice();
      });
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onCancel();
    }
  };

  return (
    <div className={styles.editor}>
      <Input
        ref={inputRef}
        value={draft}
        status={error ? 'error' : undefined}
        placeholder={PLACEHOLDER[field.input]}
        inputMode={field.input === 'amount' ? 'decimal' : undefined}
        onChange={(e) => { setDraft(e.target.value); setError(null); }}
        onKeyDown={onKeyDown}
        disabled={busy}
        aria-label={`填写${field.fullLabel}`}
      />
      {field.input === 'rate' && (
        <div className={styles.quickPicks} aria-label="常用税率">
          {COMMON_RATES.map((r) => (
            <button key={r} type="button" className={styles.quickPick} disabled={busy}
              onClick={() => { setDraft(r); void save(r); }}>
              {r}
            </button>
          ))}
        </div>
      )}
      {error && <div className={styles.error} role="alert">{error}</div>}
      <div className={styles.editorActions}>
        <Button size="small" onClick={onCancel} disabled={busy}>取消</Button>
        <Button size="small" type="primary" loading={busy} onClick={() => void save(draft)}>
          保存 <kbd className={styles.kbdOnDark}>↵</kbd>
        </Button>
      </div>
    </div>
  );
}

function StatusBadge({ field }: { field: ReviewField }) {
  switch (field.status) {
    case 'auto':
      return (
        <Tooltip title={field.basis}>
          <span className={`${styles.badge} ${styles.badgeAuto}`}>
            <CheckCircleFilled /> 已核验
          </span>
        </Tooltip>
      );
    case 'confirmed':
      return (
        <Tooltip title={field.basis}>
          <span className={`${styles.badge} ${styles.badgeConfirmed}`}>
            <CheckOutlined /> 你已确认
          </span>
        </Tooltip>
      );
    case 'edited':
      return (
        <Tooltip title={field.basis}>
          <span className={`${styles.badge} ${styles.badgeEdited}`}>
            <EditOutlined /> 已修改
          </span>
        </Tooltip>
      );
    case 'attention':
      return (
        <Tooltip title={field.basis}>
          <span className={`${styles.badge} ${styles.badgeAttention}`}>
            <ExclamationCircleFilled /> 待复核
          </span>
        </Tooltip>
      );
    default:
      return null;
  }
}

export default function FieldRow(props: Props) {
  const {
    field, pendingIndex, pendingTotal, focused, editing, busy, locked, readOnly, flash, selected,
    rowRef, onFocus, onSelect, onAccept, onEdit, onCancelEdit, onSave, onRestoreAuto,
    onConfirmInvoice,
  } = props;

  const isCard = field.status === 'pending' || field.status === 'blocked';
  const empty = field.value === null;
  const valueText = field.display || (field.status === 'blocked' ? '已清空' : '未识别');

  const issues = field.issues.length > 0 && (
    <ul className={styles.issues}>
      {field.issues.map((issue) => (
        <li key={issue.ruleId + issue.message}>
          <ExclamationCircleFilled className={styles.issueIcon} />
          <span>{issue.message}</span>
        </li>
      ))}
    </ul>
  );

  // ---------------- 紧凑行：已处理的字段 ----------------
  if (!isCard) {
    return (
      <div
        ref={rowRef}
        tabIndex={0}
        role="group"
        aria-label={`${field.fullLabel}：${field.display || '未填写'}`}
        className={[
          styles.row,
          focused ? styles.focused : '',
          field.status === 'attention' ? styles.rowAttention : '',
          editing ? styles.rowEditing : '',
        ].join(' ')}
        onFocus={(e) => { if (e.target === e.currentTarget) onFocus(); }}
        onMouseDown={onFocus}
        onDoubleClick={() => { if (!readOnly && !locked) onEdit(); }}
      >
        <div className={styles.label}>{field.label}</div>
        <div className={styles.body}>
          {editing ? (
            <FieldEditor field={field} busy={busy} onSave={onSave} onCancel={onCancelEdit}
              onConfirmInvoice={onConfirmInvoice} />
          ) : (
            <>
              <div className={styles.valueLine}>
                <span className={`${styles.value} ${empty ? styles.valueEmpty : ''} ${flash ? styles.flash : ''}`}>
                  {valueText}
                </span>
                {flash && <span className={styles.recomputed}>已自动更新</span>}
              </div>
              {issues}
            </>
          )}
        </div>
        {!editing && (
          <div className={styles.meta}>
            <StatusBadge field={field} />
            {!readOnly && field.canRestoreAuto && (
              <Tooltip title="恢复按其余金额自动推算">
                <Button type="text" size="small" icon={<UndoOutlined />} aria-label="恢复自动推算"
                  disabled={locked} onClick={onRestoreAuto} />
              </Tooltip>
            )}
            {!readOnly && (
              <Tooltip title="修改（E）">
                <Button type="text" size="small" icon={<EditOutlined />} aria-label={`修改${field.fullLabel}`}
                  className={styles.editBtn} disabled={locked} onClick={onEdit} />
              </Tooltip>
            )}
          </div>
        )}
      </div>
    );
  }

  // ---------------- 展开卡片：需要人处理的字段 ----------------
  const blocked = field.status === 'blocked';
  const hasOptions = field.options.length > 0;
  const manualIndex = field.options.length;

  let primaryText = '确认无误';
  if (hasOptions) primaryText = '使用所选';
  if (empty && !hasOptions) primaryText = field.required ? '填写' : '确认票面无此项';

  const primary = () => {
    if (hasOptions) {
      if (selected >= manualIndex) onEdit();
      else onAccept(selected);
    } else if (empty && field.required) {
      onEdit();
    } else {
      onAccept();
    }
  };

  return (
    <div
      ref={rowRef}
      tabIndex={0}
      role="group"
      aria-label={`${field.fullLabel}，${field.reason ?? ''}`}
      className={[styles.card, blocked ? styles.cardBlocked : '', focused ? styles.cardFocused : ''].join(' ')}
      onFocus={(e) => { if (e.target === e.currentTarget) onFocus(); }}
      onMouseDown={onFocus}
    >
      <div className={styles.cardHeader}>
        <span className={styles.cardLabel}>{field.label}</span>
        {!readOnly && (
          <span className={`${styles.chip} ${blocked ? styles.chipBlocked : ''}`}>
            {CHIP[field.status]}
            {pendingIndex > 0 && ` · ${pendingIndex}/${pendingTotal}`}
          </span>
        )}
      </div>

      <div className={styles.reason}>
        {blocked ? <StopOutlined /> : <InfoCircleOutlined />}
        <span>{field.reason}</span>
      </div>

      {editing ? (
        <FieldEditor field={field} busy={busy} onSave={onSave} onCancel={onCancelEdit}
          onConfirmInvoice={onConfirmInvoice} />
      ) : (
        <>
          {hasOptions ? (
            <div className={styles.options} role="radiogroup" aria-label={`${field.fullLabel}的候选值`}>
              {field.options.map((opt, i) => (
                <button
                  key={opt.value}
                  type="button"
                  role="radio"
                  aria-checked={selected === i}
                  disabled={opt.excluded || readOnly || locked}
                  className={[
                    styles.option,
                    selected === i ? styles.optionSelected : '',
                    opt.excluded ? styles.optionExcluded : '',
                  ].join(' ')}
                  onClick={() => { onSelect(i); onAccept(i); }}
                >
                  <kbd className={styles.kbd}>{i + 1}</kbd>
                  <span className={styles.optionText}>
                    {opt.segments.map((s, j) => (
                      <span key={j} className={s.changed ? styles.diff : undefined}>{s.text}</span>
                    ))}
                  </span>
                  {opt.recommended && !opt.excluded && <span className={styles.recommend}>AI 推荐</span>}
                  {opt.excluded && <span className={styles.excludedNote}>与本公司相同</span>}
                </button>
              ))}
              {!readOnly && (
                <button
                  type="button"
                  role="radio"
                  aria-checked={selected === manualIndex}
                  disabled={locked}
                  className={`${styles.option} ${styles.optionManual} ${selected === manualIndex ? styles.optionSelected : ''}`}
                  onClick={() => { onSelect(manualIndex); onEdit(); }}
                >
                  <kbd className={styles.kbd}>{manualIndex + 1}</kbd>
                  <span className={styles.optionText}>{blocked ? '手动填写…' : '都不对，手动填写…'}</span>
                </button>
              )}
            </div>
          ) : (
            <div className={`${styles.cardValue} ${empty ? styles.valueEmpty : ''} ${flash ? styles.flash : ''}`}>
              {valueText}
            </div>
          )}

          {field.evidence.length > 0 && (
            <div className={styles.evidence}>票面：{field.evidence.join(' / ')}</div>
          )}

          {!readOnly && (
            <div className={styles.cardActions}>
              {field.canRestoreAuto && (
                <Button size="small" icon={<UndoOutlined />} disabled={locked} onClick={onRestoreAuto}>
                  恢复自动推算
                </Button>
              )}
              {!(empty && field.required && !hasOptions) && (
                <Button size="small" disabled={locked} onClick={onEdit}>
                  修改 <kbd className={styles.kbd}>E</kbd>
                </Button>
              )}
              <Button size="small" type="primary" loading={busy} disabled={locked && !busy} onClick={primary}>
                {primaryText} <kbd className={styles.kbdOnDark}>↵</kbd>
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** 不参与识别核对的普通字段（归属人），或只读的补充信息（规格型号等） */
export function PlainRow({
  label, value, placeholder, editable, busy, locked, onSave,
}: {
  label: string;
  value: string | null;
  placeholder: string;
  editable: boolean;
  busy?: boolean;
  locked?: boolean;
  onSave?: (value: string) => Promise<string | null>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? '');
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!onSave) return;
    const err = await onSave(draft);
    setError(err);
    if (!err) setEditing(false);
  };

  return (
    <div className={`${styles.row} ${editing ? styles.rowEditing : ''}`}>
      <div className={styles.label}>{label}</div>
      <div className={styles.body}>
        {editing ? (
          <div className={styles.editor}>
            <Input
              autoFocus
              value={draft}
              status={error ? 'error' : undefined}
              placeholder={placeholder}
              onChange={(e) => { setDraft(e.target.value); setError(null); }}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); void save(); }
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setEditing(false); }
              }}
              disabled={busy}
            />
            {error && <div className={styles.error} role="alert">{error}</div>}
            <div className={styles.editorActions}>
              <Button size="small" onClick={() => setEditing(false)} disabled={busy}>取消</Button>
              <Button size="small" type="primary" loading={busy} onClick={() => void save()}>保存</Button>
            </div>
          </div>
        ) : (
          <div className={styles.valueLine}>
            <span className={`${styles.value} ${value ? '' : styles.valueEmpty}`}>{value || placeholder}</span>
          </div>
        )}
      </div>
      {editable && !editing && (
        <div className={styles.meta}>
          <Button type="text" size="small" icon={<EditOutlined />} aria-label={`修改${label}`}
            className={styles.editBtnVisible} disabled={locked}
            onClick={() => { setDraft(value ?? ''); setEditing(true); }} />
        </div>
      )}
    </div>
  );
}
