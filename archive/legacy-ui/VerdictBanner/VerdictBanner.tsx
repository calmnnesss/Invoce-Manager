import { Alert, Button, Space, Tag, Tooltip, Typography } from 'antd';
import { QrcodeOutlined } from '@ant-design/icons';
import type { RuleCheck, Verdict } from '../../types/invoice';
import styles from './VerdictBanner.module.css';

const { Text } = Typography;

const VERDICT_META: Record<Verdict, {
  type: 'success' | 'warning' | 'error'; title: string; desc: string;
}> = {
  ok: {
    type: 'success',
    title: '自动通过',
    desc: '全部字段来源可靠，未触发任何校验规则。',
  },
  warn: {
    type: 'warning',
    title: '需要复核',
    desc: '存在待核字段或校验未通过项，逐条确认后即可提交。',
  },
  rejected: {
    type: 'error',
    title: '已拒绝',
    desc: '销售方落在报销主体清单中——同一家公司不可能既是买方又是卖方，相关字段已置空，必须人工填写。',
  },
};

const RULE_LABELS: Record<string, string> = {
  seller_is_entity: '销售方落在主体清单',
  computed_vs_recognized: '计算值与识别值比对',
  tax_id_format: '税号位数与校验位',
  invoice_number_format: '发票代码/号码位数',
  cross_source_mismatch: '多路结果不一致',
};

const DEGRADED_LABELS: Record<string, string> = {
  invoice_code: '发票代码',
  invoice_number: '发票号码',
  issue_date: '开票日期',
  amount: '金额',
  buyer_name: '购买方名称',
  buyer_tax_id: '购买方税号',
  computed_amounts: '税额与价税合计',
};

const QR_FIELDS = ['invoice_code', 'invoice_number', 'issue_date', 'amount'];

interface Props {
  verdict: Verdict | null;
  checks: RuleCheck[] | null;
  degraded: string[] | null;
  pipelineVersion: string | null;
  qrLoading: boolean;
  onRetryQr: () => void;
}

export default function VerdictBanner({
  verdict, checks, degraded, pipelineVersion, qrLoading, onRetryQr,
}: Props) {
  const meta = VERDICT_META[verdict || 'warn'];
  const failed = (checks || []).filter((c) => c.status === 'fail');
  const skipped = (checks || []).filter((c) => c.status === 'skipped');

  // 二维码线路默认不跑，那四个字段必然落在 degraded 里。
  // 据此显示重试入口，不用另外判断线路开关。
  const qrPending = QR_FIELDS.every((f) => (degraded || []).includes(f));
  const otherDegraded = (degraded || []).filter((f) => !QR_FIELDS.includes(f));

  return (
    <div className={styles.wrap}>
      <Alert
        type={meta.type}
        showIcon
        message={
          <Space size={8}>
            <span>{meta.title}</span>
            {pipelineVersion && <Tag>{pipelineVersion} 管线</Tag>}
          </Space>
        }
        description={
          <div className={styles.body}>
            <div>{meta.desc}</div>

            {failed.length > 0 && (
              <div className={styles.rules}>
                {failed.map((c) => (
                  <div key={c.rule_id} className={styles.rule}>
                    <Tag color="error">{RULE_LABELS[c.rule_id] || c.rule_id}</Tag>
                    {/* detail 里带完整算式，人工复核时不用回头翻发票 */}
                    <Text type="secondary" className={styles.detail}>{c.detail}</Text>
                  </div>
                ))}
              </div>
            )}

            {skipped.length > 0 && (
              <div className={styles.skipped}>
                <Text type="secondary">
                  因字段缺失未能校验：
                  {skipped.map((c) => RULE_LABELS[c.rule_id] || c.rule_id).join('、')}
                </Text>
              </div>
            )}

            {otherDegraded.length > 0 && (
              <div className={styles.skipped}>
                <Text type="secondary">
                  走了降级路径：
                  {otherDegraded.map((f) => DEGRADED_LABELS[f] || f).join('、')}
                </Text>
              </div>
            )}
          </div>
        }
      />

      {qrPending && (
        <Alert
          className={styles.qr}
          type="info"
          showIcon
          icon={<QrcodeOutlined />}
          message="二维码线路未启用"
          description={
            <div className={styles.body}>
              发票代码、号码、开票日期、金额目前取自视觉模型。
              二维码是确定性来源，解出后这四项不再依赖识别。
            </div>
          }
          action={
            <Tooltip title="本地解码，不消耗模型额度">
              <Button size="small" loading={qrLoading} onClick={onRetryQr}>
                用二维码重试
              </Button>
            </Tooltip>
          }
        />
      )}
    </div>
  );
}
