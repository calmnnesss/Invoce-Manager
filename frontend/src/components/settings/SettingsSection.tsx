import type { ReactNode } from 'react';
import { Button, Popconfirm } from 'antd';
import styles from './SettingsSection.module.css';

interface SettingsSectionProps {
  id: string;
  title: string;
  description?: ReactNode;
  /** 标题右侧，例如状态标签 */
  extra?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

/** 设置页的一个分区：卡片样式与上传页一致 */
export default function SettingsSection({
  id, title, description, extra, footer, children,
}: SettingsSectionProps) {
  return (
    <section id={id} className={styles.section} aria-labelledby={`${id}-title`}>
      <header className={styles.header}>
        <div className={styles.heading}>
          <h2 id={`${id}-title`} className={styles.title}>{title}</h2>
          {description && <p className={styles.description}>{description}</p>}
        </div>
        {extra && <div className={styles.extra}>{extra}</div>}
      </header>
      <div className={styles.body}>{children}</div>
      {footer && <footer className={styles.footer}>{footer}</footer>}
    </section>
  );
}

interface SaveBarProps {
  dirty: boolean;
  saving: boolean;
  /** 没有改动时左侧显示的说明，例如「共 4 个主体」 */
  summary?: ReactNode;
  /** 草稿已经是出厂值时禁用「恢复默认」 */
  atDefaults: boolean;
  onRestoreDefaults: () => void;
  onDiscard: () => void;
  onSave: () => void;
}

/** 分区底部的保存条。「恢复默认」只改草稿，仍要点保存才生效。 */
export function SaveBar({
  dirty, saving, summary, atDefaults, onRestoreDefaults, onDiscard, onSave,
}: SaveBarProps) {
  return (
    <>
      <span className={dirty ? styles.dirtyHint : styles.summary} role="status">
        {dirty ? '有未保存的修改' : summary}
      </span>
      <div className={styles.actions}>
        <Popconfirm
          title="恢复为出厂设置？"
          description="只会填入表单，点「保存修改」后才生效。"
          okText="恢复"
          cancelText="取消"
          onConfirm={onRestoreDefaults}
          disabled={atDefaults || saving}
        >
          <Button disabled={atDefaults || saving}>恢复默认</Button>
        </Popconfirm>
        {dirty && <Button onClick={onDiscard} disabled={saving}>放弃修改</Button>}
        <Button type="primary" onClick={onSave} loading={saving} disabled={!dirty}>
          保存修改
        </Button>
      </div>
    </>
  );
}
