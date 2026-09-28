import { useCallback, useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import {
  confirmInvoice, errorDetail, getInvoice, listInvoices, reprocessInvoice, retryQr,
  updateInvoiceField,
} from '../../services/api';
import type { FieldUpdateBody, InvoiceDetail } from '../../types/invoice';

const POLL_MS = 2500;
/** 识别中最多等这么久；uvicorn --reload 会杀掉后台任务，状态可能永远停在「解析中」 */
const PROCESSING_LIMIT_MS = 3 * 60 * 1000;
/** 已上传但还没开始识别：可能在排队，也可能是后台重试耗尽后被打回 */
const QUEUED_LIMIT_MS = 60 * 1000;
const FLASH_MS = 2400;

export type LoadState = 'loading' | 'ready' | 'not-found' | 'error';

/** 单张发票的数据与操作。页面以发票 id 为 key 渲染，换一张票就是一个全新的实例。 */
export function useInvoiceReview(invoiceId: number) {
  const [invoice, setInvoice] = useState<InvoiceDetail | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [reprocessing, setReprocessing] = useState(false);
  const [pollFailed, setPollFailed] = useState(false);
  const [flashKeys, setFlashKeys] = useState<string[]>([]);
  const mounted = useRef(true);
  const saving = useRef(false);
  const current = useRef<InvoiceDetail | null>(null);
  current.current = invoice;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const load = useCallback(async (): Promise<InvoiceDetail | null> => {
    try {
      const data = await getInvoice(invoiceId);
      if (!mounted.current) return null;
      setInvoice(data);
      setLoadState('ready');
      return data;
    } catch (e) {
      if (!mounted.current) return null;
      const status = (e as { response?: { status?: number } }).response?.status;
      setLoadState((prev) => (prev === 'ready' ? prev : status === 404 ? 'not-found' : 'error'));
      return null;
    }
  }, [invoiceId]);

  useEffect(() => { void load(); }, [load]);

  // ---- 识别中轮询：只在「解析中」或「已上传且还没有结果」时进行，并且有上限
  const status = invoice?.status;
  const hasResults = (invoice?.parsing_diffs?.length ?? 0) > 0;
  const shouldPoll = !reprocessing && !pollFailed
    && (status === '解析中' || (status === '已上传' && !hasResults));

  useEffect(() => {
    if (!shouldPoll) return;
    const started = Date.now();
    let sawProcessing = current.current?.status === '解析中';
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      const data = await load();
      if (cancelled || !mounted.current || !data) return;
      if (data.status === '解析中') {
        sawProcessing = true;
        if (Date.now() - started > PROCESSING_LIMIT_MS) { setPollFailed(true); return; }
      } else if (data.status === '已上传' && (data.parsing_diffs?.length ?? 0) === 0) {
        // 从「解析中」回到「已上传」说明后台重试耗尽，识别失败
        if (sawProcessing || Date.now() - started > QUEUED_LIMIT_MS) { setPollFailed(true); return; }
      } else {
        return;
      }
      timer = window.setTimeout(tick, POLL_MS);
    };
    timer = window.setTimeout(tick, POLL_MS);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [shouldPoll, load]);

  useEffect(() => {
    if (!flashKeys.length) return;
    const t = window.setTimeout(() => setFlashKeys([]), FLASH_MS);
    return () => window.clearTimeout(t);
  }, [flashKeys]);

  /**
   * 确认或修改一个字段。同一时间只允许一个保存在途：每次保存都会在后端重算派生金额、
   * 重写校验结果，并发的话后返回的旧结果会覆盖新结果。返回错误信息，成功返回 null。
   */
  const updateField = useCallback(async (key: string, body: FieldUpdateBody): Promise<string | null> => {
    if (saving.current) return '上一项还在保存，请稍候';
    saving.current = true;
    setSavingKey(key);
    try {
      const next = await updateInvoiceField(invoiceId, key, body);
      if (!mounted.current) return null;
      const before = Object.fromEntries(
        (current.current?.parsing_diffs ?? []).map((d) => [d.field_name, d.final_value]),
      );
      // 被连带重算的字段（例如改金额后的税额与价税合计）短暂高亮
      const recomputed = next.parsing_diffs
        .filter((d) => d.field_name !== key && d.field_name in before && before[d.field_name] !== d.final_value)
        .map((d) => d.field_name);
      setInvoice(next);
      if (recomputed.length) setFlashKeys(recomputed);
      return null;
    } catch (e) {
      return errorDetail(e, '保存失败，请重试');
    } finally {
      saving.current = false;
      if (mounted.current) setSavingKey(null);
    }
  }, [invoiceId]);

  const confirm = useCallback(async (): Promise<boolean> => {
    if (saving.current) return false;
    saving.current = true;
    try {
      await confirmInvoice(invoiceId);
      await load();
      return true;
    } catch (e) {
      message.error(errorDetail(e, '确认失败，请重试'));
      return false;
    } finally {
      saving.current = false;
    }
  }, [invoiceId, load]);

  const reprocess = useCallback(async () => {
    setReprocessing(true);
    setPollFailed(false);
    try {
      await reprocessInvoice(invoiceId);
      message.success('重新识别完成');
    } catch (e) {
      message.error(errorDetail(e, '重新识别失败，请稍后重试'));
    } finally {
      await load();
      if (mounted.current) setReprocessing(false);
    }
  }, [invoiceId, load]);

  const readQr = useCallback(async () => {
    try {
      const result = await retryQr(invoiceId);
      if (result.decoded) {
        message.success(`已从二维码读取 ${Object.keys(result.fields).length} 项信息`);
        await load();
      } else {
        message.info('没能从图片中读出二维码，信息保持不变');
      }
    } catch (e) {
      message.error(errorDetail(e, '读取二维码失败'));
    }
  }, [invoiceId, load]);

  return {
    invoice,
    loadState,
    savingKey,
    reprocessing,
    waiting: shouldPoll,
    pollFailed,
    flashKeys,
    reload: load,
    updateField,
    confirm,
    reprocess,
    readQr,
  };
}

/**
 * 待审核队列的快照（按上传时间倒序，最多 100 张），用于「3/12」计数与确认后自动打开下一张。
 */
export function useReviewQueue(currentId: number) {
  const [ids, setIds] = useState<number[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listInvoices({ status: '待审核', page_size: 100 })
      .then((r) => { if (!cancelled) setIds(r.items.map((i) => i.id)); })
      .catch(() => { if (!cancelled) setIds([]); });
    return () => { cancelled = true; };
  }, [currentId]);

  const index = ids ? ids.indexOf(currentId) : -1;
  const others = ids ? ids.filter((id) => id !== currentId) : [];

  return {
    loaded: ids !== null,
    /** 当前发票在队列中的位置；不在队列中（例如从已确认发票进来）为 -1 */
    index,
    total: ids?.length ?? 0,
    prevId: ids && index > 0 ? ids[index - 1] : null,
    nextId: ids && index >= 0 ? ids[index + 1] ?? null : others[0] ?? null,
    /** 确认当前这张之后该打开的发票：原本排在它后面的那张，最后一张确认后回到队首 */
    nextAfterConfirm: (): number | null => (index >= 0 ? others[index] ?? others[0] : others[0]) ?? null,
  };
}
