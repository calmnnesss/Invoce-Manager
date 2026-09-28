import { useState } from 'react';
import { Button, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CheckCircleOutlined, WarningOutlined } from '@ant-design/icons';
import type { Confidence, FieldSource, ParsingDiff } from '../../types/invoice';
import styles from './FieldComparison.module.css';

const { Text } = Typography;

const FIELD_LABELS: Record<string, string> = {
  invoice_code: '发票代码',
  invoice_number: '发票号码',
  issue_date: '开票日期',
  buyer_name: '购买方名称',
  buyer_tax_id: '购买方税号',
  seller_name: '销售方名称',
  seller_tax_id: '销售方税号',
  item_name: '项目名称',
  amount: '金额（不含税）',
  tax_rate: '税率',
  tax_amount: '税额',
  total_with_tax: '价税合计',
};

// 来源不只是"哪个模型说的"——lookup 与 computed 根本不来自模型，
// 所以徽标要把这层区别讲清楚，否则用户会以为所有值都是识别出来的。
const SOURCE_META: Record<FieldSource, { label: string; color: string; hint: string }> = {
  qr: { label: '二维码', color: 'cyan', hint: '从发票二维码解出，确定性来源，不存在识别误差' },
  ocr: { label: 'OCR', color: 'purple', hint: 'qwen3.5-ocr 结构化抽取' },
  vlm: { label: '视觉模型', color: 'volcano', hint: 'qwen-vl-max 视觉理解' },
  lookup: { label: '查表', color: 'green', hint: '按报销主体清单回填，不依赖识别' },
  computed: { label: '计算', color: 'green', hint: '按勾稽公式算出，不依赖识别' },
  manual: { label: '人工', color: 'blue', hint: '人工填写或选定' },
};

const UNKNOWN_SOURCE = { label: '未知', color: 'default', hint: '后端返回了未收录的来源值' };
const UNKNOWN_CONFIDENCE = { label: '未知', color: 'default', hint: '后端返回了未收录的置信值' };

const CONFIDENCE_META: Record<Confidence, { label: string; color: string; hint: string }> = {
  certain: { label: '确定', color: 'success', hint: '来自确定性来源' },
  high: { label: '高', color: 'success', hint: '多路一致，或由主数据回填' },
  low: { label: '待核', color: 'warning', hint: '多路不一致，或仅单一来源、无交叉验证' },
  missing: { label: '缺失', color: 'default', hint: '所有来源都没有给出值' },
};

interface Props {
  diffs: ParsingDiff[];
  nulled: string[];
  resolvingId: number | null;
  onResolve: (diffId: number, source: 'qr' | 'ocr' | 'llm' | 'custom') => void;
  onCustom: (diff: ParsingDiff) => void;
}

/** 三路原始值。lookup / computed 的候选值只是校验证据，不是来源，要分开说。 */
function SourceRow({ label, value, active }: { label: string; value: string | null; active: boolean }) {
  return (
    <div className={styles.sourceRow}>
      <span className={styles.sourceLabel}>{label}</span>
      <span className={active ? styles.sourceValueActive : styles.sourceValue}>
        {value || <Text type="secondary">未给出</Text>}
      </span>
    </div>
  );
}

export default function FieldComparison({
  diffs, nulled, resolvingId, onResolve, onCustom,
}: Props) {
  const [expanded, setExpanded] = useState<readonly React.Key[]>([]);

  const columns: ColumnsType<ParsingDiff> = [
    {
      title: '字段',
      dataIndex: 'field_name',
      width: '18%',
      render: (name: string) => FIELD_LABELS[name] || name,
    },
    {
      title: '最终值',
      dataIndex: 'final_value',
      width: '40%',
      render: (value: string | null, row) => {
        if (value === null || value === '') {
          // 被拒绝置空和"识别不到"是两回事，必须分开显示，
          // 否则用户会以为模型没读出来，而实际是规则主动拒绝了。
          if (nulled.includes(row.field_name)) {
            return (
              <Space size={4}>
                <WarningOutlined className={styles.rejectIcon} />
                <Text type="danger">已拒绝，需人工填写</Text>
              </Space>
            );
          }
          return <Text type="secondary">未识别</Text>;
        }
        return <span className={styles.finalValue}>{value}</span>;
      },
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: '12%',
      render: (source: FieldSource | null) => {
        if (!source) return <Text type="secondary">-</Text>;
        // 后端加了新枚举而前端没跟上时，这里取到 undefined 会让整页崩掉。
        // 一个字段的展示问题不应该连带整个页面不可用。
        const meta = SOURCE_META[source] || UNKNOWN_SOURCE;
        return (
          <Tooltip title={meta.hint}>
            <Tag color={meta.color}>{meta.label}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '置信',
      dataIndex: 'confidence',
      width: '12%',
      render: (conf: Confidence | null, row) => {
        if (!conf) return <Text type="secondary">-</Text>;
        const meta = CONFIDENCE_META[conf] || UNKNOWN_CONFIDENCE;
        return (
          <Tooltip title={row.note || meta.hint}>
            <Tag color={meta.color}>{meta.label}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: '18%',
      render: (_, row) => {
        if (row.resolved) {
          return (
            <Space size={4}>
              <CheckCircleOutlined className={styles.okIcon} />
              <Text type="secondary">已确认</Text>
            </Space>
          );
        }
        const derived = row.source === 'lookup' || row.source === 'computed';
        return (
          <Space size={4} wrap>
            {row.ocr_value && !derived && (
              <Button size="small" loading={resolvingId === row.id}
                onClick={() => onResolve(row.id, 'ocr')}>
                取 OCR
              </Button>
            )}
            {row.llm_value && !derived && (
              <Button size="small" loading={resolvingId === row.id}
                onClick={() => onResolve(row.id, 'llm')}>
                取模型
              </Button>
            )}
            <Button size="small" type="link" onClick={() => onCustom(row)}>
              手填
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Table<ParsingDiff>
      rowKey="id"
      size="small"
      pagination={false}
      dataSource={diffs}
      columns={columns}
      rowClassName={(row) =>
        row.confidence === 'low' ? styles.rowLow
          : row.confidence === 'missing' ? styles.rowMissing : ''
      }
      expandable={{
        expandedRowKeys: expanded,
        onExpandedRowsChange: setExpanded,
        expandedRowRender: (row) => {
          const derived = row.source === 'lookup' || row.source === 'computed';
          return (
            <div className={styles.detail}>
              {derived && (
                <div className={styles.derivedNote}>
                  该字段不由模型识别：
                  {row.source === 'lookup'
                    ? '按报销主体清单回填。下面的识别值仅用于核对模型有没有看错。'
                    : '按「金额 × 税率」与「金额 + 税额」算出。下面的识别值仅用于核对。'}
                </div>
              )}
              <SourceRow label="二维码" value={row.qr_value} active={row.source === 'qr'} />
              <SourceRow label="OCR" value={row.ocr_value} active={row.source === 'ocr'} />
              <SourceRow label="视觉模型" value={row.llm_value} active={row.source === 'vlm'} />
              {row.note && <div className={styles.note}>选源理由：{row.note}</div>}
            </div>
          );
        },
      }}
    />
  );
}
