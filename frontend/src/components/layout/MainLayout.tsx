import { ReactNode } from 'react';
import { matchPath, useLocation, useNavigate } from 'react-router-dom';
import { Alert, Button } from 'antd';
import { RocketOutlined } from '@ant-design/icons';
import Sidebar from './Sidebar';
import styles from './MainLayout.module.css';

interface MainLayoutProps {
  children: ReactNode;
  llmConfigured: boolean | null;
  showLlmPromo: boolean;
  onCloseLLMPromo: () => void;
}

export default function MainLayout({
  children,
  llmConfigured,
  showLlmPromo,
  onCloseLLMPromo,
}: MainLayoutProps) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // 发票核对页是全高工作台（原件与核对面板各自滚动）：去掉内容区留白和页脚
  const fullBleed = matchPath('/invoices/:id', pathname) !== null;
  // 设置页自己会显示模型服务的配置状态，不再重复提示
  const onSettings = pathname === '/settings';
  const openSettings = () => navigate('/settings');

  return (
    <div className={styles.layoutContainer}>
      <Sidebar llmConfigured={llmConfigured} />

      <main className={`${styles.mainContent} ${fullBleed ? styles.mainFullBleed : ''}`}>
        <div className={`${styles.contentWrapper} ${fullBleed ? styles.fullBleed : ''}`}>
          {/* LLM Configuration Promo Banner */}
          {showLlmPromo && !llmConfigured && !onSettings && (
            <Alert
              className={fullBleed ? styles.llmPromoBanner : styles.llmPromoAlert}
              message={fullBleed ? '尚未配置模型服务，无法自动识别发票' : '尚未配置模型服务'}
              description={fullBleed ? undefined : (
                <span>
                  未配置模型服务时无法自动识别发票。文字识别需要通义千问（DashScope）API Key，配置后即可自动识别并交叉核验。
                  <Button
                    type="link"
                    icon={<RocketOutlined />}
                    onClick={openSettings}
                    style={{ padding: '0 4px' }}
                  >
                    立即配置
                  </Button>
                </span>
              )}
              action={fullBleed ? (
                <Button type="link" size="small" onClick={openSettings}>立即配置</Button>
              ) : undefined}
              banner={fullBleed}
              type="info"
              showIcon
              closable
              onClose={onCloseLLMPromo}
            />
          )}

          {children}
        </div>

        {!fullBleed && (
          <footer className={styles.footer}>
            发票管理系统 ©2025 by Stometaverse.
          </footer>
        )}
      </main>
    </div>
  );
}
