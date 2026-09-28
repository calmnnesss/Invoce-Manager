# 存档：v6 版发票详情页

详情页已重构为「AI 核对工作台」（`frontend/src/components/InvoiceReview/`），这里保留重构前的实现，
不参与构建（`frontend/tsconfig.json` 只编译 `src/`）。

| 文件 | 原位置 | 说明 |
|---|---|---|
| `InvoiceDetailPage.v6.tsx` / `.v6.module.css` | `frontend/src/pages/` | 旧详情页：发票信息卡 + 「字段来源与校验」表，编辑走 `PUT /invoices/{id}` |
| `FieldComparison/` | `frontend/src/components/` | 三路对比表（取 OCR / 取模型 / 手填），调用已删除的 `POST /invoices/{id}/diffs/{diff_id}/resolve` |
| `VerdictBanner/` | `frontend/src/components/` | 判定横幅与「二维码线路未启用」提示 |

这些文件重构时尚未提交到 git，所以移到这里而不是直接删除。确认不再需要后可以删掉整个目录。
旧页面里的来源 / 置信标签映射已并入 `InvoiceReview/RecognitionDetailsDrawer.tsx`（识别详情抽屉）。

## 设置弹窗

| 文件 | 原位置 | 说明 |
|---|---|---|
| `LLMConfigModal.tsx` | `frontend/src/components/` | 侧边栏「设置」打开的模型服务配置弹窗 |

设置改成了独立页面 `/settings`（`frontend/src/pages/SettingsPage.tsx`），弹窗里的模型服务配置
移到了 `frontend/src/components/settings/ModelServiceSection.tsx`；同页新增购买方主体清单与金额校验规则。
