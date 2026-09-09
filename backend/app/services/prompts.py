"""Prompts for LLM-based invoice parsing.

Prompt 是会反复迭代的东西，所以这里按版本保留：改动不覆盖旧版，而是新增一版，
由 settings.prompt_version 决定线上用哪版。这样做的好处：

  1. A/B 可复现 —— 同一批图分别用 v1/v2 跑，结果能直接对比
  2. 一键回滚   —— 改坏了把 PROMPT_VERSION 改回去即可，不用翻 git
  3. 日志可追溯 —— 每条 LLM dump 都记录了当次使用的 prompt 版本

评测见 backend/eval/README.md。
"""

import hashlib
import json
from pathlib import Path

# --------------------------------------------------------------------------
# JSON Schema — 12 个业务字段，所有版本共用
# --------------------------------------------------------------------------
INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {
            "type": ["string", "null"],
            "description": "发票号码，通常为8-20位数字"
        },
        "invoice_code": {
            "type": ["string", "null"],
            "description": "发票代码，通常为10-12位数字（如有）"
        },
        "issue_date": {
            "type": ["string", "null"],
            "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
            "description": "开票日期，格式必须为YYYY-MM-DD"
        },
        "buyer_name": {
            "type": ["string", "null"],
            "description": "购买方名称（公司全称）"
        },
        "buyer_tax_id": {
            "type": ["string", "null"],
            "pattern": "^[A-Z0-9]{15,20}$",
            "description": "购买方纳税人识别号，15-20位字母数字"
        },
        "seller_name": {
            "type": ["string", "null"],
            "description": "销售方名称（公司全称）"
        },
        "seller_tax_id": {
            "type": ["string", "null"],
            "pattern": "^[A-Z0-9]{15,20}$",
            "description": "销售方纳税人识别号，15-20位字母数字"
        },
        "item_name": {
            "type": ["string", "null"],
            "description": "项目名称/货物名称，如有类别标记(*类别*商品)则保留"
        },
        "total_with_tax": {
            "type": ["string", "null"],
            "pattern": "^\\d+(\\.\\d{1,2})?$",
            "description": "价税合计金额，纯数字不含货币符号"
        },
        "amount": {
            "type": ["string", "null"],
            "pattern": "^\\d+(\\.\\d{1,2})?$",
            "description": "金额（不含税），纯数字"
        },
        "tax_amount": {
            "type": ["string", "null"],
            "pattern": "^\\d+(\\.\\d{1,2})?$",
            "description": "税额，纯数字（免税发票返回\"0\"）"
        },
        "tax_rate": {
            "type": ["string", "null"],
            "description": "税率，如\"6%\"、\"13%\"、\"免税\""
        }
    },
    "required": [
        "invoice_number", "invoice_code", "issue_date",
        "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id",
        "item_name", "total_with_tax", "amount", "tax_amount", "tax_rate"
    ],
    "additionalProperties": False
}

# Required fields list for validation
REQUIRED_FIELDS = list(INVOICE_JSON_SCHEMA["required"])

# v2 额外要求模型输出的判断依据字段。这些字段不进业务数据，
# _parse_json_response 会把它们当作 extra_keys 丢弃，但完整内容留在 dump 日志里，
# 用来分析模型对版面方向的判断是否正确。
DIAGNOSTIC_FIELDS = ["image_orientation", "party_evidence"]

# --------------------------------------------------------------------------
# 报销主体清单（v3 起使用）
# --------------------------------------------------------------------------
_ENTITIES_PATH = Path(__file__).resolve().parent / "buyer_entities.json"
_entities_cfg = json.loads(_ENTITIES_PATH.read_text(encoding="utf-8"))

BUYER_ENTITIES: list[dict] = _entities_cfg["entities"]
ENTITY_BY_ID: dict[str, dict] = {e["id"]: e for e in BUYER_ENTITIES}
DEFAULT_ENTITY_ID: str = _entities_cfg.get("default_entity_id", "")

# 主体清单会被渲染进 prompt，改清单等于改 prompt —— 但版本号不会变。
# 所以这里算一个自动指纹写进日志，让“同一个 v3.1 跡下实际跑的是哪份清单”
# 可追溯。手写 version 可选，忘了填也有 hash 兵底。
_entities_blob = json.dumps(BUYER_ENTITIES, sort_keys=True, ensure_ascii=False)
ENTITIES_HASH: str = hashlib.sha1(_entities_blob.encode("utf-8")).hexdigest()[:8]
ENTITIES_VERSION: str = (
    f"{_entities_cfg.get('version', 'unlabeled')}#{ENTITIES_HASH}"
    f"({len(BUYER_ENTITIES)})"
)

_entity_table = "\n".join(
    f"  {e['id']}    {e['name']}    {e['tax_id']}" for e in BUYER_ENTITIES
)

# Build field descriptions for the prompt
_field_descriptions = json.dumps(
    {k: v["description"] for k, v in INVOICE_JSON_SCHEMA["properties"].items()},
    ensure_ascii=False,
    indent=2
)

# v3 不让模型输出购买方名称和税号（改由系统按 id 填充）
_field_descriptions_no_buyer = json.dumps(
    {
        k: v["description"]
        for k, v in INVOICE_JSON_SCHEMA["properties"].items()
        if k not in ("buyer_name", "buyer_tax_id")
    },
    ensure_ascii=False,
    indent=2
)

# 系统提示词在各版本间保持一致，让 A/B 的变量只有 user prompt 一个
INVOICE_VISION_SYSTEM_PROMPT = """你是一个专业的中国发票信息提取助手。
你必须严格按照JSON Schema格式返回结果，无法识别的字段返回null。
只返回JSON对象，不要包含任何其他文字、解释或markdown代码块标记。"""


# ==========================================================================
# v1 —— 初版。保留用于对照，不要修改。
#
# 已知问题：「中国发票格式说明」一节给出了绝对位置先验（购买方在左上、
# 销售方在左下）。当图片被旋转 90/180/270 度时，这条规则会主动把模型
# 引向错误的栏位。基线评测中 51 张图有 41 张买卖方被完全对调，
# 竖向图的方向正确率仅 14.0%。
# ==========================================================================
INVOICE_VISION_PROMPT_V1 = f"""请分析这张中国发票图片，提取发票信息。

## 输出格式要求（必须严格遵守）

返回一个JSON对象，必须包含以下12个字段（不能多也不能少）：
{_field_descriptions}

字段类型规则：
- 所有字段值必须是 string 或 null（数值也用字符串表示）
- 日期格式必须是 YYYY-MM-DD
- 金额字段仅包含数字和小数点
- 税率字段格式如 \"6%\"、\"13%\" 或 \"免税\"
- 不能返回空字符串，用 null 表示缺失

## 购买方与销售方识别规则（最重要）

根据标签文字识别，不要依赖位置：
- 购买方 = 标注为"购买方"、"购方"、"购货单位"的区域
- 销售方 = 标注为"销售方"、"销方"、"销货单位"的区域
- 销售方区域通常还有"开票人"、"收款人"、"复核"等字样

中国发票格式说明：
- 标准增值税发票：购买方在左上，销售方在左下
- 电子普通发票：购买方在上部，销售方在底部（价税合计下方）
- 全电发票：购买方和销售方可能左右并排

## 字段提取规则

1. invoice_number: 查找"发票号码"标签后的数字
2. invoice_code: 查找"发票代码"标签后的数字（如无则返回null）
3. issue_date: 查找"开票日期"，转换为YYYY-MM-DD格式
4. buyer_name/seller_name: 查找"名称"标签后的公司全称
5. buyer_tax_id/seller_tax_id: 查找"纳税人识别号"后的15-20位编码
6. item_name: 查找商品行中的项目名称（可能有*分类*前缀）
7. total_with_tax: 查找"价税合计"或"小写"金额，只返回数字
8. amount: 查找"金额"或"合计"行的不含税金额
9. tax_amount: 查找"税额"，免税发票返回"0"
10. tax_rate: 查找"税率"列的值，如"6%"、"免税"

## 数据清洗规则

- 金额字段去除¥、￥、$、逗号，只保留数字和小数点
- 日期统一转换为YYYY-MM-DD
- 无法识别的字段返回null，不要猜测

请直接返回JSON对象："""


# ==========================================================================
# v2 —— 针对「买卖方对调」问题重写。相对 v1 的三处改动：
#
#   (1) 删除全部绝对位置先验，改为纯锚点判定（栏位标签 / 发票专用章 /
#       收款人行 / 密码区），这些锚点在图片被旋转后依然成立。
#   (2) 显式告知图片可能被旋转，并要求先判定方向、再以「摆正后」的
#       画面为参照系读取。
#   (3) 增加两个前置的判断依据字段（image_orientation / party_evidence），
#       放在业务字段之前输出，迫使模型先完成版面推理再填值；
#       这两个字段同时是可评测的诊断信号。
# ==========================================================================
INVOICE_VISION_PROMPT_V2 = f"""请从这张中国增值税发票图片中提取信息。

## 第一步：先判断图片方向

这些图片是手机拍摄的，很多张被旋转过（可能转了 90、180 或 270 度）。
在读取任何内容之前，先做这件事：

- 找到发票标题（如"广东增值税专用发票"）和表格边框，确定文字的正立方向
- 判断需要把原图**顺时针**旋转多少度，文字才能正立

之后描述任何"上/下/左/右"时，一律以**摆正之后**的画面为准，
不要使用图片文件本身的上下左右。

## 第二步：确定购买方和销售方（本任务最容易出错的地方）

**不要用"谁在上面谁就是购买方"这类位置规则判断。** 必须依靠下列锚点：

- **锚点 A（首选）—— 栏位标签**：每个主体信息块的边缘印有竖排的
  "购买方"或"销售方"（也可能写作"购方"/"销方"/"购货单位"/"销货单位"）。
  这个标签属于哪个信息块，该块里的"名称"和"纳税人识别号"就属于谁。

- **锚点 B —— 发票专用章**：图中的红色椭圆印章上写着"××公司发票专用章"，
  **印章上的公司名一定是销售方**，绝不会是购买方。

- **锚点 C**："收款人 / 复核 / 开票人"这一行紧邻销售方信息块。

- **锚点 D**："密码区"方块紧邻购买方信息块。

判定顺序：优先用 A；A 看不清时用 B；**A 与 B 冲突时以 B 为准**。

**输出前自检一次**：印章上的公司名是否等于你填的 seller_name？
如果印章上的公司名跑到了 buyer_name 里，说明买卖方填反了，交换后再输出。

## 第三步：其余字段

1. invoice_number: "发票号码"标签后的数字（常以 No. 或 № 开头）
2. invoice_code: "发票代码"标签后的数字（如无则返回null）
3. issue_date: "开票日期"，转换为 YYYY-MM-DD
4. buyer_name/seller_name: 对应信息块中"名称"后的公司全称
5. buyer_tax_id/seller_tax_id: 对应信息块中"纳税人识别号"后的编码
6. item_name: 商品行中的货物或应税劳务名称（有 *分类* 前缀则保留）
7. amount: "金额"列的合计值（不含税）
8. tax_rate: "税率"列的值，如"17%"、"6%"、"免税"
9. tax_amount: "税额"列的合计值，免税发票返回"0"
10. total_with_tax: "价税合计（小写）"后的数字

注意区分：**"地址、电话"和"开户行及账号"里的内容不是公司名称。**
如果某个字段你只能从这两行里凑出来，说明找错了位置，返回 null。

## 输出格式

返回一个 JSON 对象，**严格按下面的顺序**输出 14 个键：

先输出 2 个判断依据字段：
- "image_orientation": 需要把原图顺时针旋转多少度才能正立，取值 "0"、"90"、"180" 或 "270"
- "party_evidence": 一句话说明你判定销售方的依据，并写出你在红色印章上读到的公司名

再输出 12 个数据字段：
{_field_descriptions}

字段类型规则：
- 所有字段值必须是 string 或 null（数值也用字符串表示）
- 日期格式必须是 YYYY-MM-DD
- 金额字段只含数字和小数点，去除 ¥、￥、$、逗号
- 税率格式如 "6%"、"17%" 或 "免税"
- 不能返回空字符串，缺失一律用 null
- **无法辨认的字段返回 null，不要根据常识或上下文猜测**

只返回 JSON 对象本身，不要任何解释文字或 markdown 代码块标记："""


# ==========================================================================
# v3 —— 购买方改为闭集选择。相对 v2 只改一件事：
#
#   模型不再自由识别购买方名称和税号，而是从报销主体清单里选出一个 id，
#   系统再据此填充规范值。这使得购买方字段在结构上不可能出错：
#   v2 里购买方名称错 20 次、税号错 15 次，其中 7 处是把“地址、电话”或
#   “开户行及账号”整行串进了名称字段——这类错误在闭集下不再可能发生。
#
# 销售方仍然靠 v2 的锚点规则识别，未做改动。
# buyer_observed_* 两个字段只作诊断：没有它们就无法区分
# “模型真的读了发票选对了”和“模型盲选蒙对了”。
# ==========================================================================
INVOICE_VISION_PROMPT_V3 = f"""请从这张中国增值税发票图片中提取信息。

## 第一步：先判断图片方向

这些图片是手机拍摄的，很多张被旋转过（可能转了 90、180 或 270 度）。
在读取任何内容之前，先找到发票标题和表格边框，确定文字的正立方向。
之后描述任何"上/下/左/右"时，一律以**摆正之后**的画面为准。

## 第二步：购买方 —— 从清单中选择，不要自由填写

这张发票的购买方一定是下列主体之一：

{_entity_table}

做法：在发票的**购买方信息块**里找到"名称"和"纳税人识别号"两行，
把读到的内容与上表逐项比对，选出唯一匹配项，返回它的 id。

**清单中多项名称高度相似，必须逐字比对，不要凭大致印象选择。**
特别注意三个容易搞错的地方：
- 行业词：“网络”还是“科技”
- 地名：“深圳市”、“东莞市”还是“广州”
- 字序：“购机汇”还是“汇机购”
名称看不清时，以纳税人识别号为准，逐位比对。

同时请把你在发票上**实际读到**的购买方名称和税号原样填入
 buyer_observed_name / buyer_observed_tax_id，不要用清单里的写法替换它们。
这两个字段用于核对，写你真实看到的内容。

## 第三步：销售方 —— 靠锚点定位

**不要用"谁在上面谁在下面"这类位置规则判断。** 必须依靠下列锚点：

- **锚点 A（首选）—— 栏位标签**：信息块边缘印有竖排的"销售方"
  （也可能写作"销方"/"销货单位"）。该标签所属的块里的"名称"和
  "纳税人识别号"就是销售方的。
- **锚点 B —— 发票专用章**：红色椭圆印章上的公司名一定是销售方。
- **锚点 C**："收款人 / 复核 / 开票人"这一行紧邻销售方信息块。

判定顺序：优先用 A；A 看不清时用 B；**A 与 B 冲突时以 B 为准**。

销售方**不可能**是上面清单里的任何一家。如果你写入 seller_name 的公司
出现在了主体清单中，说明买卖方填反了，请重新检查。

## 第四步：其余字段

1. invoice_number: "发票号码"标签后的数字（常以 No. 或 № 开头）
2. invoice_code: "发票代码"标签后的数字（如无则返回null）
3. issue_date: "开票日期"，转换为 YYYY-MM-DD
4. seller_name / seller_tax_id: 销售方信息块中的公司全称与纳税人识别号
5. item_name: 商品行中的货物或应税劳务名称（有 *分类* 前缀则保留）
6. amount: "金额"列的合计值（不含税）
7. tax_rate: "税率"列的值，如"17%"、"6%"、"免税"
8. tax_amount: "税额"列的合计值，免税发票返回"0"
9. total_with_tax: "价税合计（小写）"后的数字

注意区分：**"地址、电话"和"开户行及账号"里的内容不是公司名称。**
如果某个字段你只能从这两行里凑出来，说明找错了位置，返回 null。

## 输出格式

返回一个 JSON 对象，**严格按下面的顺序**输出：

先输出 3 个购买方相关字段：
- "buyer_entity_id": 从上述清单中选中的主体 id，如 "E01"
- "buyer_observed_name": 你在发票购买方栏实际读到的名称原文
- "buyer_observed_tax_id": 你在发票购买方栏实际读到的纳税人识别号原文

再输出 10 个数据字段（注意：**不要**输出 buyer_name 和 buyer_tax_id）：
{_field_descriptions_no_buyer}

字段类型规则：
- 所有字段值必须是 string 或 null（数值也用字符串表示）
- 日期格式为 YYYY-MM-DD
- 金额字段只含数字和小数点，去除 ¥、￥、$、逗号
- 税率格式如 "6%"、"17%" 或 "免税"
- 不能返回空字符串，缺失一律用 null
- **无法辨认的字段返回 null，不要根据常识或上下文猜测**

只返回 JSON 对象本身，不要任何解释文字或 markdown 代码块标记："""


# ==========================================================================
# v3.1 —— 在 v3 基础上只加回一个字段：party_evidence。
#
# v3 实测发现销售方大幅回退（seller_name 56.9% -> 39.2%，
# seller_tax_id 76.5% -> 62.7%，方向正确率 80.4% -> 68.6%）。归因：
# v3 把购买方改成闭集时，顺手用 buyer_observed_* 替换掉了 v2 的两个
# 诊断字段，把 party_evidence 一并删了。而那个字段迫使模型在填
# seller_name 之前先论证判据，它才是 v2 销售方准确率的承载者。
#
# 为保证除此之外与 v3 逐字相同（单变量对照），这里不重写整段 prompt，
# 而是在 v3 上做一处锚点替换。锚点失效会直接报错，不会静默退化。
# ==========================================================================
_V3_OUTPUT_ANCHOR = """先输出 3 个购买方相关字段：
- "buyer_entity_id": 从上述清单中选中的主体 id，如 "E01"
- "buyer_observed_name": 你在发票购买方栏实际读到的名称原文
- "buyer_observed_tax_id": 你在发票购买方栏实际读到的纳税人识别号原文
"""

_V3_1_OUTPUT = """先输出 4 个判断依据字段：
- "buyer_entity_id": 从上述清单中选中的主体 id，如 "E01"
- "buyer_observed_name": 你在发票购买方栏实际读到的名称原文
- "buyer_observed_tax_id": 你在发票购买方栏实际读到的纳税人识别号原文
- "party_evidence": 一句话说明你判定销售方的依据，并写出你在红色印章上读到的公司名
"""

INVOICE_VISION_PROMPT_V3_1 = INVOICE_VISION_PROMPT_V3.replace(
    _V3_OUTPUT_ANCHOR, _V3_1_OUTPUT
)
if INVOICE_VISION_PROMPT_V3_1 == INVOICE_VISION_PROMPT_V3:
    raise RuntimeError(
        "v3.1 锚点未命中：v3 的输出格式段落被改过，请同步更新 _V3_OUTPUT_ANCHOR"
    )


# ==========================================================================
# v3.2 —— 用户手写版。相对 v3.1 的主要变化：
#
#   (1) 去掉 buyer_observed_name / buyer_observed_tax_id，前置字段从 4 个减到 2 个。
#       动机：v2->v3->v3.1 数值字段单调劣化（total_with_tax 96.1%->90.2%->88.2%），
#       假设是前置字段过多扰动了后面的生成。
#       代价：失去“模型实际读到什么”的诊断信号，无法再区分“盲选”与“读错”，
#       评测的方向判定也只能退化为只看销售方一侧。
#   (2) 主体选择从“选出唯一匹配项”改为“选出可行度最高的选项”，
#       并允许结合名称与税号综合判断。
#   (3) 删掉了 v3/v3.1 里针对干扰项的三条逐字比对提醒（行业词/地名/字序）。
#   (4) 销售方约束从末尾的硬性声明（“不可能是清单里的任何一家”）
#       改为开头的软性提示（“一般来说不会是…之一”）。
# ==========================================================================
INVOICE_VISION_PROMPT_V3_2 = f"""## 第一步：先判断图片方向
这些图片是手机拍摄的，很多张被旋转过（可能转了 90、180 或 270 度）。
在读取任何内容之前，先找到发票标题和表格边框，确定文字的正立方向。
之后描述任何"上/下/左/右"时，一律以**摆正之后**的画面为准。
## 第二步：购买方 —— 从清单中选择，不要自由填写
这张发票的购买方一定是下列主体之一：
{_entity_table}
做法：在发票的**购买方信息块**里找到"名称"和"纳税人识别号"两行，
把读到的内容与上表逐项比对，选出可行度最高的选项，并返回它的 id。
**清单中多项名称高度相似，必须逐字比对，不要凭大致印象选择，并且可以结合名称和纳税人识别号综合判断得到可行度最高的选项。**
## 第三步：销售方 —— 靠锚点定位
首先一般来说销售方不会是我前面列出来的购买方之一。
**不要用"谁在上面谁在下面"这类位置规则判断。** 必须依靠下列锚点：
- **锚点 A（首选）—— 栏位标签**：信息块边缘印有竖排的"销售方"
  （也可能写作"销方"/"销货单位"）。该标签所属的块里的"名称"和
  "纳税人识别号"就是销售方的。
- **锚点 B —— 发票专用章**：红色椭圆印章上的公司名一定是销售方。
- **锚点 C**："收款人 / 复核 / 开票人"这一行紧邻销售方信息块。
判定顺序：优先用 A；A 看不清时用 B；**A 与 B 冲突时以 B 为准**。
## 第四步：其余字段
1. invoice_number: "发票号码"标签后的数字（常以 No. 或 № 开头）
2. invoice_code: "发票代码"标签后的数字（如无则返回null）
3. issue_date: "开票日期"，转换为 YYYY-MM-DD
4. seller_name / seller_tax_id: 销售方信息块中的公司全称与纳税人识别号
5. item_name: 商品行中的货物或应税劳务名称（有 *分类* 前缀则保留）
6. amount: "金额"列的合计值（不含税）
7. tax_rate: "税率"列的值，如"17%"、"6%"、"免税"
8. tax_amount: "税额"列的合计值，免税发票返回"0"
9. total_with_tax: "价税合计（小写）"后的数字
注意区分：**"地址、电话"和"开户行及账号"里的内容不是公司名称。**
如果某个字段你只能从这两行里凑出来，说明找错了位置，返回 null。
## 输出格式
返回一个 JSON 对象，**严格按下面的顺序**输出：
先输出 2 个判断依据字段：
- "buyer_entity_id": 从上述清单中选中的主体 id，如 "E01"
- "party_evidence": 一句话说明你判定销售方的依据，并写出你在红色印章上读到的公司名
再输出 10 个数据字段（注意：**不要**输出 buyer_name 和 buyer_tax_id）：
{_field_descriptions_no_buyer}
字段类型规则：
- 所有字段值必须是 string 或 null（数值也用字符串表示）
- 日期格式为 YYYY-MM-DD
- 金额字段只含数字和小数点，去除 ¥、￥、$、逗号
- 税率格式如 "6%"、"17%" 或 "免税"
- 不能返回空字符串，缺失一律用 null
- **无法辨认的字段返回 null，不要根据常识或上下文猜测**
只返回 JSON 对象本身，不要任何解释文字或 markdown 代码块标记："""


# --------------------------------------------------------------------------
# 版本注册表
# --------------------------------------------------------------------------
DEFAULT_PROMPT_VERSION = "v3.2"

PROMPT_VERSIONS = {
    "v1": INVOICE_VISION_PROMPT_V1,
    "v2": INVOICE_VISION_PROMPT_V2,
    "v3": INVOICE_VISION_PROMPT_V3,
    "v3.1": INVOICE_VISION_PROMPT_V3_1,
    "v3.2": INVOICE_VISION_PROMPT_V3_2,
}


def resolve_prompt_version(version: str | None) -> str:
    """把配置值规约成一个确实存在的版本号。"""
    v = (version or "").strip().lower()
    return v if v in PROMPT_VERSIONS else DEFAULT_PROMPT_VERSION


def get_vision_prompt(version: str | None = None) -> tuple[str, str, str]:
    """返回 (system_prompt, user_prompt, resolved_version)。"""
    resolved = resolve_prompt_version(version)
    return INVOICE_VISION_SYSTEM_PROMPT, PROMPT_VERSIONS[resolved], resolved


# 向后兼容：老代码里直接 import INVOICE_VISION_PROMPT 的地方仍然可用
INVOICE_VISION_PROMPT = PROMPT_VERSIONS[DEFAULT_PROMPT_VERSION]
