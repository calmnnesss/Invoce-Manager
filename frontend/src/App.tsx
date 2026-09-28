import { useState, useEffect } from 'react';
import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom';
import LandingPage from './pages/LandingPage';
import UploadPage from './pages/UploadPage';
import InvoiceListPage from './pages/InvoiceListPage';
import InvoiceDetailPage from './pages/InvoiceDetailPage';
import SettingsPage from './pages/SettingsPage';
import { MainLayout } from './components/layout';
import { getLLMStatus } from './services/api';

/** 应用本体：侧栏布局 + 模型服务状态。介绍页在它外面，不检查状态、不显示侧栏。 */
function AppShell() {
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null);
  const [showLlmPromo, setShowLlmPromo] = useState(false);

  // Check LLM status on app load
  useEffect(() => {
    const checkLLMStatus = async () => {
      try {
        const status = await getLLMStatus();
        setLlmConfigured(status.is_configured);
        // Show promotion banner if LLM is not configured (non-blocking)
        if (!status.is_configured) {
          setShowLlmPromo(true);
        }
      } catch (error) {
        console.error('Failed to check LLM status:', error);
        // If check fails, assume not configured
        setLlmConfigured(false);
        setShowLlmPromo(true);
      }
    };
    checkLLMStatus();
  }, []);

  const handleLLMConfigured = () => {
    setLlmConfigured(true);
    setShowLlmPromo(false);
  };

  return (
    <MainLayout
      llmConfigured={llmConfigured}
      showLlmPromo={showLlmPromo}
      onCloseLLMPromo={() => setShowLlmPromo(false)}
    >
      <Routes>
        <Route path="/invoices" element={<InvoiceListPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/settings" element={<SettingsPage onLLMConfigured={handleLLMConfigured} />} />
        <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </MainLayout>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 项目介绍页：全屏独立页面，发票列表在 /invoices */}
        <Route path="/" element={<LandingPage />} />
        <Route path="/*" element={<AppShell />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
