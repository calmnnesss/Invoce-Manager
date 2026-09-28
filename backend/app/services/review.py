"""人工核对阶段的纯函数（详情页上的字段确认与修改）。

融合层 fusion.fuse() 回答「机器怎么看」：每个字段取哪一路、置信多少、哪些要复核。
这里回答「人看过之后」：

    fusion_row_values  融合结果 -> ParsingDiff 行的列值（入库与测试共用这一份映射）
    revalidate            人工改值后用同一组规则重校验，判定口径与 fuse() 一致
    reconcile_after_edit  人改了金额组的某一项后，按同一套三层规则重新判定整组
    resolve_set           人工填写值的校验、规范化与来源归属
    apply_to_invoice   按 invoices 表的列类型写回

与 fusion 一样不碰数据库、不发请求。入参是 ParsingDiff 行或任何带同名属性的对象，
所以可以离线测试。

几条约定
--------
- 人工操作只改 resolved / final_value / source，不改 confidence。confidence 记录的是
  机器的判断，页面靠它区分「自动核验」和「你已确认」。（金额组重判时整组放行或退回，
  那是机器的新判断，会同步改写没人看过的那几项的 confidence。）
- 人明确给出的值不被系统覆盖：金额组重判只动没人看过的机器值，
  重校验命中 seller_is_entity 也只报错、不置空。
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.fusion import (
    AMOUNT_GROUP,
    DEFAULT_AMOUNT_RULES,
    AmountRules,
    CONF_HIGH,
    CONF_LOW,
    CONF_MISSING,
    SRC_COMPUTED,
    SRC_LOOKUP,
    SRC_MANUAL,
    SRC_OCR,
    SRC_QR,
    SRC_VLM,
    FieldResult,
    clean,
    reconcile_amounts,
    run_checks,
)
from app.services.validation import (
    STATUS_FAIL,
    STATUS_PASS,
    VERDICT_OK,
    VERDICT_REJECTED,
    VERDICT_WARN,
    Check,
    parse_amount,
)

# 融合层产出、在 ParsingDiff 里各有一行的字段
DIFF_FIELDS = (
    "invoice_code", "invoice_number", "issue_date", "amount",
    "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id",
    "tax_rate", "item_name", "tax_amount", "total_with_tax",
)

# 确认发票时必须有值的字段（confirm 接口与前端共用这一口径）
REQUIRED_FIELDS = (
    "invoice_number", "issue_date", "total_with_tax", "buyer_name",
    "buyer_tax_id", "seller_name", "seller_tax_id", "item_name",
)

# 以金额数值存储的字段（输入校验、比较与写回 Numeric 列时按数值处理）
MONEY_FIELDS = {"amount", "tax_amount", "total_with_tax"}
ID_FIELDS = {"invoice_code", "invoice_number", "buyer_tax_id", "seller_tax_id"}

# invoices 表对应列的长度上限，超长会在 flush 时才报错，所以提前拦
TEXT_LIMITS = {
    "invoice_code": 20, "invoice_number": 50, "buyer_name": 255,
    "buyer_tax_id": 50, "seller_name": 255, "seller_tax_id": 50,
    "item_name": 500, "tax_rate": 20,
}

TAX_FREE_WORDS = ("免税", "不征税", "零税率")

# ParsingDiff 上三路候选值所在的列
CANDIDATE_ATTRS = ((SRC_QR, "qr_value"), (SRC_OCR, "ocr_value"), (SRC_VLM, "llm_value"))

_DATE_INPUT = re.compile(
    r"(\d{4})\s*[-/.年]?\s*(\d{1,2})\s*[-/.月]?\s*(\d{1,2})\s*日?"
)


class InvalidValue(ValueError):
    """人工填写的值不合法。消息是中文，可以直接展示给用户。"""


# --------------------------------------------------------------------------
# 融合结果 <-> ParsingDiff 行
# --------------------------------------------------------------------------
def fusion_row_values(field_name: str, fr: FieldResult) -> Dict[str, Any]:
    """一个融合字段对应的 ParsingDiff 列值。低置信与缺失的字段留给人复核。"""
    return {
        "field_name": field_name,
        "qr_value": fr.candidates.get(SRC_QR),
        "ocr_value": fr.candidates.get(SRC_OCR),
        "llm_value": fr.candidates.get(SRC_VLM),
        "final_value": fr.value,
        "source": fr.source,
        "confidence": fr.confidence,
        "note": (fr.note or "")[:255],
        "resolved": 0 if fr.confidence in (CONF_LOW, CONF_MISSING) else 1,
    }


def candidates_of(row: Any) -> Dict[str, str]:
    return {name: getattr(row, attr) for name, attr in CANDIDATE_ATTRS
            if getattr(row, attr) is not None}


def rows_to_fields(rows: Iterable[Any]) -> Dict[str, FieldResult]:
    """把 ParsingDiff 行还原成融合层的 FieldResult，供同一组规则复用。

    人已裁决的低置信行按 high 参与校验：人看过了，就不再算「多路不一致」。
    这只影响本次计算，不改库里的 confidence。
    """
    out: Dict[str, FieldResult] = {}
    for r in rows:
        conf = r.confidence or CONF_MISSING
        if r.resolved and conf == CONF_LOW:
            conf = CONF_HIGH
        out[r.field_name] = FieldResult(
            value=r.final_value, source=r.source, confidence=conf,
            candidates=candidates_of(r), note=r.note or "",
        )
    return out


# --------------------------------------------------------------------------
# 重校验
# --------------------------------------------------------------------------
def revalidate(rows: List[Any], nulled: Iterable[str], entities: List[Dict],
               rules: AmountRules = DEFAULT_AMOUNT_RULES) -> Tuple[List[Check], str]:
    """按当前字段值重跑 v6 的规则，并给出判定。

    判定口径与 fuse() 一致：
      rejected  seller_is_entity 失败
      warn      有规则失败，或还有未裁决的低置信字段（缺失字段不算，与 fuse 相同）
      ok        其余
    """
    fields = rows_to_fields(rows)
    checks = run_checks(fields, entities, rules)

    # 被 seller_is_entity 置空、人还没填回来：fuse() 当时的失败结论依然成立。
    # 置空后销售方为空，规则本身只会得出 skipped，不沿用的话判定会从 rejected
    # 悄悄降成 warn。
    waiting = [k for k in (nulled or []) if k in fields and fields[k].value is None]
    if waiting and checks[0].status != STATUS_FAIL:
        checks[0] = Check("seller_is_entity", STATUS_FAIL,
                          "销售方识别结果与本公司主体相同，已清空，待人工填写", waiting)

    if checks[0].status == STATUS_FAIL:
        verdict = VERDICT_REJECTED
    elif any(c.status == STATUS_FAIL for c in checks) or any(
            r.confidence == CONF_LOW and not r.resolved for r in rows):
        verdict = VERDICT_WARN
    else:
        verdict = VERDICT_OK
    return checks, verdict


# --------------------------------------------------------------------------
# 金额组重判
# --------------------------------------------------------------------------
def _human_held(row: Any) -> bool:
    """人填写或确认过的值、二维码读出的值：重判时不改写。
    机器给出、没人看过的值（包括自动放行的）可以被重新判定。"""
    if row.source in (SRC_MANUAL, SRC_QR):
        return True
    return bool(row.resolved) and row.confidence in (CONF_LOW, CONF_MISSING)


def _pass_group(group: Dict[str, Any], locked: Iterable[str], detail: str) -> None:
    """整组自洽：没人看过的机器值一并放行（这是机器的判断，所以同步改写 confidence）。"""
    for k, r in group.items():
        if k not in locked:
            r.confidence = CONF_HIGH
            r.resolved = 1
            r.note = detail or "金额勾稽校验通过"


def reconcile_after_edit(rows: Dict[str, Any], changed: str,
                         rules: AmountRules = DEFAULT_AMOUNT_RULES) -> List[str]:
    """人确认或修改了金额组的某一项之后，按 fusion.reconcile_amounts 的三层规则重新判定整组。
    返回 final_value 被改写的字段（调用方据此写回 invoices 列）。

    - 整组自洽（含推导补全一项）：推导出的值写回，没人看过的机器值一并放行
    - 仍然对不上：没人看过的机器值退回待复核（包括之前自动放行的——人给的新值和它们矛盾了）
    人填写或确认过的值、二维码读出的值始终不改写。
    """
    group = {k: rows.get(k) for k in AMOUNT_GROUP}
    if any(r is None for r in group.values()):
        return []
    locked = {k for k, r in group.items() if _human_held(r)} | {changed}
    check = reconcile_amounts({k: r.final_value for k, r in group.items()}, locked, rules)

    touched: List[str] = []
    if check.status == STATUS_PASS:
        if check.derived:
            row = group[check.derived]
            if row.final_value != check.values[check.derived]:
                touched.append(check.derived)
            row.final_value = check.values[check.derived]
            row.source = SRC_COMPUTED
        _pass_group(group, locked, check.detail)
    else:
        for k in check.implicated:
            if k in locked:
                continue
            r = group[k]
            r.resolved = 0
            r.confidence = CONF_LOW if r.final_value is not None else CONF_MISSING
            r.note = check.detail
    return touched


def restore_auto(rows: Dict[str, Any], field: str,
                 rules: AmountRules = DEFAULT_AMOUNT_RULES) -> List[str]:
    """把人填写的金额组某一项交还给公式，按其余三项推导。返回 final_value 被改写的字段。"""
    if field not in AMOUNT_GROUP:
        raise InvalidValue("只有金额、税率、税额和价税合计可以自动推算")
    group = {k: rows.get(k) for k in AMOUNT_GROUP}
    if any(r is None for r in group.values()):
        raise InvalidValue("缺少金额相关字段，无法自动推算")

    values = {k: r.final_value for k, r in group.items()}
    values[field] = None  # 当作缺失，让其余三项来推
    locked = {k for k, r in group.items() if k != field and _human_held(r)}
    check = reconcile_amounts(values, locked, rules)
    if check.status != STATUS_PASS or check.derived != field:
        raise InvalidValue("其余三项对不上，无法自动推算")

    row = group[field]
    touched = [field] if row.final_value != check.values[field] else []
    row.final_value = check.values[field]
    row.source = SRC_COMPUTED
    _pass_group(group, locked, check.detail)
    return touched


# --------------------------------------------------------------------------
# 人工填写
# --------------------------------------------------------------------------
def normalize_input(field: str, raw: Any) -> Optional[str]:
    """校验并规范化人工填写的值。空值返回 None；不合法抛 InvalidValue。

    只做格式层面的规范（日期、金额位数、税率写法、税号大小写）。名称类文本保留
    原文，只去首尾空白——fusion.clean() 会删掉所有空白，它只用来做比较。
    """
    s = str(raw).strip() if raw is not None else ""
    if not s:
        return None

    if field in MONEY_FIELDS:
        t = re.sub(r"[,，¥￥\s]", "", s)
        if not re.fullmatch(r"-?\d{1,10}(\.\d{1,2})?", t):
            raise InvalidValue("请输入金额数字，最多两位小数")
        return f"{Decimal(t):.2f}"

    if field == "issue_date":
        m = _DATE_INPUT.fullmatch(s)
        try:
            if not m:
                raise ValueError(s)
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            raise InvalidValue("日期格式不正确，例如 2024-01-31") from None

    if field == "tax_rate":
        t = s.replace(" ", "")
        if t in TAX_FREE_WORDS:
            return t
        m = re.fullmatch(r"(\d{1,2}(?:\.\d{1,2})?)%", t) or re.fullmatch(r"(\d{1,2})", t)
        if not m:
            raise InvalidValue("税率格式不正确，例如 13% 或 免税")
        return f"{m.group(1)}%"

    if field in ID_FIELDS:
        s = re.sub(r"[\s\-]", "", s.upper())
        if not re.fullmatch(r"[0-9A-Z]+", s):
            raise InvalidValue("只能填写数字和字母")

    limit = TEXT_LIMITS.get(field)
    if limit and len(s) > limit:
        raise InvalidValue(f"内容过长，最多 {limit} 个字符")
    return s


def _comparable(field: str, value: Optional[str]) -> Any:
    """比较用的规约形式。金额按数值比（clean() 会丢掉负号），其余用 clean()。"""
    if value is None:
        return None
    if field in MONEY_FIELDS:
        v = parse_amount(value)
        return None if v is None else round(v, 2)
    return clean(field, value)


def resolve_set(row: Any, raw: Any) -> Tuple[Optional[str], Optional[str], bool]:
    """人把字段设为 raw。返回 (写入值, 来源, 是否等同于确认当前值)。

    校验不通过抛 InvalidValue，此时行没有被改动。来源归属：
      - 与当前值相同：按确认处理，保留原来源
      - 金额组、查表值：记为 manual。这是人的判断，也因此不会被金额组重判覆盖
      - 等于某一路识别结果：记为那一路，存那一路的原值
      - 其余：manual
    """
    field = row.field_name
    value = normalize_input(field, raw)
    if value is None and field in REQUIRED_FIELDS:
        raise InvalidValue("这一项必填，不能为空")

    if _comparable(field, value) == _comparable(field, row.final_value):
        return row.final_value, row.source, True

    if field in AMOUNT_GROUP or row.source == SRC_LOOKUP or value is None:
        return value, SRC_MANUAL, False

    target = _comparable(field, value)
    for src, attr in CANDIDATE_ATTRS:
        cand = getattr(row, attr)
        if cand is not None and _comparable(field, cand) == target:
            return cand, src, False
    return value, SRC_MANUAL, False


def apply_to_invoice(invoice: Any, field: str, value: Optional[str]) -> None:
    """按 invoices 表的列类型写回。先转换、成功后才赋值，失败时不留半截写入。"""
    if value is None:
        converted: Any = None
    elif field == "issue_date":
        try:
            converted = date.fromisoformat(value)
        except ValueError:
            raise InvalidValue("日期格式不正确，例如 2024-01-31") from None
    elif field in MONEY_FIELDS:
        try:
            converted = Decimal(value)
        except InvalidOperation:
            raise InvalidValue("请输入金额数字，最多两位小数") from None
    else:
        limit = TEXT_LIMITS.get(field)
        if limit and len(value) > limit:
            raise InvalidValue(f"内容过长，最多 {limit} 个字符")
        converted = value
    setattr(invoice, field, converted)
