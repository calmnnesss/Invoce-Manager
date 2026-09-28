import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState,
} from 'react';
import { Button, Tooltip } from 'antd';
import {
  ColumnWidthOutlined, DownloadOutlined, ExpandOutlined, RotateRightOutlined,
  ZoomInOutlined, ZoomOutOutlined,
} from '@ant-design/icons';
import styles from './DocumentViewer.module.css';

export interface DocumentViewerHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  fit: () => void;
  rotate: () => void;
}

interface Props {
  invoiceId: number;
  src: string;
  downloadUrl: string;
}

type FitMode = 'width' | 'page' | null;

const MIN_SCALE = 0.1;
const MAX_SCALE = 8;
const STEP = 1.25;
const PADDING = 24;

// 评测集里 51 张有 44 张是侧着存的：默认沿用上次用过的旋转角度，每张票可以单独调整
const LAST_ROTATION_KEY = 'invoiceViewer.lastRotation';
const rotationKey = (id: number) => `invoiceViewer.rotation.${id}`;

function readRotation(id: number): number {
  try {
    const own = localStorage.getItem(rotationKey(id));
    const last = localStorage.getItem(LAST_ROTATION_KEY);
    const v = Number(own ?? last ?? 0);
    return [0, 90, 180, 270].includes(v) ? v : 0;
  } catch {
    return 0;
  }
}

function saveRotation(id: number, deg: number) {
  try {
    localStorage.setItem(rotationKey(id), String(deg));
    localStorage.setItem(LAST_ROTATION_KEY, String(deg));
  } catch {
    // 存不了只是下次不记得角度，不影响使用
  }
}

const clamp = (v: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, v));

/**
 * 原件查看器：适应宽度 / 适应页面、缩放、拖拽平移、90° 旋转。
 *
 * 结构上是「图片坐标系的舞台（stage）+ 叠加层（overlay）」：舞台按图片原始像素比例
 * 缩放和旋转，叠加层与图片共用这套坐标。二期的字段框直接画进 overlay 即可，
 * 缩放旋转会自动跟随。
 */
const DocumentViewer = forwardRef<DocumentViewerHandle, Props>(({ invoiceId, src, downloadUrl }, ref) => {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const [rotation, setRotation] = useState(() => readRotation(invoiceId));
  const [scale, setScale] = useState(1);
  const [fitMode, setFitMode] = useState<FitMode>('width');
  // Ctrl+滚轮缩放时保持光标下的那一点不动：渲染后再修正滚动位置
  const pendingScroll = useRef<{ left: number; top: number } | null>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  const turned = rotation % 180 !== 0;
  const boxW = natural ? (turned ? natural.h : natural.w) : 0;
  const boxH = natural ? (turned ? natural.w : natural.h) : 0;

  const fitScale = useCallback((mode: Exclude<FitMode, null>) => {
    const vp = viewportRef.current;
    if (!vp || !boxW || !boxH) return 1;
    const availW = Math.max(vp.clientWidth - PADDING * 2, 50);
    const availH = Math.max(vp.clientHeight - PADDING * 2, 50);
    return clamp(mode === 'width' ? availW / boxW : Math.min(availW / boxW, availH / boxH));
  }, [boxW, boxH]);

  // 适应模式下，窗口或旋转变化时重新适应
  useLayoutEffect(() => {
    if (!fitMode || !natural) return;
    setScale(fitScale(fitMode));
    const vp = viewportRef.current;
    if (!vp) return;
    const ro = new ResizeObserver(() => setScale(fitScale(fitMode)));
    ro.observe(vp);
    return () => ro.disconnect();
  }, [fitMode, fitScale, natural]);

  useLayoutEffect(() => {
    const vp = viewportRef.current;
    if (vp && pendingScroll.current) {
      vp.scrollLeft = pendingScroll.current.left;
      vp.scrollTop = pendingScroll.current.top;
      pendingScroll.current = null;
    }
  }, [scale]);

  const zoomTo = useCallback((next: number, anchor?: { x: number; y: number }) => {
    const vp = viewportRef.current;
    const target = clamp(next);
    setFitMode(null);
    if (vp) {
      const ax = anchor?.x ?? vp.clientWidth / 2;
      const ay = anchor?.y ?? vp.clientHeight / 2;
      const ratio = target / scale;
      pendingScroll.current = {
        left: (vp.scrollLeft + ax) * ratio - ax,
        top: (vp.scrollTop + ay) * ratio - ay,
      };
    }
    setScale(target);
  }, [scale]);

  const rotate = useCallback(() => {
    setRotation((r) => {
      const next = (r + 90) % 360;
      saveRotation(invoiceId, next);
      return next;
    });
    setFitMode((m) => m ?? 'width');
  }, [invoiceId]);

  useImperativeHandle(ref, () => ({
    zoomIn: () => zoomTo(scale * STEP),
    zoomOut: () => zoomTo(scale / STEP),
    fit: () => setFitMode('width'),
    rotate,
  }), [zoomTo, scale, rotate]);

  // React 的 onWheel 是 passive 的，preventDefault 无效，会连带整个浏览器页面一起缩放
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const rect = vp.getBoundingClientRect();
      zoomTo(scale * (e.deltaY < 0 ? STEP : 1 / STEP), { x: e.clientX - rect.left, y: e.clientY - rect.top });
    };
    vp.addEventListener('wheel', onWheel, { passive: false });
    return () => vp.removeEventListener('wheel', onWheel);
  }, [zoomTo, scale]);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const vp = viewportRef.current;
    if (!vp || e.button !== 0) return;
    if (vp.scrollWidth <= vp.clientWidth && vp.scrollHeight <= vp.clientHeight) return;
    drag.current = { x: e.clientX, y: e.clientY, left: vp.scrollLeft, top: vp.scrollTop };
    vp.setPointerCapture(e.pointerId);
    setDragging(true);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const vp = viewportRef.current;
    if (!vp || !drag.current) return;
    vp.scrollLeft = drag.current.left - (e.clientX - drag.current.x);
    vp.scrollTop = drag.current.top - (e.clientY - drag.current.y);
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    viewportRef.current?.releasePointerCapture(e.pointerId);
  };

  const stageW = natural ? natural.w * scale : 0;
  const stageH = natural ? natural.h * scale : 0;

  return (
    <div className={styles.viewer}>
      <div className={styles.toolbar} role="toolbar" aria-label="原件查看工具">
        <Tooltip title="适应宽度">
          <Button type="text" size="small" aria-label="适应宽度" icon={<ColumnWidthOutlined />}
            className={fitMode === 'width' ? styles.toolActive : undefined}
            onClick={() => setFitMode('width')} />
        </Tooltip>
        <Tooltip title="适应页面">
          <Button type="text" size="small" aria-label="适应页面" icon={<ExpandOutlined />}
            className={fitMode === 'page' ? styles.toolActive : undefined}
            onClick={() => setFitMode('page')} />
        </Tooltip>
        <span className={styles.divider} />
        <Tooltip title="缩小（−）">
          <Button type="text" size="small" aria-label="缩小" icon={<ZoomOutOutlined />}
            onClick={() => zoomTo(scale / STEP)} />
        </Tooltip>
        <span className={styles.zoomValue}>{Math.round(scale * 100)}%</span>
        <Tooltip title="放大（+），也可按住 Ctrl 滚动滚轮">
          <Button type="text" size="small" aria-label="放大" icon={<ZoomInOutlined />}
            onClick={() => zoomTo(scale * STEP)} />
        </Tooltip>
        <span className={styles.divider} />
        <Tooltip title="旋转 90°（R）">
          <Button type="text" size="small" aria-label="旋转" icon={<RotateRightOutlined />} onClick={rotate} />
        </Tooltip>
        <span className={styles.spacer} />
        <Tooltip title="下载原件">
          <Button type="text" size="small" aria-label="下载原件" icon={<DownloadOutlined />}
            href={downloadUrl} download />
        </Tooltip>
      </div>

      <div
        ref={viewportRef}
        className={`${styles.viewport} ${dragging ? styles.dragging : ''}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {failed ? (
          <div className={styles.state}>
            原件预览加载失败，<a href={downloadUrl} download>下载原件</a>查看
          </div>
        ) : (
          <>
            {!natural && <div className={`${styles.state} ${styles.loading}`}>正在加载原件…</div>}
            <div
              className={styles.canvas}
              style={{ width: boxW * scale + PADDING * 2, height: boxH * scale + PADDING * 2 }}
            >
              <div
                className={styles.stage}
                style={{
                  width: stageW,
                  height: stageH,
                  transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
                  visibility: natural ? 'visible' : 'hidden',
                }}
              >
                <img
                  src={src}
                  alt="发票原件"
                  draggable={false}
                  className={styles.image}
                  onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                  onError={() => setFailed(true)}
                />
                {/* 二期：字段位置框画在这一层，坐标即图片原始像素坐标 */}
                <div className={styles.overlay} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
});

DocumentViewer.displayName = 'DocumentViewer';

export default DocumentViewer;
