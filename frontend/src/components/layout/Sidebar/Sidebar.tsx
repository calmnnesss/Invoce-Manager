import { Link, useLocation } from 'react-router-dom';
import { Tooltip } from 'antd';
import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import styles from './Sidebar.module.css';

interface NavItem {
  path: string;
  label: string;
}

interface SidebarProps {
  llmConfigured: boolean | null;
}

const SETTINGS_PATH = '/settings';

const navItems: NavItem[] = [
  { path: '/', label: '发票列表' },
  { path: '/upload', label: '上传发票' },
  { path: SETTINGS_PATH, label: '设置' },
];

export default function Sidebar({ llmConfigured }: SidebarProps) {
  const location = useLocation();

  // 设置项后面带模型服务的配置状态
  const settingsStatus = llmConfigured === false ? (
    <ExclamationCircleOutlined style={{ marginLeft: 8, color: 'var(--warning)', fontSize: 12 }} />
  ) : llmConfigured === true ? (
    <CheckCircleOutlined style={{ marginLeft: 8, color: 'var(--success)', fontSize: 12 }} />
  ) : null;

  return (
    <aside className={styles.sidebar}>
      {/* Top Section: Logo + Navigation */}
      <div className={styles.sidebarTop}>
        {/* Logo */}
        <div className={styles.logo}>
          <div className={styles.logoMark} />
          <span className={styles.logoText}>发票管理</span>
        </div>

        {/* Navigation */}
        <nav className={styles.navigation}>
          {navItems.map((item) => {
            const isActive = location.pathname === item.path;
            const isSettings = item.path === SETTINGS_PATH;
            const link = (
              <Link
                key={item.path}
                to={item.path}
                className={`${styles.navItem} ${isActive ? styles.active : ''}`}
                aria-current={isActive ? 'page' : undefined}
              >
                <span className={styles.navDot} />
                <span className={styles.navLabel}>
                  {item.label}
                  {isSettings && settingsStatus}
                </span>
              </Link>
            );
            return isSettings && llmConfigured !== null ? (
              <Tooltip
                key={item.path}
                title={llmConfigured ? '模型服务已配置' : '模型服务未配置，点击前往设置'}
                placement="right"
              >
                {link}
              </Tooltip>
            ) : link;
          })}
        </nav>
      </div>

      {/* Bottom Section: User Profile */}
      <div className={styles.sidebarBottom}>
        <div className={styles.userProfile}>
          <div className={styles.userAvatar}>
            <span className={styles.userInitial}>S</span>
          </div>
          <div className={styles.userInfo}>
            <span className={styles.userName}>Stone</span>
            <span className={styles.userRole}>管理员</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
