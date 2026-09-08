# LLM 线评测框架

用于衡量 LLM 视觉抽取（`llm_service.parse_invoice_from_image`）在 b0–b50 这批
测试图上的表现，并支持不同 prompt / 图像预处理方案之间的 A/B 对比。

**这套脚本不发起任何 API 调用。** 它读的是 `backend/logs/llm/*.log` ——
即 `_dump_llm_exchange` 已经落盘的原始响应。所以重跑评测是零成本的，
只有"重新识别一遍发票"才会消耗 token。

## 文件

| 文件 | 作用 |
|---|---|
| `ground_truth.json` | 标注。本数据集除发票号码外字段恒定，因此以「数据集级常量」形式记录 |
| `score_logs.py` | 打分器。读日志 → 对照 GT → 输出报告 + `results/<label>.json` |
| `fetch_image_meta.py` | 拉每张图的像素尺寸，用于「按方向分组」的准确率拆解（需后端在跑） |
| `image_meta.json` | 上一步的产物 |
| `results/` | 每次评测的完整结果，用于跨版本对比 |

## 工作流

```bash
cd backend

# 一次性：抓图片尺寸
python eval/fetch_image_meta.py --base-url http://localhost:18080

# 记录当前基线
python eval/score_logs.py --label baseline

# --- 改动 prompt 或加入图像预处理，然后重新上传/重跑这 51 张图 ---

# 只评新日志，并与基线对比
python eval/score_logs.py --after 20260908-1000 --label v1_orient --compare baseline
```

`--after` 用日志文件名的时间戳前缀做过滤，是区分「改动前后两批日志」的手段。
同一个文件被跑过多次时，脚本只保留时间戳最新的那一次。

## 指标定义

| 指标 | 含义 | 为什么看它 |
|---|---|---|
| **direction_accuracy** | 购买方/销售方没有颠倒的样本占比 | 本数据集的首要瓶颈。字段全部合法、格式全部正确，但付款对象反了 —— 下游审批拿不到任何异常信号，这是最危险的一类错误 |
| **exact_match** | 10 个受评字段全部正确的样本占比 | 真正可用性的口径。字段级准确率会显著高估可用性 |
| **field_acc_micro** | 所有字段格的正确率（微平均） | 用来和外部 OCR/抽取指标横向对比 |
| **raw vs clean** | 模型原始输出 vs 经 `_normalize_field_value` 之后 | 区分「模型识别错」和「清洗器改坏/吞掉」两类问题 |
| **cleaner_dropped** | 清洗器把非空错值直接吞成 `None` 的次数 | 信息丢失。下游连模型给过什么都不知道，无法转人工复核 |
| **arith_fail** | 金额 + 税额 ≠ 价税合计 的条数 | 纯规则可发现的错误，用来估算「不改模型能挽回多少」 |

## 主体字段错误分类

`buyer_name / buyer_tax_id / seller_name / seller_tax_id` 四个字段逐个归类：

- `exact` —— 完全正确
- `swapped` —— 填成了对方栏的值（版面方向理解错误）
- `swapped_near_miss` —— 对调，且伴随字形识别错误
- `near_miss` —— 归属正确但字形有错（如「晶东」→「晶乐」）
- `unrelated` —— 与两栏都不沾边，通常是把地址、开户行串了进来，或纯幻觉
- `missing` —— 空值

## 标注说明

- **不评 `invoice_number`**：这批图是同批次连号的 51 张**不同**发票，
  发票号码逐张不同，尚未逐张标注。
- **不评 `item_name`**：存在合法的写法差异（空格、`*分类*` 前缀），
  精确匹配没有意义，需要另设归一化规则。
- 买卖方方向已在 `b3.jpg`、`b0.jpg`、`b12.jpg` 三张原图上人工核对：
  **购买方 = 深圳市购机汇网络有限公司，销售方 = 广州晶东贸易有限公司**
  （右下角发票专用章属于销售方，是判定依据）。
- `b195.jpg` 不属于这批数据，已在 `exclude_files` 中排除。
