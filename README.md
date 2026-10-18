<div align="center">

# Invoice Manager 🧾

### 三路抽取 · 置信度融合的中文增值税发票识别系统
### Chinese VAT Invoice Extraction with Tri-Source Fusion

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.2-61DAFB.svg)](https://reactjs.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
![Pipeline](https://img.shields.io/badge/Pipeline-v6-0052CC.svg)

**[在线体验 Live Demo](https://invoice.yukeyao.dev)**

<br/>

> **这个系统里，没有一个字段是「某个模型说什么就是什么」。**
>
> 要么来自确定性来源（二维码），要么查主数据表，要么按勾稽公式算出来，
> 要么多路交叉比对后取优先线 —— 模型的识别结果一律降级为**校验证据**。
>
> 因为在报销场景里，真正的风险不是「识别得不够准」，
> 而是**「错了但没人知道」**。

</div>

---

## ✨ 亮点 | Highlights

- **🔀 三路并行抽取** — 二维码（确定性）、qwen3.5-ocr（结构化抽取）、qwen-vl-max（视觉理解）同时跑，互为交叉验证
- **🎯 逐字段选源** — 每个字段按实测表现指定主源与兜底，输出带 `source` + `confidence` 标注，可追溯到三路各自说了什么
- **🧮 能算的不识别** — 税额与价税合计由「金额 × 税率」算出，购买方由主数据回填，从源头消灭两类最脏的字段
- **🛡️ 五条校验规则** — 买卖方倒置、税号校验位、代码位数、计算值比对、多路分歧，错误不修正、只标记
- **📏 静默错误率为核心指标** — 不追求「识别更准」，追求「错了能被发现」
- **🔬 离线评测闭环** — 融合层是纯函数，可在历史日志上零 API 成本重放；prompt 版本 / 主数据清单 / 后处理版本三个维度都写进日志指纹
- **🐳 一键部署** — Docker Compose，发票记录存在自己的数据库

---

## 🎯 要解决的问题 | Problem

<table>
<tr><td width="50%">

### 😫 常规做法的问题

- ❌ 单模型识别，错了无从发现
- ❌ 准确率 85% 听起来够用，但**整单可用率可能只有 30%**
- ❌ 买卖方位置填反 —— 字段全部合法，审批流一路绿灯
- ❌ 手机拍摄的发票方向不一，版面理解大幅退化
- ❌ 清洗器把「只差一位」的值删成 `null`，可诊断的错误变成信息缺失

</td><td width="50%">

### 😊 本项目的做法

- ✅ 三路独立抽取，**不一致本身就是检测信号**
- ✅ 主指标改为整单全对率 + 静默错误率
- ✅ 销售方落在本企业主体清单中 → 硬矛盾，直接拒绝
- ✅ 购买方改为闭集选择 + 主数据回填，不依赖版面理解
- ✅ 格式不合规保留原值并打标记，不静默删除

</td></tr>
</table>

---

## 🧩 它是怎么工作的 | How It Works

```
                          发票图片
                             │
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
      A 二维码解码      B qwen3.5-ocr     C qwen-vl-max
      本地 zbar/cv2     结构化字段抽取      视觉理解 + 闭集选择
      确定性，不会幻觉   rotate=off         prompt v3.1
            └────────────────┼────────────────┘
                             ▼
                    归一化 + 回声检测
              （表头被原样抄回时判为「未识别」）
                             ▼
                   ┌─────────────────┐
                   │   逐字段选源     │
                   │ 二维码→certain   │
                   │ 多路一致→high    │
                   │ 不一致/单源→low  │
                   └─────────────────┘
                             ▼
              购买方查表  ·  税额与合计按公式算出
                             ▼
                   校验层（五条规则）
                             ▼
              ok  /  warn  /  rejected  →  入库
```

### 逐字段选源表

| 字段 | 主源 | 兜底 | 校验 |
|---|---|---|---|
| 发票代码 / 号码 / 开票日期 | **二维码** | 视觉模型 → OCR | 位数校验、三路比对 |
| 不含税金额 | **二维码** | 视觉模型 | 与勾稽关系互验 |
| 购买方名称 / 税号 | **主体清单查表** | — | 识别值仅作核对 |
| 销售方名称 | **OCR** | 视觉模型 | 与视觉模型比对 |
| 销售方税号 | **OCR** | 视觉模型 | 位数 + 统一社会信用代码校验位 |
| 税率 | 视觉模型 | OCR | 与计算结果自洽 |
| 税额 / 价税合计 | **计算** | 识别值（降级时） | 计算值 vs 识别值 |
| 项目名称 | 视觉模型 | — | — |

销售方两个字段之所以是 OCR 优先：51 张实测中两路不一致共 7 次，**OCR 全对、视觉模型全错，没有一次相反**。
而且两者的错误形态不同 —— OCR 不确定时返回 `null`（可检测），视觉模型会给出格式合法的错值（静默）。

### 校验层

| 规则 | 判据 | 动作 |
|---|---|---|
| `seller_is_entity` | 销售方 ∈ 报销主体清单 | **拒绝**，销售方置空转人工 |
| `computed_vs_recognized` | 计算值与任一路识别值差 > 0.01 | 报警 |
| `tax_id_format` | 位数非 15/18，或 18 位校验位不通过 | 报警 |
| `invoice_number_format` | 代码非 10–12 位、号码非 8–10 位、两者相同 | 报警 |
| `cross_source_mismatch` | 存在多路不一致的字段 | 报警 |

**规则只标记、不修正。** 知道一个值错了，不等于知道正确值是什么 —— 猜测会把可见错误变成静默错误。

---

## 📊 实测效果 | Results

51 张手机拍摄的增值税专用发票，10 个受评字段（发票号码每张不同、未标注，不参与评分）：

| 指标 | 数值 |
|---|---|
| **整单 10 字段全对** | **50/51 = 98.0%** |
| **静默错误率**（错了但未触发任何规则） | **0 / 51 = 0%** |
| 错误拦截率 | 1/1 = 100% |
| 九个字段 51/51 全对 | 发票代码、开票日期、买卖双方名称与税号、税率等 |

唯一出错的一张是金额被读成了另一个数值，连带算出的税额与价税合计也错 —— 但被 `computed_vs_recognized` 与 `tax_id_format` 同时拦下。

### ⚠️ 已知局限

这些是实测暴露出来的真实边界，不是待办清单上的客套话：

- **误伤率偏高**：当前二维码线路未启用，`amount` 成为唯一单源字段，导致 51 张全部被判为 `warn`。全部标红等于没有标红 —— 判定需要进一步区分「值存疑」与「来源不够冗余」。
- **二维码在本测试集 51/51 解码失败**：zbar 与 cv2 两个解码器、四档缩放、灰度变体均已尝试。二维码区域在 3120×4208 的画面中占比过小是可能原因之一。该线路因此改为结果页手动触发。
- **样本为单一版式**：51 张均为同一版式的增值税专用发票。结论能否推广到数电票、普票、卷票尚未验证。
- **OCR 的 `enable_rotate=False` 是对本数据集的适配**：这批图中 44 张恰为顺时针侧存，关闭转正后模型进入「主体模式」，销售方税号从 11/51 提升至 45/51。真实用户上传的发票多为正放，届时可能退化，上线前需用正放样本复测。
- **OCR 的 schema 要 11 个字段但只采信 2 个**：收窄到 2 字段后销售方税号从 45/51 跌至 0/51。多出的 9 个字段是定位上下文而非冗余，删除会导致功能退化。

---

## 🔬 评测体系 | Evaluation Harness

迭代过程中最关键的基础设施不是管线本身，而是**可复现的离线评测**：

- **融合层与校验层都是纯函数** —— 不碰数据库、不发网络请求，因此可以直接在历史日志上重放。改一条融合规则、调一个阈值，重放一次即可看到效果，**零 API 成本**。
- **三维度版本指纹** —— 每条调用日志都记录 `Prompt` / `Entities` / `Postprocess` 三个版本号。同一个 prompt 版本下换了主数据清单，prompt 内容实际已经不同，仅靠版本号会静默混淆；清单指纹由内容哈希自动生成，忘记手动改标签也不会出错。
- **主指标明确** —— 字段级准确率会显著高估可用性（曾出现字段级 85.1% 而整单全对仅 29.4%）。对外口径以整单全对率与静默错误率为准。

```bash
cd backend

# 跑三条线路并输出融合报告（约 102 次 API 调用）
bash eval/run_v6.sh

# 只重放融合层，不调用任何模型
SKIP_VLM=1 SKIP_OCR=1 SKIP_QR=1 bash eval/run_v6.sh
```

产物在 `backend/eval/reports/`：逐字段 CSV（含三路原始值）、逐单据汇总 CSV、文字报告。

---

## 🚀 快速开始 | Quick Start

```bash
# 1. 克隆
git clone https://github.com/calmnnesss/Invoce-Manager.git
cd Invoce-Manager

# 2. 配置模型 API Key
#    backend/.env 中填入阿里云百炼的 API Key
#    QWEN_API_KEY=sk-xxxx
cp backend/.env.example backend/.env

# 3. 启动
docker compose up -d
```

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:15173 |
| 后端 API | http://localhost:18080 |
| API 文档 | http://localhost:18080/docs |

### 关键配置

```bash
# backend/.env
QWEN_API_KEY=sk-xxxx           # 阿里云百炼，VLM 与 OCR 两条线路共用
QWEN_MODEL=qwen-vl-max         # 视觉理解线路
PROMPT_VERSION=v3.1            # 提示词版本，见 app/services/prompts.py
OCR_ENABLE_ROTATE=false        # OCR 图像转正，实测依据见 qwen_ocr_service.py
OCR_MAX_PIXELS_N=8192          # max_pixels = 32*32*N
QR_ENABLED=false               # 二维码线路，默认关闭、结果页手动触发
```

报销主体清单在 `backend/app/services/buyer_entities.json`，购买方从中闭集选择。
主体清单、金额校验规则与模型服务也可在**前端设置页**直接维护，无需改代码或重启。

---

## 📋 功能特性 | Features

**识别与融合**
- 图片（JPG/PNG）与 PDF 上传，PDF 取首页，与识别管线一致
- 三路并行抽取，逐字段选源与置信度标注
- 购买方闭集选择 + 主数据回填
- 税额与价税合计按勾稽公式计算
- 原件统一渲染为 JPEG，图片按 EXIF 转正，前端缩放 / 平移 / 旋转

**人工复核**
- 字段级三路对比：最终值 + 来源徽标 + 置信标签，展开查看各路原始值
- 查表与计算类字段显式标注「不由模型识别」，识别值仅作核对
- 被拒绝置空的字段显示「已拒绝，需人工填写」，与「未识别」区分
- 人工改值后按同一组规则重新校验，判定口径与自动流程一致
- 结果页「用二维码重试」按钮，本地解码不消耗模型额度

**管理**
- **设置页可直接维护报销主体清单、金额校验规则、模型服务**，无需改代码
- 列表筛选、批量重处理、批量删除、统计卡片
- CSV / Excel 导出
- 审计日志、速率限制

---

## 📡 API 接口 | API Reference

```bash
POST   /api/invoices/upload                    # 上传（支持多文件）
GET    /api/invoices                           # 列表（分页 + 筛选）
GET    /api/invoices/{id}                      # 详情（含三路结果与校验明细）
GET    /api/invoices/statistics                # 统计

POST   /api/invoices/{id}/process              # 重新走一遍识别管线
POST   /api/invoices/{id}/retry-qr             # 手动触发二维码解码
PATCH  /api/invoices/{id}/fields/{field_name}  # 字段级确认 / 修改
POST   /api/invoices/{id}/confirm              # 整单确认

GET    /api/invoices/{id}/file                 # 原文件下载
GET    /api/invoices/{id}/preview              # 预览（统一 JPEG，已按 EXIF 转正）

POST   /api/invoices/batch-reprocess           # 批量重处理
POST   /api/invoices/batch-update              # 批量更新
GET    /api/invoices/export/csv                # 导出 CSV
GET    /api/invoices/export/excel              # 导出 Excel
```

详情接口返回的 `parsing_diffs` 中，每个字段都带 `qr_value` / `ocr_value` / `llm_value` / `final_value` / `source` / `confidence` / `note`，前端据此渲染三路对比。

---

## 🛠️ 技术栈 | Tech Stack

| 层 | 技术 |
|---|---|
| 后端 | FastAPI · SQLAlchemy 2.0（async）· Pydantic v2 |
| 前端 | React 18 · TypeScript · Ant Design 5 · Vite |
| 数据库 | PostgreSQL 16 |
| 视觉理解 | qwen-vl-max（阿里云百炼） |
| 结构化抽取 | qwen3.5-ocr `key_information_extraction`（DashScope SDK） |
| 二维码 | pyzbar / OpenCV，本地解码 |
| 部署 | Docker Compose |

---

## 📁 项目结构 | Project Structure

```
backend/app/services/
├── llm_service.py          视觉理解线路，多模型适配，调用日志落盘
├── qwen_ocr_service.py     qwen3.5-ocr 结构化抽取（参数的实测依据写在模块注释里）
├── qr_service.py           二维码解码，zbar 优先、cv2 回退
├── prompts.py              提示词版本注册表 v1 → v3.2，旧版原样保留以便 A/B
├── buyer_entities.json     报销主体清单（闭集），带内容指纹
├── fusion.py               逐字段选源、置信度判定、计算层          ← 纯函数
├── validation.py           校验规则                                ← 纯函数
├── review.py               人工核对后的重校验与回写                ← 纯函数
├── preview_service.py      原件统一渲染为 JPEG，EXIF 转正
└── invoice_service.py      管线编排：三路并发 → 融合 → 校验 → 入库

backend/eval/
├── run_v6.sh               端到端跑一轮并出报告
├── simulate_fusion.py      在历史日志上离线重放融合层
├── score_logs.py           按版本 / 主数据指纹筛选并打分
├── ground_truth.json       标注
└── reports/                逐字段 CSV、逐单据汇总、文字报告

frontend/src/components/
├── InvoiceReview/          复核工作区
│   ├── ReviewWorkspace     主布局：字段列表 + 原件查看器
│   ├── FieldRow            单字段：最终值 + 来源标记 + 置信标签
│   ├── RecognitionDetails  展开查看三路原始值
│   ├── DocumentViewer      原件缩放 / 平移 / 旋转
│   ├── charDiff.ts         字符级差异高亮
│   ├── useReviewKeyboard   键盘快捷复核
│   └── reviewModel.test.ts 单元测试
├── settings/               界面化配置：主体清单、金额规则、模型服务
├── dashboard/              统计卡片与控制栏
└── landing/                落地页（含管线示意图）
```

三个标注为「纯函数」的模块不依赖数据库和网络 —— 这是离线重放能力的前提。

---

## 💬 常见问题 | FAQ

<details>
<summary><b>Q: 为什么不只用一个模型？</b></summary>

因为单模型无法自检。两个独立线路的分歧本身就是最便宜的错误检测信号 ——
不需要知道谁对，只需要知道他们不一致。实测中销售方字段有 23 张两路不一致，
全部被正确标记，而最终取值 51/51 正确。
</details>

<details>
<summary><b>Q: 购买方为什么不识别？</b></summary>

因为系统本来就知道答案。员工在本企业 OA 内提交报销，购买方必然是本企业主体之一。
让模型去识别等于凭空制造一个错误来源。改为闭集选择后，购买方识别准确率从 54.9% 提升到 100%。

但不能盲填 —— 模型仍然识别购买方，只是用途从「提取」变成「核验」：
与系统主体不一致时报警，用于发现传错票、拿他人发票报销等情况。
</details>

<details>
<summary><b>Q: 税额为什么是算出来的？</b></summary>

增值税专票的税额与价税合计可由不含税金额和税率推出。这两个字段原本是识别最脏的
（OCR 有 49/51 返回大写金额，视觉模型也只有 88–96%），改为计算后直接消除。

识别出的值不丢弃，降级为校验值：与计算值不一致即报警。检测能力不但没丢，
反而更强 —— 第一次有了可信基准去比对。
</details>

<details>
<summary><b>Q: 数据安全吗？</b></summary>

发票记录存储在你自己的数据库中。识别时发票图片会发送给所配置的模型服务
（阿里云百炼），二维码解码完全在本地进行。请根据合规要求选择部署方式。
</details>

<details>
<summary><b>Q: 识别不准怎么办？</b></summary>

先看详情页的判定条：被规则拦下的错误会给出具体算式（例如「计算值 507.82，
视觉模型识别为 210.64」），不需要回头翻发票即可判断问题所在。
展开字段可以看到三路各自的原始值，一键采用其中一路或手动填写。
</details>

---

## 📜 License

MIT License

---

## 🙏 致谢 | Acknowledgments

本项目的工程骨架（FastAPI + React + PostgreSQL 的三端结构、Docker 部署方式、
发票管理的基础 CRUD 与界面）起步于 **[stone16/Invoice-Manager](https://github.com/stone16/Invoice-Manager)**，
一个基于 PaddleOCR + LLM 双源验证的开源发票管理项目（MIT License）。

在此基础上，本项目重写了识别管线的全部核心逻辑：以三路并行抽取与置信度融合
替换原有的两路仲裁，移除了本地 PaddleOCR 线路，新增主数据回填、勾稽计算、
规则校验与离线评测体系。前端的字段对比与复核界面亦重新实现。

感谢原作者提供了一个结构清晰、易于上手的起点。

其余依赖：

- [通义千问 qwen-vl-max / qwen3.5-ocr](https://help.aliyun.com/zh/model-studio/)（阿里云百炼）
- [FastAPI](https://fastapi.tiangolo.com/) · [React](https://reactjs.org/) · [Ant Design](https://ant.design/)
- [pyzbar](https://github.com/NaturalHistoryMuseum/pyzbar) · [OpenCV](https://opencv.org/)

---

<div align="center">

**Made by [YukeYao](https://yukeyao.dev)**

</div>
