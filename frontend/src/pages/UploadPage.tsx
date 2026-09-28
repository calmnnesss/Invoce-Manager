import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Upload, message, List, Tag } from 'antd';
import { UploadOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import type { RcFile, UploadFile } from 'antd/es/upload/interface';
import { uploadInvoices } from '../services/api';
import type { UploadResponse } from '../types/invoice';
import styles from './UploadPage.module.css';

const { Dragger } = Upload;

interface Sample {
  key: string;
  label: string;
  /** 上传时的文件名 */
  name: string;
  src: string;
  thumb: string;
}

/**
 * 内置的示例发票（frontend/public/samples/），体验者不用自己找素材。
 * 原件逐字节保留（含 EXIF 方向），缩略图只用于展示。两张的购买方都在默认主体清单里。
 */
const SAMPLES: Sample[] = [
  { key: 'sample-1', label: '示例发票 1', name: '示例发票1.jpg', src: '/samples/sample-1.jpg', thumb: '/samples/sample-1-thumb.jpg' },
  { key: 'sample-2', label: '示例发票 2', name: '示例发票2.jpg', src: '/samples/sample-2.jpg', thumb: '/samples/sample-2-thumb.jpg' },
];

function UploadPage() {
  const navigate = useNavigate();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<UploadResponse[]>([]);
  const [loadingSample, setLoadingSample] = useState<string | null>(null);

  /** 把示例发票加入待上传列表，之后和自己选的文件走同一个上传流程 */
  const addSample = async (sample: Sample) => {
    setLoadingSample(sample.key);
    try {
      const res = await fetch(sample.src);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const file = new File([await res.blob()], sample.name, { type: 'image/jpeg' }) as RcFile;
      file.uid = sample.key;
      setFileList((list) => [
        ...list,
        { uid: sample.key, name: sample.name, size: file.size, type: file.type, originFileObj: file },
      ]);
    } catch (error) {
      console.error('Failed to load sample invoice:', error);
      message.error('示例发票加载失败，请重试');
    } finally {
      setLoadingSample(null);
    }
  };

  const handleUpload = async () => {
    if (fileList.length === 0) {
      message.warning('请先选择文件');
      return;
    }

    setUploading(true);
    try {
      const files = fileList.map((f) => f.originFileObj as File);
      const response = await uploadInvoices(files);
      setResults(response);

      const successCount = response.filter((r) => r.status === 'success').length;
      if (successCount > 0) {
        message.success(`成功上传 ${successCount} 个文件`);
        setFileList([]);
      }

      const failCount = response.filter((r) => r.status === 'error').length;
      if (failCount > 0) {
        message.error(`${failCount} 个文件上传失败`);
      }
    } catch (error) {
      message.error('上传失败，请重试');
      console.error(error);
    } finally {
      setUploading(false);
    }
  };

  const uploadProps = {
    multiple: true,
    accept: '.pdf,.jpg,.jpeg,.png',
    fileList,
    beforeUpload: (file: File) => {
      const isValid = ['application/pdf', 'image/jpeg', 'image/png'].includes(file.type);
      if (!isValid) {
        message.error('只支持 PDF、JPG、PNG 格式');
        return Upload.LIST_IGNORE;
      }

      const isLt10M = file.size / 1024 / 1024 < 10;
      if (!isLt10M) {
        message.error('文件大小不能超过 10MB');
        return Upload.LIST_IGNORE;
      }

      return false; // Prevent auto upload
    },
    onChange: ({ fileList: newFileList }: { fileList: UploadFile[] }) => {
      setFileList(newFileList);
    },
    onRemove: (file: UploadFile) => {
      setFileList(fileList.filter((f) => f.uid !== file.uid));
    },
  };

  return (
    <div className={styles.pageContainer}>
      <div className={styles.contentWrapper}>
        <header className={styles.pageHeader}>
          <h1 className={styles.pageTitle}>上传发票</h1>
        </header>

        <div className={styles.uploadCard}>
          <div className={styles.uploadDropzone}>
            <Dragger {...uploadProps}>
              <div className="ant-upload-drag-icon">
                <span className={styles.uploadIcon}>
                  <UploadOutlined />
                </span>
              </div>
              <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
              <p className="ant-upload-hint">
                支持 PDF、JPG、PNG 格式，单个文件最大 10MB，支持批量上传
              </p>
            </Dragger>
          </div>

          <div className={styles.samples}>
            <div>
              <p className={styles.samplesTitle}>没有现成的发票？用示例试试</p>
              <p className={styles.samplesHint}>
                两张增值税专用发票，购买方已在默认的主体清单里。加入后点「开始上传」即可。
              </p>
            </div>
            <div className={styles.sampleGrid}>
              {SAMPLES.map((sample) => {
                const added = fileList.some((f) => f.uid === sample.key);
                const loading = loadingSample === sample.key;
                return (
                  <button
                    key={sample.key}
                    type="button"
                    className={styles.sample}
                    disabled={added || loading || uploading}
                    onClick={() => addSample(sample)}
                  >
                    <img className={styles.sampleThumb} src={sample.thumb} alt={sample.label} width={640} height={475} />
                    <span className={styles.sampleMeta}>
                      <span className={styles.sampleName}>{sample.label}</span>
                      <span className={added ? styles.sampleAdded : styles.sampleAction}>
                        {added ? '已加入' : loading ? '加载中…' : '加入上传列表'}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className={styles.actionButtons}>
            <button
              className={styles.uploadButton}
              onClick={handleUpload}
              disabled={fileList.length === 0 || uploading}
            >
              {uploading ? '上传中...' : '开始上传'}
            </button>
            <button
              className={styles.backButton}
              onClick={() => navigate('/invoices')}
            >
              返回列表
            </button>
          </div>
        </div>

        {results.length > 0 && (
          <div className={styles.resultsCard}>
            <h2 className={styles.resultsTitle}>上传结果</h2>
            <List
              className={styles.resultsList}
              dataSource={results}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    avatar={
                      item.status === 'success' ? (
                        <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 24 }} />
                      ) : (
                        <CloseCircleOutlined style={{ color: '#ff4d4f', fontSize: 24 }} />
                      )
                    }
                    title={item.file_name}
                    description={item.message}
                  />
                  {item.status === 'success' && (
                    <Tag color="success">ID: {item.id}</Tag>
                  )}
                </List.Item>
              )}
            />
            <button
              className={styles.viewListButton}
              onClick={() => navigate('/invoices')}
            >
              查看发票列表
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default UploadPage;
