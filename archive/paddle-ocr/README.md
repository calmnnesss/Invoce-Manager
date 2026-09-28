# 存档：本地 PaddleOCR 线路（传统 OCR）

v6 管线起不再使用本地 PaddleOCR。文字识别改由阿里云百炼的 qwen3.5-ocr 完成
（`backend/app/services/qwen_ocr_service.py`），它直接返回结构化键值对，不再经过
「识别成文本 → 正则抽字段」这一步。这里的文件只作参考，不参与运行、测试或镜像构建。

最后一个接入 PaddleOCR 的提交：`bc8ceb2`（version6 completed）。

## 文件

| 文件 | 原位置 | 说明 |
|---|---|---|
| `ocr_service.py` | `backend/app/services/` | PaddleOCR 识别 + `FieldExtractor`（正则抽字段）；PDF 文本层用 pdfplumber |
| `ocr_service.py.bak` | `backend/app/services/` | 上一文件的更早副本。被 `.gitignore` 的 `*.bak` 忽略，只存在于本地 |
| `invoice_service.py.v5bak` | `backend/app/services/` | v5 流程：PaddleOCR 与 LLM 两路并行、`_compare_and_resolve` 两路仲裁 |
| `archived_test_buyer_seller_extraction.py` | `backend/tests/test_buyer_seller_extraction.py` | `FieldExtractor` 的测试。已改名，并由 `archive/conftest.py` 排除在 pytest 收集之外 |
| `InvoiceDetailPage.tsx.v5bak` | `frontend/src/pages/` | v5 详情页（OCR 与 LLM 结果对比表） |

同时删掉了 `backend/app/services/invoice_service.py` 里已无调用方的 v5 仲裁残留：
`_normalize_value`、`NUMERIC_FIELDS`、`_values_are_equal`、`check_llm_available`（见 `bc8ceb2`）。

## 一并移除的依赖

- `backend/requirements.txt`：`paddlepaddle==2.6.2`、`paddleocr==2.7.3`、`pdfplumber==0.10.3`
- `docker-compose.yml`：后端的 `paddleocr_models:/root/.paddleocr` 卷挂载及卷声明
  （宿主机上已有的卷不会自动删除，确认不需要后可 `docker volume rm` 掉）

保留的依赖：`opencv-python-headless` 与 `numpy<2.0`（二维码线路用），`pdf2image` 与
apt 包 `poppler-utils`（视觉模型读 PDF、详情页原件预览用）。

## 若要恢复

1. 把 `ocr_service.py` 放回 `backend/app/services/`，测试放回 `backend/tests/` 并改回原名。
2. 恢复上面三个 pip 依赖与 docker 卷，重建后端镜像。
3. 在 `invoice_service.process_invoice` 里重新接入（参考 `invoice_service.py.v5bak` 的 `_run_ocr`）。

## 备注

- 二维码线路在容器里目前只走 cv2：镜像没有安装 `libzbar0`，`pyzbar` 导入失败会被
  `qr_service.decode_payload` 静默跳过。这与本次存档无关，只是顺带记录。
- PDF 原件现在以 `file://….pdf` 当作图片发给 qwen-ocr，可能识别失败；以前 PDF 的文本层
  靠 PaddleOCR + pdfplumber。这是已知缺口，未在本次处理。
