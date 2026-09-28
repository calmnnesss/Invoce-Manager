import { Descriptions, Drawer, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { InvoiceDetail, ParsingDiff } from '../../types/invoice';
import { fieldLabel } from './reviewModel';

const { Text } = Typography;

/**
 * 识别详情（排查用）：页面上刻意隐藏的技术细节都在这里——各路原始值、来源、置信、
 * 规则结果与管线版本。默认不打开，只从「更多」菜单进入。
 */
const SOURCE_LABEL: Record<string, { label: string; color: string }> = {
  qr: { label: '二维码', color: 'cyan' },
  ocr: { label: 'OCR', color: 'purple' },
  vlm: { label: '视觉模型', color: 'volcano' },
  lookup: { label: '查表', color: 'green' },
  computed: { label: '计算', color: 'green' },
  manual: { label: '人工', color: 'blue' },
};

const CONFIDENCE_LABEL: Record<string, { label: string; color: string }> = {
  certain: { label: '确定', color: 'success' },
  high: { label: '高', color: 'success' },
  low: { label: '低', color: 'warning' },
  missing: { label: '缺失', color: 'default' },
};

const RULE_LABEL: Record<string, string> = {
  seller_is_entity: '销售方落在主体清单',
  amount_consistency: '金额勾稽（两式校验）',
  computed_vs_recognized: '计算值与识别值比对（旧）',
  tax_id_format: '税号位数与校验位',
  invoice_number_format: '发票代码/号码位数',
  cross_source_mismatch: '多路结果不一致',
};

const RULE_STATUS: Record<string, { label: string; color: string }> = {
  pass: { label: '通过', color: 'success' },
  fail: { label: '未通过', color: 'error' },
  skipped: { label: '未校验', color: 'default' },
};

function raw(v: string | null) {
  return v ?? <Text type="secondary">—</Text>;
}

function tagOf(map: Record<string, { label: string; color: string }>, v: string | null) {
  if (!v) return <Text type="secondary">—</Text>;
  const meta = map[v] ?? { label: v, color: 'default' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

interface Props {
  open: boolean;
  invoice: InvoiceDetail;
  onClose: () => void;
}

export default function RecognitionDetailsDrawer({ open, invoice, onClose }: Props) {
  const columns: ColumnsType<ParsingDiff> = [
    { title: '字段', dataIndex: 'field_name', width: 110, render: (k: string) => fieldLabel(k) },
    { title: '采用值', dataIndex: 'final_value', render: raw },
    { title: '来源', dataIndex: 'source', width: 86, render: (v: string | null) => tagOf(SOURCE_LABEL, v) },
    { title: '置信', dataIndex: 'confidence', width: 70, render: (v: string | null) => tagOf(CONFIDENCE_LABEL, v) },
    { title: '二维码', dataIndex: 'qr_value', render: raw },
    { title: 'OCR', dataIndex: 'ocr_value', render: raw },
    { title: '视觉模型', dataIndex: 'llm_value', render: raw },
    { title: '已核', dataIndex: 'resolved', width: 56, render: (v: number) => (v ? '是' : '否') },
    { title: '选源说明', dataIndex: 'note', render: raw },
  ];

  return (
    <Drawer title="识别详情（排查用）" width={960} open={open} onClose={onClose} destroyOnClose>
      <Descriptions size="small" column={3} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="管线版本">{invoice.pipeline_version ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="判定">{invoice.verdict ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="走了降级路径">
          {(invoice.degraded ?? []).map(fieldLabel).join('、') || '—'}
        </Descriptions.Item>
        <Descriptions.Item label="被规则置空">
          {(invoice.nulled ?? []).map(fieldLabel).join('、') || '—'}
        </Descriptions.Item>
      </Descriptions>

      <Table<ParsingDiff>
        rowKey="id"
        size="small"
        pagination={false}
        dataSource={invoice.parsing_diffs}
        columns={columns}
        scroll={{ x: 900 }}
      />

      <Typography.Title level={5} style={{ marginTop: 24 }}>规则结果</Typography.Title>
      <Text type="secondary">人工修改后会按当前字段值重新校验，这里显示的是最近一次的结果。</Text>
      <Table
        rowKey="rule_id"
        size="small"
        pagination={false}
        style={{ marginTop: 8 }}
        dataSource={invoice.checks ?? []}
        columns={[
          { title: '规则', dataIndex: 'rule_id', width: 180, render: (id: string) => RULE_LABEL[id] ?? id },
          { title: '结果', dataIndex: 'status', width: 80, render: (s: string) => tagOf(RULE_STATUS, s) },
          { title: '涉及字段', dataIndex: 'fields', width: 180,
            render: (f?: string[]) => (f?.length ? f.map(fieldLabel).join('、') : '—') },
          { title: '说明', dataIndex: 'detail', render: (d: string) => d || '—' },
        ]}
      />
    </Drawer>
  );
}
