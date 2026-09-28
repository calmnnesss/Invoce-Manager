import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import PipelineDiagram from '../components/landing/PipelineDiagram';
import { CODE_URL, COPY, REPO_URL, type Lang } from '../components/landing/copy';
import { useLang } from '../components/landing/useLang';
import styles from './LandingPage.module.css';

const APP_PATH = '/invoices';
const LANGS: { lang: Lang; label: string }[] = [
  { lang: 'zh', label: '中' },
  { lang: 'en', label: 'EN' },
];

/** 文案里的 [[界面路径]] 渲染成标签，`按键` 渲染成键帽 */
function renderInline(text: string): ReactNode[] {
  return text.split(/(\[\[.+?\]\]|`.+?`)/g).filter(Boolean).map((part, i) => {
    if (part.startsWith('[[')) return <span key={i} className={styles.chip}>{part.slice(2, -2)}</span>;
    if (part.startsWith('`')) return <kbd key={i} className={styles.kbd}>{part.slice(1, -1)}</kbd>;
    return part;
  });
}

/**
 * 项目介绍页（作品集首页）。全屏独立页面，不带应用侧栏；只讲终版系统怎么工作，
 * 不展示任何发票样本。文案与出处见 components/landing/copy.ts。
 */
function LandingPage() {
  const [lang, setLang] = useLang();
  const t = COPY[lang];

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <div className={`${styles.container} ${styles.topbarInner}`}>
          <span className={styles.brand}>
            <span className={styles.brandMark} aria-hidden="true" />
            Invoice-Manager
          </span>
          <nav className={styles.topnav}>
            <div className={styles.langSwitch} role="group" aria-label={t.nav.language}>
              {LANGS.map((item, i) => (
                <span key={item.lang} className={styles.langItem}>
                  {i > 0 && <span className={styles.langDivider} aria-hidden="true">/</span>}
                  <button
                    type="button"
                    lang={item.lang}
                    aria-pressed={lang === item.lang}
                    className={lang === item.lang ? styles.langActive : styles.langButton}
                    onClick={() => setLang(item.lang)}
                  >
                    {item.label}
                  </button>
                </span>
              ))}
            </div>
            <a className={styles.navLink} href={REPO_URL} target="_blank" rel="noopener noreferrer">
              {t.nav.github}
            </a>
            <Link className={`${styles.button} ${styles.buttonOutline} ${styles.buttonSmall}`} to={APP_PATH}>
              {t.nav.openApp}
            </Link>
          </nav>
        </div>
      </header>

      <main>
        <section className={styles.hero}>
          <div className={styles.container}>
            <p className={styles.eyebrow}>{t.hero.eyebrow}</p>
            <h1 className={styles.heroTitle}>{t.hero.title}</h1>
            <div className={styles.heroActions}>
              <Link className={`${styles.button} ${styles.buttonPrimary}`} to={APP_PATH}>{t.hero.openApp}</Link>
              <a className={`${styles.button} ${styles.buttonOutline}`} href={REPO_URL} target="_blank" rel="noopener noreferrer">
                {t.hero.github}
              </a>
            </div>
            <a className={styles.heroTry} href="#try">{t.hero.tryIt}</a>
          </div>
        </section>

        <section className={`${styles.section} ${styles.sectionSurface}`} aria-labelledby="how-title">
          <div className={styles.container}>
            <h2 id="how-title" className={styles.sectionTitle}>{t.how.title}</h2>
            <p className={styles.sectionLead}>{t.how.lead}</p>
            <div className={styles.diagram}>
              <PipelineDiagram how={t.how} d={t.diagram} />
            </div>
            <details className={styles.fields}>
              <summary className={styles.fieldsSummary}>{t.how.fieldsSummary}</summary>
              <table className={styles.fieldsTable}>
                <thead>
                  <tr>
                    <th scope="col">{t.how.fieldsHead.field}</th>
                    <th scope="col">{t.how.fieldsHead.source}</th>
                  </tr>
                </thead>
                <tbody>
                  {t.how.fields.map((row) => (
                    <tr key={row.field}>
                      <td>{row.field}</td>
                      <td>{row.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </div>
        </section>

        <section className={styles.section} aria-labelledby="principles-title">
          <div className={styles.container}>
            <h2 id="principles-title" className={styles.sectionTitle}>{t.principles.title}</h2>
            <p className={styles.sectionLead}>{t.principles.lead}</p>
            <div className={styles.principles}>
              {t.principles.items.map((item, i) => (
                <article key={item.title} className={styles.principle}>
                  <span className={styles.principleIndex}>{String(i + 1).padStart(2, '0')}</span>
                  <h3 className={styles.principleTitle}>{item.title}</h3>
                  <p className={styles.principleBody}>{item.body}</p>
                  {i === 1 && (
                    <ol className={styles.layers}>
                      {t.principles.layers.map((layer) => (
                        <li key={layer.name} className={styles.layer}>
                          <span className={styles.layerName}>{layer.name}</span>
                          <span className={styles.layerRule}>{layer.rule}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                  <p className={styles.codeLinks}>
                    <span>{t.principles.viewCode} →</span>
                    {item.files.map((file) => (
                      <a key={file} href={`${CODE_URL}/${file}`} target="_blank" rel="noopener noreferrer">{file}</a>
                    ))}
                  </p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="try" className={styles.section} aria-labelledby="try-title">
          <div className={styles.container}>
            <div className={styles.tryCard}>
              <h2 id="try-title" className={styles.sectionTitle}>{t.tryIt.title}</h2>
              <ol className={styles.trySteps}>
                {t.tryIt.steps.map((step, i) => (
                  <li key={i}>
                    <span className={styles.tryIndex}>{i + 1}</span>
                    <span className={styles.tryText}>{renderInline(step)}</span>
                  </li>
                ))}
              </ol>
              <Link className={`${styles.button} ${styles.buttonPrimary} ${styles.tryAction}`} to={APP_PATH}>
                {t.tryIt.openApp}
              </Link>
              <p className={styles.tryNote}>{t.tryIt.note}</p>
            </div>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={`${styles.container} ${styles.footerInner}`}>
          <span>Invoice-Manager · {t.footer.project}</span>
          <span className={styles.footerStack}>{t.footer.stack}</span>
          <span className={styles.footerLinks}>
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">{t.footer.github}</a>
            <Link to={APP_PATH}>{t.footer.openApp}</Link>
          </span>
        </div>
      </footer>
    </div>
  );
}

export default LandingPage;
