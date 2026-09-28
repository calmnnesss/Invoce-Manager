import type { Copy } from './copy';
import { RULE_IDS } from './copy';
import styles from './PipelineDiagram.module.css';

interface PipelineDiagramProps {
  how: Copy['how'];
  d: Copy['diagram'];
}

/*
 * 画布 1080 × 364，横向：上传 | 01 识别 | 02 融合 | 03 校验 | 已确认 / 04 人工核对。
 * 下方说明按同样的比例分栏（CAPTION_COLUMNS），与节点对齐。
 */
const NODE_TOP = 64;
const NODE_BOTTOM = 280;
const MID = 172;
const READ = { x: 100, w: 216 };
const FUSE = { x: 340, w: 216 };
const CHECK = { x: 580, w: 216 };
const OUT = { x: 860, w: 220 };
const REVIEW_TOP = 148;
const CHECK_ROWS = [134, 160, 186, 212, 238];

const center = (n: { x: number; w: number }) => n.x + n.w / 2;
const right = (n: { x: number; w: number }) => n.x + n.w;

/** 处理流程图：宽屏是 SVG，窄屏换成内容相同的有序列表 */
export default function PipelineDiagram({ how, d }: PipelineDiagramProps) {
  const [read, fuse, check, review] = how.steps;

  return (
    <div className={styles.root}>
      <div className={styles.desktop}>
        <svg
          className={styles.svg}
          viewBox="0 0 1080 364"
          role="img"
          aria-labelledby="pipeline-title pipeline-desc"
        >
          <title id="pipeline-title">{d.title}</title>
          <desc id="pipeline-desc">{d.desc}</desc>
          <defs>
            {(['ink', 'gray', 'red'] as const).map((tone) => (
              <marker
                key={tone}
                id={`pd-arrow-${tone}`}
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="8"
                markerHeight="8"
                markerUnits="userSpaceOnUse"
                orient="auto"
              >
                <path d="M0 0 L7 4 L0 8" className={styles[`arrow_${tone}`]} />
              </marker>
            ))}
          </defs>

          {/* 设置：清单进 prompt 与查表，规则进融合与校验 */}
          <rect x={READ.x} y={0} width={right(CHECK) - READ.x} height={28} rx={4} className={styles.settings} />
          <text x={center({ x: READ.x, w: right(CHECK) - READ.x })} y={18} textAnchor="middle" className={`${styles.text} ${styles.muted}`}>
            {d.settings}
          </text>
          {[READ, FUSE, CHECK].map((n) => (
            <line key={n.x} x1={center(n)} y1={28} x2={center(n)} y2={NODE_TOP - 1}
              className={styles.drop} markerEnd="url(#pd-arrow-gray)" />
          ))}

          {/* 上传 */}
          <rect x={0} y={MID - 16} width={80} height={32} rx={16} className={styles.box} />
          <text x={40} y={MID + 5} textAnchor="middle" className={styles.label}>{d.upload}</text>
          <text x={40} y={MID + 32} textAnchor="middle" className={`${styles.tiny} ${styles.muted}`}>{d.formats}</text>
          <line x1={80} y1={MID} x2={READ.x - 1} y2={MID} className={styles.edge} markerEnd="url(#pd-arrow-ink)" />

          {/* 01 识别 */}
          <rect x={READ.x} y={NODE_TOP} width={READ.w} height={NODE_BOTTOM - NODE_TOP} rx={6} className={styles.box} />
          <text x={READ.x + 16} y={88} className={styles.title}>{read.title}</text>
          <rect x={READ.x + 16} y={102} width={184} height={30} rx={4} className={styles.inner} />
          <text x={READ.x + 26} y={121} className={styles.text}>{d.ocr}</text>
          <rect x={READ.x + 16} y={140} width={184} height={46} rx={4} className={styles.inner} />
          <text x={READ.x + 26} y={159} className={styles.text}>{d.vlm}</text>
          <text x={READ.x + 26} y={176} className={`${styles.small} ${styles.muted}`}>{d.vlmNote}</text>
          <text x={READ.x + 16} y={204} className={`${styles.tiny} ${styles.muted}`}>{d.parallel}</text>
          <rect x={READ.x + 16} y={216} width={184} height={30} rx={4} className={styles.optional} />
          <text x={READ.x + 26} y={235} className={`${styles.text} ${styles.muted}`}>{d.qr}</text>
          <line x1={right(READ)} y1={MID} x2={FUSE.x - 1} y2={MID} className={styles.edge} markerEnd="url(#pd-arrow-ink)" />

          {/* 02 融合 */}
          <rect x={FUSE.x} y={NODE_TOP} width={FUSE.w} height={NODE_BOTTOM - NODE_TOP} rx={6} className={styles.box} />
          <text x={FUSE.x + 16} y={88} className={styles.title}>{fuse.title}</text>
          <text x={FUSE.x + 16} y={106} className={`${styles.small} ${styles.muted}`}>{d.fuseNote}</text>
          <text x={FUSE.x + 16} y={128} className={`${styles.tiny} ${styles.muted}`}>{d.confidence}</text>
          {([
            [d.levels.certain, 0, 0, false],
            [d.levels.high, 1, 0, false],
            [d.levels.low, 0, 1, true],
            [d.levels.missing, 1, 1, false],
          ] as const).map(([label, col, row, low]) => (
            <g key={label}>
              <rect x={FUSE.x + 16 + col * 96} y={136 + row * 28} width={88} height={22} rx={11}
                className={low ? styles.pillLow : styles.pill} />
              <text x={FUSE.x + 16 + col * 96 + 44} y={151 + row * 28} textAnchor="middle"
                className={`${styles.small} ${low ? styles.red : ''}`}>{label}</text>
            </g>
          ))}
          <rect x={FUSE.x + 16} y={200} width={184} height={28} rx={4} className={styles.inner} />
          <text x={FUSE.x + 26} y={218} className={styles.text}>{d.buyer}</text>
          <rect x={FUSE.x + 16} y={236} width={184} height={28} rx={4} className={styles.inner} />
          <text x={FUSE.x + 26} y={254} className={styles.text}>{d.amounts}</text>
          <line x1={right(FUSE)} y1={MID} x2={CHECK.x - 1} y2={MID} className={styles.edge} markerEnd="url(#pd-arrow-ink)" />

          {/* 03 校验：悬停显示规则 id */}
          <rect x={CHECK.x} y={NODE_TOP} width={CHECK.w} height={NODE_BOTTOM - NODE_TOP} rx={6} className={styles.box} />
          <text x={CHECK.x + 16} y={88} className={styles.title}>{check.title}</text>
          <text x={CHECK.x + 16} y={106} className={`${styles.small} ${styles.muted}`}>{d.checkNote}</text>
          {d.checks.map((label, i) => (
            <g key={RULE_IDS[i]}>
              <title>{RULE_IDS[i]}</title>
              <rect x={CHECK.x + 16} y={CHECK_ROWS[i] - 8} width={6} height={6} className={styles.bullet} />
              <text x={CHECK.x + 30} y={CHECK_ROWS[i]} className={styles.text}>{label}</text>
            </g>
          ))}

          {/* 全部通过 → 已确认（细线：项目名称固定人工确认，实际很少走这条） */}
          <line x1={right(CHECK)} y1={80} x2={OUT.x - 1} y2={80} className={styles.edgeGray} markerEnd="url(#pd-arrow-gray)" />
          <text x={(right(CHECK) + OUT.x) / 2} y={72} textAnchor="middle" className={`${styles.tiny} ${styles.muted}`}>{d.pass}</text>
          <rect x={OUT.x} y={NODE_TOP} width={OUT.w} height={32} rx={16} className={styles.box} />
          <text x={center(OUT)} y={NODE_TOP + 21} textAnchor="middle" className={styles.label}>{d.confirmed}</text>

          {/* 警告 / 拒绝 → 04 人工核对 */}
          <line x1={right(CHECK)} y1={228} x2={OUT.x - 1} y2={228} className={styles.edgeRed} markerEnd="url(#pd-arrow-red)" />
          <text x={(right(CHECK) + OUT.x) / 2} y={206} textAnchor="middle" className={`${styles.tiny} ${styles.red}`}>{d.flagged[0]}</text>
          <text x={(right(CHECK) + OUT.x) / 2} y={219} textAnchor="middle" className={`${styles.tiny} ${styles.red}`}>{d.flagged[1]}</text>
          <rect x={OUT.x} y={REVIEW_TOP} width={OUT.w} height={NODE_BOTTOM - REVIEW_TOP} rx={6} className={styles.boxReview} />
          <text x={OUT.x + 16} y={REVIEW_TOP + 24} className={styles.title}>{review.title}</text>
          <text x={OUT.x + 16} y={REVIEW_TOP + 46} className={`${styles.small} ${styles.muted}`}>{d.reviewNotes[0]}</text>
          <text x={OUT.x + 16} y={REVIEW_TOP + 64} className={`${styles.small} ${styles.muted}`}>{d.reviewNotes[1]}</text>
          <line x1={center(OUT)} y1={REVIEW_TOP} x2={center(OUT)} y2={NODE_TOP + 33} className={styles.edgeGray} markerEnd="url(#pd-arrow-gray)" />
          <text x={center(OUT) + 8} y={128} className={`${styles.tiny} ${styles.muted}`}>{d.confirm}</text>

          {/* 修改后回到校验 */}
          <path
            d={`M ${center(OUT)} ${NODE_BOTTOM} C ${center(OUT)} 332, ${center(CHECK)} 332, ${center(CHECK)} ${NODE_BOTTOM + 1}`}
            className={styles.loop}
            markerEnd="url(#pd-arrow-red)"
          />
          <text x={(center(OUT) + center(CHECK)) / 2} y={354} textAnchor="middle" className={`${styles.small} ${styles.muted}`}>
            {d.loop}
          </text>
        </svg>

        <div className={styles.captions}>
          {how.steps.map((step, i) => (
            <p key={step.title} className={styles.caption} style={{ gridColumn: 2 + i * 2 }}>
              <span className={styles.captionIndex}>{String(i + 1).padStart(2, '0')}</span>
              {step.body}
            </p>
          ))}
        </div>
      </div>

      {/* 窄屏：同样的内容排成有序列表 */}
      <div className={styles.mobile}>
        <p className={styles.note}>{d.settingsNote}</p>
        <ol className={styles.stages}>
          <li className={styles.stage}>
            <h3 className={styles.stageTitle}>{read.title}</h3>
            <p className={styles.stageBody}>{read.body}</p>
            <ul className={styles.stageList}>
              <li>{d.ocr}</li>
              <li>{d.vlm} — {d.vlmNote}</li>
              <li className={styles.mutedItem}>{d.qr}</li>
            </ul>
          </li>
          <li className={styles.stage}>
            <h3 className={styles.stageTitle}>{fuse.title}</h3>
            <p className={styles.stageBody}>{fuse.body}</p>
            <div className={styles.pills} aria-label={d.confidence}>
              {[d.levels.certain, d.levels.high, d.levels.low, d.levels.missing].map((label) => (
                <span key={label} className={label === d.levels.low ? styles.pillTagLow : styles.pillTag}>{label}</span>
              ))}
            </div>
          </li>
          <li className={styles.stage}>
            <h3 className={styles.stageTitle}>{check.title}</h3>
            <p className={styles.stageBody}>{check.body}</p>
            <ol className={styles.checkList}>
              {d.checks.map((label, i) => (
                <li key={RULE_IDS[i]}>
                  {label}<code className={styles.ruleId}>{RULE_IDS[i]}</code>
                </li>
              ))}
            </ol>
            <p className={styles.verdict}>{d.pass} → {d.confirmed}</p>
            <p className={styles.verdict}>{d.flagged.join(' / ')} → {review.title}</p>
          </li>
          <li className={`${styles.stage} ${styles.stageReview}`}>
            <h3 className={styles.stageTitle}>{review.title}</h3>
            <p className={styles.stageBody}>{review.body}</p>
            <p className={styles.loopRow}>↺ {d.loop}</p>
          </li>
        </ol>
      </div>
    </div>
  );
}
