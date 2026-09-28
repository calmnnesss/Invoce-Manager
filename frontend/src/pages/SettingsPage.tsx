import { useEffect, useState } from 'react';
import { Alert, Button, Skeleton } from 'antd';
import AmountRulesSection from '../components/settings/AmountRulesSection';
import BuyerEntitiesSection from '../components/settings/BuyerEntitiesSection';
import ModelServiceSection from '../components/settings/ModelServiceSection';
import { getReviewSettings } from '../services/api';
import type { AmountRules, BuyerEntity, ReviewSettingsValues } from '../types/invoice';
import styles from './SettingsPage.module.css';

interface SettingsPageProps {
  onLLMConfigured?: () => void;
}

function SettingsPage({ onLLMConfigured }: SettingsPageProps) {
  // 主体清单与金额规则各自保存、分开存：保存其中一个不会把另一个未保存的草稿冲掉
  const [entities, setEntities] = useState<BuyerEntity[] | null>(null);
  const [rules, setRules] = useState<AmountRules | null>(null);
  const [defaults, setDefaults] = useState<ReviewSettingsValues | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = async () => {
    setLoadFailed(false);
    try {
      const data = await getReviewSettings();
      setEntities(data.buyer_entities);
      setRules(data.amount_rules);
      setDefaults(data.defaults);
    } catch (error) {
      console.error('Failed to load review settings:', error);
      setLoadFailed(true);
    }
  };

  useEffect(() => { load(); }, []);

  let review;
  if (entities && rules && defaults) {
    review = (
      <>
        <BuyerEntitiesSection
          saved={entities}
          defaults={defaults.buyer_entities}
          onSaved={(s) => setEntities(s.buyer_entities)}
        />
        <AmountRulesSection
          saved={rules}
          defaults={defaults.amount_rules}
          onSaved={(s) => setRules(s.amount_rules)}
        />
      </>
    );
  } else if (loadFailed) {
    review = (
      <Alert
        type="error"
        showIcon
        message="读取购买方主体与金额校验规则失败"
        description="请确认后端服务已启动后重试。"
        action={<Button size="small" onClick={load}>重试</Button>}
      />
    );
  } else {
    review = (
      <div className={styles.placeholder}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </div>
    );
  }

  return (
    <div className={styles.pageContainer}>
      <header className={styles.pageHeader}>
        <div className={styles.headerContent}>
          <h1 className={styles.pageTitle}>设置</h1>
          <p className={styles.pageSubtitle}>识别模型、购买方主体与金额校验规则</p>
        </div>
      </header>

      <div className={styles.sections}>
        <ModelServiceSection onConfigured={onLLMConfigured} />
        {review}
      </div>
    </div>
  );
}

export default SettingsPage;
