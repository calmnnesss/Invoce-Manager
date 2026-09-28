import { useParams, useNavigate } from 'react-router-dom';
import { Button, Result } from 'antd';
import ReviewWorkspace from '../components/InvoiceReview/ReviewWorkspace';

/**
 * 发票核对页。以发票 id 为 key 渲染工作台：切到下一张时草稿、焦点、选中项全部重置，
 * 上一张还没返回的请求也不会写进新发票的状态。
 */
function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const invoiceId = Number(id);

  if (!Number.isInteger(invoiceId) || invoiceId <= 0) {
    return (
      <Result
        status="404"
        title="发票不存在"
        extra={<Button onClick={() => navigate('/')}>返回列表</Button>}
      />
    );
  }

  return <ReviewWorkspace key={invoiceId} invoiceId={invoiceId} />;
}

export default InvoiceDetailPage;
