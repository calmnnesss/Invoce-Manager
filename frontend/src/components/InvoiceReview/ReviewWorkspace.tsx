import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Dropdown, Modal, Popconfirm, Result, Skeleton, Spin, Tooltip, message } from 'antd';
import type { MenuProps } from 'antd';
import {
  ArrowLeftOutlined, CheckCircleFilled, DownloadOutlined, LeftOutlined, LockOutlined, MoreOutlined,
  ProfileOutlined, QrcodeOutlined, RightOutlined, SyncOutlined,
} from '@ant-design/icons';
import StatusTag from '../StatusTag';
import { getInvoiceFileUrl, getInvoicePreviewUrl } from '../../services/api';
import type { FieldUpdateBody } from '../../types/invoice';
import DocumentViewer, { type DocumentViewerHandle } from './DocumentViewer';
import FieldRow, { PlainRow } from './FieldRow';
import RecognitionDetailsDrawer from './RecognitionDetailsDrawer';
import ReviewSummary from './ReviewSummary';
import { GROUPS, buildReviewModel, fieldLabel, type ReviewField } from './reviewModel';
import { formatAmount } from './format';
import { useInvoiceReview, useReviewQueue } from './useInvoiceReview';
import { SHORTCUTS, useReviewKeyboard } from './useReviewKeyboard';
import styles from './ReviewWorkspace.module.css';

const STATUS_TAG: Record<string, 'pending' | 'processing' | 'success' | 'warning' | 'error'> = {
  已上传: 'pending',
  解析中: 'processing',
  待处理: 'warning',
  待审核: 'warning',
  已确认: 'success',
  已报销: 'success',
  未报销: 'pending',
};

/** 没有识别明细、只在非空时展示的补充信息（v6 不抽取这些字段） */
const EXTRA_FIELDS: { key: 'specification' | 'unit' | 'quantity' | 'unit_price'; label: string }[] = [
  { key: 'specification', label: '规格型号' },
  { key: 'unit', label: '单位' },
  { key: 'quantity', label: '数量' },
  { key: 'unit_price', label: '单价' },
];

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/** 默认选中的候选：当前采用值，否则第一个可选项 */
function defaultOption(f: ReviewField): number {
  const rec = f.options.findIndex((o) => o.recommended && !o.excluded);
  if (rec >= 0) return rec;
  const first = f.options.findIndex((o) => !o.excluded);
  return first >= 0 ? first : f.options.length;
}

export default function ReviewWorkspace({ invoiceId }: { invoiceId: number }) {
  const navigate = useNavigate();
  const review = useInvoiceReview(invoiceId);
  const queue = useReviewQueue(invoiceId);
  const { invoice } = review;

  const model = useMemo(
    () => (invoice ? buildReviewModel(invoice, { processing: review.reprocessing || review.waiting }) : null),
    [invoice, review.reprocessing, review.waiting],
  );

  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, number>>({});
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reprocessAsk, setReprocessAsk] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [queueDone, setQueueDone] = useState(false);

  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const viewerRef = useRef<DocumentViewerHandle>(null);
  const advanceFrom = useRef<string | null>(null);
  const initialFocus = useRef(false);

  const busy = review.savingKey !== null;

  // ---------------------------------------------------------------- 焦点
  const focusField = useCallback((key: string) => {
    setFocusedKey(key);
    const el = rowRefs.current[key];
    if (!el) return;
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }, []);

  const focusConfirm = useCallback(() => {
    setFocusedKey(null);
    confirmBtnRef.current?.focus();
  }, []);

  // 等下一次提交后再聚焦：编辑框卸载时焦点会掉到 body，要等新的 DOM 就位。
  // 不用 requestAnimationFrame——页面不绘制时（后台标签页）它不会执行，焦点就丢了。
  const pendingFocus = useRef<string | null>(null);
  const [focusTick, setFocusTick] = useState(0);
  const scheduleFocus = useCallback((key: string) => {
    pendingFocus.current = key;
    setFocusTick((t) => t + 1);
  }, []);
  useEffect(() => {
    const key = pendingFocus.current;
    if (key === null) return;
    pendingFocus.current = null;
    focusField(key);
  }, [focusTick, focusField]);

  // 进入页面：聚焦第一个待确认项（卡片本身，不是输入框，避免误敲覆盖）
  useEffect(() => {
    if (!model || initialFocus.current || !model.hasResults || model.processing) return;
    initialFocus.current = true;
    if (model.pendingKeys.length && !model.readOnly) focusField(model.pendingKeys[0]);
  }, [model, focusField]);

  // 一项处理完：跳到它后面的下一个待确认项；全部处理完就把焦点交给「确认发票」。
  // 用状态计数触发而不是只靠 ref：保证这次渲染里已经是保存后的新数据。
  const [advanceTick, setAdvanceTick] = useState(0);
  const advanceAfter = useCallback((key: string) => {
    advanceFrom.current = key;
    setAdvanceTick((t) => t + 1);
  }, []);
  useEffect(() => {
    if (!model || advanceFrom.current === null) return;
    const from = advanceFrom.current;
    advanceFrom.current = null;
    const order = model.fields.map((f) => f.key);
    const pending = model.pendingKeys;
    if (!pending.length) {
      focusConfirm();
      return;
    }
    const start = order.indexOf(from);
    const next = pending.find((k) => order.indexOf(k) > start) ?? pending[0];
    focusField(next);
  }, [advanceTick, model, focusField, focusConfirm]);

  // ---------------------------------------------------------------- 字段操作
  const startEdit = useCallback((key: string) => {
    if (!model || model.readOnly || busy) return;
    setEditingKey(key);
    setFocusedKey(key);
  }, [model, busy]);

  const acceptField = useCallback(async (key: string, index?: number) => {
    const f = model?.byKey[key];
    if (!model || !f || model.readOnly || busy) return;
    let body: FieldUpdateBody;
    if (index !== undefined && f.options.length) {
      if (index >= f.options.length) { startEdit(key); return; }
      const opt = f.options[index];
      if (opt.excluded) return;
      body = opt.recommended ? { action: 'confirm' } : { action: 'set', value: opt.value };
    } else if (f.value === null && f.required) {
      startEdit(key);
      return;
    } else {
      body = { action: 'confirm' };
    }
    const err = await review.updateField(key, body);
    if (err) { message.error(err); return; }
    advanceAfter(key);
  }, [model, busy, review, startEdit, advanceAfter]);

  const saveField = useCallback(async (key: string, value: string) => {
    const wasPending = model?.pendingKeys.includes(key);
    const err = await review.updateField(key, { action: 'set', value });
    if (err) return err;
    setEditingKey(null);
    if (wasPending) advanceAfter(key);
    else scheduleFocus(key);
    return null;
  }, [model, review, scheduleFocus, advanceAfter]);

  const restoreAuto = useCallback(async (key: string) => {
    const err = await review.updateField(key, { action: 'auto' });
    if (err) message.error(err);
  }, [review]);

  const saveOwner = useCallback(
    (value: string) => review.updateField('owner', { action: 'set', value }),
    [review],
  );

  // ---------------------------------------------------------------- 确认发票
  const doConfirm = useCallback(async () => {
    setConfirmOpen(false);
    const nextId = queue.nextAfterConfirm();
    const ok = await review.confirm();
    if (!ok) return;
    if (nextId) {
      message.success('发票已确认，已为你打开下一张待审核发票');
      navigate(`/invoices/${nextId}`, { replace: true });
    } else {
      message.success('发票已确认');
      setQueueDone(true);
    }
  }, [queue, review, navigate]);

  const requestConfirm = useCallback(() => {
    if (!model || model.readOnly || !model.hasResults || model.processing || busy) return;
    if (model.confirmBlocker) {
      message.warning(model.confirmBlocker);
      const target = model.missingRequired[0] ?? (model.byKey.seller_name ? 'seller_name' : undefined);
      if (target) focusField(target);
      return;
    }
    if (model.pendingKeys.length) {
      setConfirmOpen(true);
      return;
    }
    void doConfirm();
  }, [model, busy, focusField, doConfirm]);

  // 编辑框里按 Ctrl+Enter：先保存，等新状态渲染后再走确认流程
  const requestConfirmRef = useRef(requestConfirm);
  requestConfirmRef.current = requestConfirm;
  const confirmFromEditor = useCallback(() => {
    window.setTimeout(() => requestConfirmRef.current(), 0);
  }, []);

  // ---------------------------------------------------------------- 队列导航
  const openInvoice = useCallback((id: number | null) => {
    if (id !== null) navigate(`/invoices/${id}`, { replace: true });
  }, [navigate]);

  // ---------------------------------------------------------------- 快捷键
  const keyboardOn = Boolean(model && model.hasResults && !model.processing)
    && !drawerOpen && !helpOpen && !confirmOpen && !reprocessAsk && !menuOpen && editingKey === null;

  const moveFocus = (delta: number) => {
    if (!model?.fields.length) return;
    const order = model.fields.map((f) => f.key);
    const at = focusedKey ? order.indexOf(focusedKey) : -1;
    const next = at < 0 ? (delta > 0 ? 0 : order.length - 1) : Math.min(order.length - 1, Math.max(0, at + delta));
    focusField(order[next]);
  };

  useReviewKeyboard(keyboardOn, {
    accept: () => {
      if (!model) return;
      const f = focusedKey ? model.byKey[focusedKey] : undefined;
      if (!f || (f.status !== 'pending' && f.status !== 'blocked')) {
        if (model.pendingKeys.length) focusField(model.pendingKeys[0]);
        else focusConfirm();
        return;
      }
      void acceptField(f.key, f.options.length ? selected[f.key] ?? defaultOption(f) : undefined);
    },
    select: (i) => {
      const f = focusedKey ? model?.byKey[focusedKey] : undefined;
      if (!f || !f.options.length || i > f.options.length) return;
      if (i < f.options.length && f.options[i].excluded) return;
      setSelected((s) => ({ ...s, [f.key]: i }));
    },
    edit: () => { if (focusedKey) startEdit(focusedKey); },
    next: () => moveFocus(1),
    prev: () => moveFocus(-1),
    confirmInvoice: requestConfirm,
    prevInvoice: () => openInvoice(queue.prevId),
    nextInvoice: () => openInvoice(queue.nextId),
    zoomIn: () => viewerRef.current?.zoomIn(),
    zoomOut: () => viewerRef.current?.zoomOut(),
    fit: () => viewerRef.current?.fit(),
    rotate: () => viewerRef.current?.rotate(),
    help: () => setHelpOpen(true),
  });

  // ---------------------------------------------------------------- 渲染
  if (review.loadState === 'loading') {
    return <div className={styles.center}><Spin size="large" /></div>;
  }
  if (review.loadState === 'not-found' || !invoice || !model) {
    return (
      <div className={styles.center}>
        <Result
          status={review.loadState === 'not-found' ? '404' : 'error'}
          title={review.loadState === 'not-found' ? '发票不存在' : '加载失败'}
          subTitle={review.loadState === 'not-found' ? '它可能已被删除' : '请检查网络后重试'}
          extra={[
            <Button key="back" onClick={() => navigate('/invoices')}>返回列表</Button>,
            review.loadState === 'error' && (
              <Button key="retry" type="primary" onClick={() => void review.reload()}>重试</Button>
            ),
          ]}
        />
      </div>
    );
  }

  const rows = model.fields;
  const seller = model.byKey.seller_name?.value ?? invoice.seller_name;
  const total = formatAmount(model.byKey.total_with_tax?.value ?? invoice.total_with_tax);
  const number = model.byKey.invoice_number?.value ?? invoice.invoice_number;
  const date = model.byKey.issue_date?.value ?? invoice.issue_date;
  const confirmed = invoice.status === '已确认' && model.pendingKeys.length === 0;
  const canAct = !model.readOnly && model.hasResults && !model.processing;

  const menuItems: MenuProps['items'] = [
    ...(!model.readOnly && !model.processing ? [
      { key: 'reprocess', icon: <SyncOutlined />, label: '重新识别' },
      ...(model.hasResults ? [{ key: 'qr', icon: <QrcodeOutlined />, label: '尝试读取二维码' }] : []),
    ] : []),
    { key: 'download', icon: <DownloadOutlined />, label: <a href={getInvoiceFileUrl(invoice.id)} download>下载原件</a> },
    { type: 'divider' },
    { key: 'details', icon: <ProfileOutlined />, label: '识别详情', disabled: !model.hasResults },
  ];

  const onMenu: MenuProps['onClick'] = ({ key }) => {
    setMenuOpen(false);
    if (key === 'reprocess') setReprocessAsk(true);
    else if (key === 'qr') void review.readQr();
    else if (key === 'details') setDrawerOpen(true);
  };

  const renderField = (f: ReviewField) => {
    const pendingIndex = model.pendingKeys.indexOf(f.key) + 1;
    return (
      <FieldRow
        key={f.key}
        field={f}
        pendingIndex={pendingIndex}
        pendingTotal={model.pendingKeys.length}
        focused={focusedKey === f.key}
        editing={editingKey === f.key}
        busy={review.savingKey === f.key}
        locked={busy}
        readOnly={!canAct}
        flash={review.flashKeys.includes(f.key)}
        selected={selected[f.key] ?? defaultOption(f)}
        rowRef={(el) => { rowRefs.current[f.key] = el; }}
        onFocus={() => setFocusedKey(f.key)}
        onSelect={(i) => setSelected((s) => ({ ...s, [f.key]: i }))}
        onAccept={(i) => void acceptField(f.key, i)}
        onEdit={() => startEdit(f.key)}
        onCancelEdit={() => { setEditingKey(null); scheduleFocus(f.key); }}
        onSave={(v) => saveField(f.key, v)}
        onRestoreAuto={() => void restoreAuto(f.key)}
        onConfirmInvoice={confirmFromEditor}
      />
    );
  };

  const extras = EXTRA_FIELDS.filter((x) => invoice[x.key] !== null && invoice[x.key] !== undefined);

  let footerInfo: React.ReactNode;
  if (model.readOnly) footerInfo = <><LockOutlined /> 已进入报销流程，不可修改</>;
  else if (!model.hasResults || model.processing) footerInfo = '识别完成后即可核对';
  else if (confirmed) footerInfo = <><CheckCircleFilled className={styles.okIcon} /> 发票已确认</>;
  else if (model.pendingKeys.length) footerInfo = <>还需复核 <b className={styles.count}>{model.pendingKeys.length}</b> 项</>;
  else if (model.hintKeys.length) footerInfo = `全部核对完成 · 仍有 ${model.hintKeys.length} 项需要复核`;
  else footerInfo = <><CheckCircleFilled className={styles.okIcon} /> 全部核对完成</>;

  return (
    <div className={styles.workspace}>
      {/* ---------------- 头部 ---------------- */}
      <header className={styles.header}>
        <button type="button" className={styles.back} onClick={() => navigate('/invoices')}>
          <ArrowLeftOutlined /> 返回列表
        </button>
        <div className={styles.titleBlock}>
          <div className={styles.titleRow}>
            <h1 className={styles.title} title={seller ?? undefined}>{seller || '销售方待填写'}</h1>
            {total && <span className={styles.total}>{total}</span>}
            <StatusTag status={STATUS_TAG[invoice.status] ?? 'pending'}>{invoice.status}</StatusTag>
          </div>
          <div className={styles.subtitle}>
            {[number && `发票号码 ${number}`, date, invoice.file_name].filter(Boolean).join(' · ')}
          </div>
        </div>
        <div className={styles.headerRight}>
          {queue.index >= 0 && queue.total > 0 && (
            <div className={styles.queue}>
              <Tooltip title="上一张（[）">
                <Button type="text" size="small" icon={<LeftOutlined />} aria-label="上一张待审核发票"
                  disabled={!queue.prevId} onClick={() => openInvoice(queue.prevId)} />
              </Tooltip>
              <span className={styles.queueText}>待审核 {queue.index + 1}/{queue.total}</span>
              <Tooltip title="下一张（]）">
                <Button type="text" size="small" icon={<RightOutlined />} aria-label="下一张待审核发票"
                  disabled={!queue.nextId} onClick={() => openInvoice(queue.nextId)} />
              </Tooltip>
            </div>
          )}
          <Dropdown menu={{ items: menuItems, onClick: onMenu }} trigger={['click']}
            open={menuOpen} onOpenChange={setMenuOpen} placement="bottomRight">
            <Button icon={<MoreOutlined />} aria-label="更多操作" />
          </Dropdown>
        </div>
      </header>

      {/* ---------------- 主体 ---------------- */}
      <div className={styles.body}>
        <section className={styles.viewerPane} aria-label="发票原件">
          <DocumentViewer
            ref={viewerRef}
            invoiceId={invoice.id}
            src={getInvoicePreviewUrl(invoice.id)}
            downloadUrl={getInvoiceFileUrl(invoice.id)}
          />
        </section>

        <aside className={styles.panel} aria-label="核对">
          <div className={styles.panelScroll}>
            <ReviewSummary model={model} onFocusField={focusField} />

            {model.processing ? (
              <div className={styles.skeleton}>
                <Skeleton active paragraph={{ rows: 7 }} title={false} />
              </div>
            ) : !model.hasResults ? (
              <div className={styles.emptyState}>
                <p>{review.pollFailed ? '这张发票的识别没有完成。' : '这张发票还没有识别。'}</p>
                {!model.readOnly && (
                  <Button type="primary" icon={<SyncOutlined />} onClick={() => void review.reprocess()}>
                    开始识别
                  </Button>
                )}
              </div>
            ) : (
              GROUPS.map((g) => {
                if (g.key === 'other') {
                  return (
                    <section key={g.key} className={styles.group}>
                      <h3 className={styles.groupTitle}>{g.title}</h3>
                      <PlainRow
                        label="归属人"
                        value={invoice.owner}
                        placeholder="未填写"
                        editable={canAct}
                        busy={review.savingKey === 'owner'}
                        locked={busy}
                        onSave={saveOwner}
                      />
                    </section>
                  );
                }
                const inGroup = rows.filter((f) => f.group === g.key);
                const groupExtras = g.key === 'goods' ? extras : [];
                if (!inGroup.length && !groupExtras.length) return null;
                return (
                  <section key={g.key} className={styles.group}>
                    <h3 className={styles.groupTitle}>{g.title}</h3>
                    {inGroup.map(renderField)}
                    {groupExtras.map((x) => (
                      <PlainRow key={x.key} label={x.label} value={String(invoice[x.key])}
                        placeholder="—" editable={false} />
                    ))}
                  </section>
                );
              })
            )}
          </div>

          {/* ---------------- 底栏 ---------------- */}
          <footer className={styles.footer}>
            <div className={styles.footerInfo}>
              {footerInfo}
              <Tooltip title="键盘快捷键（?）">
                <button type="button" className={styles.kbdHint} onClick={() => setHelpOpen(true)}>⌨ 快捷键</button>
              </Tooltip>
            </div>
            <div className={styles.footerActions}>
              {confirmed || model.readOnly ? (
                queue.nextId ? (
                  <Button type="primary" onClick={() => openInvoice(queue.nextId)}>
                    下一张待审核 <RightOutlined />
                  </Button>
                ) : (
                  <Button onClick={() => navigate('/invoices')}>
                    {queueDone ? '待审核发票已全部处理，返回列表' : '返回列表'}
                  </Button>
                )
              ) : (
                <Popconfirm
                  open={confirmOpen}
                  onOpenChange={(open) => { if (!open) setConfirmOpen(false); }}
                  title={`还有 ${model.pendingKeys.length} 项需要复核`}
                  description={
                    <div className={styles.popDesc}>
                      <div>{model.pendingKeys.map(fieldLabel).join('、')}</div>
                      <div className={styles.popHint}>将按当前结果一并确认</div>
                    </div>
                  }
                  okText="仍然确认"
                  cancelText="去核对"
                  onConfirm={() => void doConfirm()}
                  onCancel={() => {
                    setConfirmOpen(false);
                    if (model.pendingKeys.length) scheduleFocus(model.pendingKeys[0]);
                  }}
                  placement="topRight"
                >
                  <Tooltip title={model.confirmBlocker ?? undefined}>
                    <Button
                      ref={confirmBtnRef}
                      type="primary"
                      size="large"
                      disabled={!canAct || busy || model.confirmBlocker !== null}
                      onClick={requestConfirm}
                      className={styles.confirmBtn}
                    >
                      确认发票 <kbd className={styles.kbd}>Ctrl ↵</kbd>
                    </Button>
                  </Tooltip>
                </Popconfirm>
              )}
            </div>
          </footer>
        </aside>
      </div>

      <RecognitionDetailsDrawer open={drawerOpen} invoice={invoice} onClose={() => setDrawerOpen(false)} />

      <Modal
        title="重新识别这张发票？"
        open={reprocessAsk}
        okText="重新识别"
        cancelText="取消"
        onOk={() => { setReprocessAsk(false); void review.reprocess(); }}
        onCancel={() => setReprocessAsk(false)}
      >
        会重新调用识别服务，并覆盖你在这张发票上做的所有修改和确认。
      </Modal>

      <Modal title="键盘快捷键" open={helpOpen} footer={null} onCancel={() => setHelpOpen(false)} width={460}>
        <dl className={styles.shortcuts}>
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className={styles.shortcut}>
              <dt><kbd className={styles.kbdLight}>{s.keys}</kbd></dt>
              <dd>{s.action}</dd>
            </div>
          ))}
        </dl>
      </Modal>
    </div>
  );
}
