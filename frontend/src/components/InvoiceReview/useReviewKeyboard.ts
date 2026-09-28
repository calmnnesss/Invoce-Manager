import { useEffect, useRef } from 'react';

export interface ReviewKeyHandlers {
  accept: () => void;
  select: (index: number) => void;
  edit: () => void;
  next: () => void;
  prev: () => void;
  confirmInvoice: () => void;
  prevInvoice: () => void;
  nextInvoice: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  fit: () => void;
  rotate: () => void;
  help: () => void;
}

export const SHORTCUTS: { keys: string; action: string }[] = [
  { keys: 'Enter', action: '确认当前项，并跳到下一个待确认项' },
  { keys: '1 – 9', action: '选择对应的候选值' },
  { keys: 'E / F2', action: '修改当前项' },
  { keys: 'Esc', action: '取消修改' },
  { keys: '↑ ↓ / K J', action: '在字段之间移动' },
  { keys: 'Ctrl + Enter', action: '确认整张发票（编辑中会先保存）' },
  { keys: '[ / ]', action: '上一张 / 下一张待审核发票' },
  { keys: '+ / − / 0', action: '原件放大 / 缩小 / 适应宽度' },
  { keys: 'R', action: '原件旋转 90°' },
  { keys: '?', action: '显示本帮助' },
];

/**
 * 核对页的全局快捷键。
 * - 输入框内只处理 Ctrl+Enter，Enter / Esc 由编辑框自己处理
 * - 中文输入法组词中的按键一律忽略
 * - 焦点在按钮、链接等控件上时，Enter 交给控件自己（避免触发两次）
 * - enabled 为 false（弹窗、抽屉、菜单打开，或正在识别）时整体停用
 */
export function useReviewKeyboard(enabled: boolean, handlers: ReviewKeyHandlers) {
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
      const h = ref.current;
      const target = e.target instanceof HTMLElement ? e.target : null;
      const typing = Boolean(target?.closest('input, textarea, select, [contenteditable="true"]'));

      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (typing) return; // 编辑框自己先保存再确认
        e.preventDefault();
        h.confirmInvoice();
        return;
      }
      if (typing || e.altKey || e.ctrlKey || e.metaKey) return;
      const onControl = Boolean(target?.closest('button, a[href], [role="button"], [role="menuitem"]'));

      switch (e.key) {
        case 'Enter':
          if (onControl) return;
          e.preventDefault();
          h.accept();
          return;
        case 'ArrowDown':
        case 'j':
        case 'J':
          e.preventDefault();
          h.next();
          return;
        case 'ArrowUp':
        case 'k':
        case 'K':
          e.preventDefault();
          h.prev();
          return;
        case 'e':
        case 'E':
        case 'F2':
          e.preventDefault();
          h.edit();
          return;
        case '[':
          h.prevInvoice();
          return;
        case ']':
          h.nextInvoice();
          return;
        case '+':
        case '=':
          h.zoomIn();
          return;
        case '-':
          h.zoomOut();
          return;
        case '0':
          h.fit();
          return;
        case 'r':
        case 'R':
          h.rotate();
          return;
        case '?':
          h.help();
          return;
        default:
          if (/^[1-9]$/.test(e.key)) {
            e.preventDefault();
            h.select(Number(e.key) - 1);
          }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled]);
}
