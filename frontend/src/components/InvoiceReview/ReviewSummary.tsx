import { CheckOutlined, ExclamationOutlined, MinusOutlined } from '@ant-design/icons';
import type { Pill, ReviewModel } from './reviewModel';
import styles from './ReviewSummary.module.css';

interface Props {
  model: ReviewModel;
  onFocusField: (key: string) => void;
}

function Sparkle() {
  return (
    <svg className={styles.sparkle} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2c.6 4.9 2.9 7.8 8 9.2v1.6c-5.1 1.4-7.4 4.3-8 9.2h-1.6c-.6-4.9-2.9-7.8-8-9.2v-1.6c5.1-1.4 7.4-4.3 8-9.2H12Z" />
    </svg>
  );
}

const PILL_ICON: Record<Pill['tone'], JSX.Element> = {
  pass: <CheckOutlined />,
  warn: <ExclamationOutlined />,
  fail: <ExclamationOutlined />,
  muted: <MinusOutlined />,
};

/** 顶部摘要：一句话结论 + 进度 + 自动核验结果 */
export default function ReviewSummary({ model, onFocusField }: Props) {
  const percent = model.total ? Math.round((model.handled / model.total) * 100) : 0;
  const showProgress = model.hasResults && !model.processing;

  return (
    <section className={`${styles.summary} ${styles[model.tone]}`} aria-live="polite">
      <div className={styles.headlineRow}>
        <Sparkle />
        <p className={styles.headline}>{model.headline}</p>
      </div>

      {showProgress && (
        <div className={styles.progressRow}>
          <div className={styles.track} role="progressbar" aria-valuenow={model.handled}
            aria-valuemin={0} aria-valuemax={model.total} aria-label="核对进度">
            <div className={styles.bar} style={{ width: `${percent}%` }} />
          </div>
          <span className={styles.count}>{model.handled}/{model.total}</span>
        </div>
      )}

      {model.pills.length > 0 && (
        <ul className={styles.pills} aria-label="自动核验结果">
          {model.pills.map((p, i) => (
            <li key={p.id} style={{ '--i': i } as React.CSSProperties}>
              {p.focusKey ? (
                <button type="button" className={`${styles.pill} ${styles[p.tone]}`}
                  onClick={() => onFocusField(p.focusKey!)}>
                  {PILL_ICON[p.tone]}
                  <span>{p.text}</span>
                </button>
              ) : (
                <span className={`${styles.pill} ${styles[p.tone]}`}>
                  {PILL_ICON[p.tone]}
                  <span>{p.text}</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
