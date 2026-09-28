import { useEffect, useState } from 'react';
import {
  Alert, AutoComplete, Button, Col, Form, Input, Row, Select, Skeleton, Space, Tag, Tooltip, message,
} from 'antd';
import {
  ApiOutlined, CheckCircleOutlined, DownOutlined, ExclamationCircleOutlined, EyeOutlined, RightOutlined,
} from '@ant-design/icons';
import {
  configureLLM, errorDetail, getAvailableModels, getLLMStatus, testLLMConfig,
} from '../../services/api';
import type { LLMStatusResponse, ModelInfo } from '../../types/invoice';
import SettingsSection from './SettingsSection';
import styles from './ModelServiceSection.module.css';

// 模型列表接口失败时的兜底
const FALLBACK_MODELS: Record<string, ModelInfo[]> = {
  openai: [
    { id: 'gpt-4o', name: 'GPT-4o', vision: true },
    { id: 'gpt-4o-mini', name: 'GPT-4o Mini', vision: true },
    { id: 'gpt-4-turbo', name: 'GPT-4 Turbo', vision: true },
  ],
  anthropic: [
    { id: 'claude-sonnet-4-20250514', name: 'Claude Sonnet 4', vision: true },
    { id: 'claude-3-5-sonnet-20241022', name: 'Claude 3.5 Sonnet', vision: true },
    { id: 'claude-3-5-haiku-20241022', name: 'Claude 3.5 Haiku', vision: true },
  ],
  google: [
    { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash', vision: true },
    { id: 'gemini-1.5-pro', name: 'Gemini 1.5 Pro', vision: true },
    { id: 'gemini-1.5-flash', name: 'Gemini 1.5 Flash', vision: true },
  ],
  qwen: [
    { id: 'qwen2.5-vl-72b-instruct', name: 'Qwen 2.5 VL 72B', vision: true },
    { id: 'qwen-turbo', name: 'Qwen Turbo', vision: false },
  ],
  deepseek: [
    { id: 'deepseek-chat', name: 'DeepSeek Chat', vision: false },
  ],
  zhipu: [
    { id: 'glm-4v', name: 'GLM-4V', vision: true },
    { id: 'glm-4-flash', name: 'GLM-4 Flash', vision: false },
  ],
};

// 文字识别线路用的是通义千问，没有任何配置时默认选它
const DEFAULT_PROVIDER = 'qwen';

interface FormValues {
  provider: string;
  model?: string;
  api_key?: string;
  base_url?: string;
  config_token?: string;
}

interface ModelServiceSectionProps {
  onConfigured?: () => void;
}

export default function ModelServiceSection({ onConfigured }: ModelServiceSectionProps) {
  const [status, setStatus] = useState<LLMStatusResponse | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState('');
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsSource, setModelsSource] = useState<'openrouter' | 'fallback'>('fallback');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<FormValues>();

  const fetchModels = async (provider: string) => {
    setModelsLoading(true);
    try {
      const response = await getAvailableModels(provider, false);
      if (response.models && response.models.length > 0) {
        setModels(response.models);
        setModelsSource(response.source);
      } else {
        setModels(FALLBACK_MODELS[provider] || []);
        setModelsSource('fallback');
      }
    } catch (error) {
      console.error('Failed to fetch models:', error);
      setModels(FALLBACK_MODELS[provider] || []);
      setModelsSource('fallback');
    } finally {
      setModelsLoading(false);
    }
  };

  const load = async () => {
    setLoadFailed(false);
    try {
      const data = await getLLMStatus();
      const provider = data.active_provider || DEFAULT_PROVIDER;
      const saved = data.available_providers.find((p) => p.name === provider);
      setStatus(data);
      setSelectedProvider(provider);
      setShowAdvanced(Boolean(saved?.base_url));
      await fetchModels(provider);
    } catch (error) {
      console.error('Failed to fetch LLM status:', error);
      setLoadFailed(true);
    }
  };

  // 只在进入页面时加载一次
  useEffect(() => { load(); }, []);

  /** 某个提供商已保存的模型与地址（已配置的才有） */
  const savedConfig = (provider: string) => {
    const info = status?.available_providers.find((p) => p.name === provider);
    return info?.is_configured
      ? { model: info.model || '', base_url: info.base_url || '' }
      : { model: '', base_url: '' };
  };

  const handleProviderChange = async (provider: string) => {
    setSelectedProvider(provider);
    const saved = savedConfig(provider);
    form.setFieldsValue({
      provider,
      model: saved.model || FALLBACK_MODELS[provider]?.[0]?.id || '',
      api_key: '',
      base_url: saved.base_url,
      config_token: '',
    });
    await fetchModels(provider);
  };

  // 模型列表加载完后，表单里还没有模型就填第一个
  useEffect(() => {
    if (models.length > 0 && selectedProvider && status && !form.getFieldValue('model')) {
      form.setFieldsValue({ model: models[0].id });
    }
  }, [models, selectedProvider, status, form]);

  const handleSave = async () => {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // 表单校验未通过，错误已就地显示
    }
    setSaving(true);
    try {
      const result = await configureLLM({
        provider: values.provider,
        api_key: (values.api_key || '').trim(),
        model: values.model || undefined,
        base_url: values.base_url?.trim() || undefined,
      }, values.config_token?.trim() || undefined);
      if (result.success) {
        message.success(result.message);
        form.setFieldsValue({ api_key: '', config_token: '' });
        setStatus(await getLLMStatus());
        onConfigured?.();
      }
    } catch (error) {
      message.error(errorDetail(error, '保存失败，请检查配置'));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    const values = form.getFieldsValue();
    if (!values.provider) {
      message.warning('请先选择提供商');
      return;
    }
    if (!values.api_key?.trim()) {
      form.validateFields(['api_key']).catch(() => undefined);
      message.warning('请先输入 API 密钥');
      return;
    }
    setTesting(true);
    try {
      const result = await testLLMConfig({
        provider: values.provider,
        api_key: values.api_key.trim(),
        model: values.model || undefined,
        base_url: values.base_url?.trim() || undefined,
      });
      if (result.success) {
        message.success(`${result.message}（${result.response_time_ms}ms）`);
      } else {
        message.error(result.message);
      }
    } catch (error) {
      message.error(errorDetail(error, '测试失败'));
    } finally {
      setTesting(false);
    }
  };

  const statusTag = status && (status.is_configured ? (
    <Tag color="success" icon={<CheckCircleOutlined />}>已配置 · {status.active_provider_display}</Tag>
  ) : (
    <Tag color="warning" icon={<ExclamationCircleOutlined />}>未配置</Tag>
  ));

  const selectedConfigured = Boolean(
    status?.available_providers.find((p) => p.name === selectedProvider)?.is_configured,
  );

  let body;
  if (loadFailed) {
    body = (
      <Alert
        type="error"
        showIcon
        message="读取模型服务配置失败"
        action={<Button size="small" onClick={load}>重试</Button>}
      />
    );
  } else if (!status) {
    body = <Skeleton active paragraph={{ rows: 4 }} />;
  } else {
    const saved = savedConfig(selectedProvider);
    body = (
      <>
        {!status.is_configured && (
          <Alert
            className={styles.notice}
            type="warning"
            showIcon
            message="未配置模型服务时无法自动识别发票"
            description="文字识别需要通义千问（DashScope）API Key；再配置一个视觉模型可获得两路交叉核验。"
          />
        )}
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          initialValues={{
            provider: selectedProvider,
            model: saved.model || FALLBACK_MODELS[selectedProvider]?.[0]?.id || '',
            api_key: '',
            base_url: saved.base_url,
            config_token: '',
          }}
        >
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Form.Item name="provider" label="提供商" rules={[{ required: true, message: '请选择提供商' }]}>
                <Select
                  onChange={handleProviderChange}
                  options={status.available_providers.map((p) => ({
                    value: p.name,
                    label: (
                      <span className={styles.providerOption}>
                        <span>{p.display_name}</span>
                        {status.active_provider === p.name ? (
                          <Tag color="blue" bordered={false}>当前使用</Tag>
                        ) : p.is_configured && <Tag bordered={false}>已配置</Tag>}
                      </span>
                    ),
                  }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="model"
                label={(
                  <Space size={6}>
                    <span>模型</span>
                    {modelsSource === 'openrouter' && (
                      <Tag color="blue" bordered={false} className={styles.sourceTag}>来自 OpenRouter</Tag>
                    )}
                  </Space>
                )}
              >
                <AutoComplete
                  placeholder={modelsLoading ? '加载模型列表…' : '选择或输入模型名称'}
                  allowClear
                  options={models.map((m) => ({
                    value: m.id,
                    label: (
                      <Space>
                        <span>{m.name || m.id}</span>
                        {m.vision && (
                          <Tooltip title="支持图像识别">
                            <EyeOutlined className={styles.visionIcon} />
                          </Tooltip>
                        )}
                      </Space>
                    ),
                  }))}
                  filterOption={(input, option) =>
                    (option?.value as string)?.toLowerCase().includes(input.toLowerCase())}
                />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="api_key"
            label="API 密钥"
            rules={[{ required: true, whitespace: true, message: '请输入 API 密钥' }]}
            extra={selectedConfigured ? '已保存的密钥不会显示；修改这一项配置时需要重新输入。' : undefined}
          >
            <Input.Password
              placeholder={selectedConfigured ? '已配置，重新输入以更新' : '输入 API 密钥'}
              prefix={<ApiOutlined />}
              autoComplete="new-password"
            />
          </Form.Item>

          <Button
            type="link"
            className={styles.advancedToggle}
            icon={showAdvanced ? <DownOutlined /> : <RightOutlined />}
            onClick={() => setShowAdvanced((v) => !v)}
          >
            高级选项
          </Button>
          {/* 收起时只是不显示，字段仍在表单里：测试与保存都要带上已保存的自定义地址 */}
          <Row gutter={24} className={showAdvanced ? styles.advanced : styles.advancedHidden}>
            <Col xs={24} md={12}>
              <Form.Item name="base_url" label="自定义 API 地址" tooltip="用于自定义 API 端点或代理服务">
                <Input placeholder="例如 https://api.example.com/v1" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="config_token"
                label="管理员令牌"
                tooltip="后端设置了 LLM_CONFIG_TOKEN 时需要填写"
              >
                <Input.Password placeholder="未设置可留空" autoComplete="new-password" />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </>
    );
  }

  return (
    <SettingsSection
      id="model-service"
      title="模型服务"
      description="识别发票所用的模型与 API 密钥。密钥保存在服务器的 .env 文件中。"
      extra={statusTag}
      footer={status && !loadFailed ? (
        <div className={styles.footerActions}>
          <Button onClick={handleTest} loading={testing} disabled={saving}>测试连接</Button>
          <Button type="primary" onClick={handleSave} loading={saving}>保存配置</Button>
        </div>
      ) : undefined}
    >
      {body}
    </SettingsSection>
  );
}
