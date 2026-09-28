"""三路抽取结果的融合层（Pipeline v6）。

输入三条相互独立的线路，输出一条带来源与置信度标注的记录。

    A 二维码   本地解码，确定性，无幻觉。解不出就是解不出，不会给个像的。
    B qwen3.5-ocr   schema 11 字段、rotate=off、N=8192，但只取销售方两个字段。
    C qwen-vl-max   prompt v3.1 + 主体清单闭集。

设计原则
--------
1. 纯函数 —— 不碰数据库、不发网络请求，因此可以拿历史日志离线回放，
   改融合规则不需要重新调用任何模型。
2. 没有任何字段是「某个模型说什么就是什么」：要么来自确定性来源（二维码），
   要么查表（购买方主体清单），要么靠金额组的两个等式互相印证（金额、税率、
   税额、价税合计），要么多路比对后取优先线。
3. 不一致本身就是产出。融合不追求猜对，而是在猜不准时如实标记，
   核心指标是静默错误率而不是准确率。

金额组的三层判定（见 reconcile_amounts）
--------------------------------------
金额 A、税率 r、税额 T、价税合计 S 满足 A + T = S 与 A × r = T，四个数互相印证，
比任何单一读数都可靠。所以这四项不再逐项比对，而是一起判定：
  第一层  两式同时成立（容差 0.01 / 0.02 元）直接放行，完全免人工
  第二层  只缺一项或只有一项有偏差时自动推导：反推税率（场景 A）、补全金额或税额（场景 B）、
          多行明细凑整误差以加法为准（场景 C，乘法差额上限 0.05 元）
  第三层  其余交人工
51 张实测：49 张第一层放行且全部正确（b1 是另一张带折扣行的票，真值文件把它当成了同一张），
b11（加法恰好成立但数字全错）与 b18（税额、价税合计读串栏）交人工，静默错误 0。
另一路（OCR）读出不同的数不影响放行：b21、b31 都是 OCR 把别的栏读进了金额栏。

为什么销售方税号是 OCR 优先
--------------------------
51 张实测：两路不一致 7 张，OCR 全对、VLM 全错，没有一张反过来。
OCR 的错误形态是 null（可检测），VLM 是格式合法的错值（静默）。
OCR 优先 51/51，VLM 优先 44/51，两者静默错误都是 0。

已知风险
--------
- rotate=off 在这批数据上有效，是因为 51 张里 44 张恰好是顺时针侧存的。
  真实用户上传的票大多正放，那时 OCR 可能进「表格模式」，销售方税号会退化。
  上线前需要用一批正放的真实票复测。
- 二维码线路在当前测试集上 51/51 解码失败，主路径未经真实数据检验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.validation import (
    Check,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    VERDICT_OK,
    VERDICT_REJECTED,
    VERDICT_WARN,
    check_seller_is_entity,
    parse_amount,
    parse_rate,
)

PIPELINE_VERSION = "v6"

# 置信等级
CONF_CERTAIN = "certain"    # 二维码解出，或由确定值计算得到
CONF_HIGH = "high"          # 多路一致，或查表得到
CONF_LOW = "low"            # 单源、或多路不一致 —— 一律标记复核
CONF_MISSING = "missing"

SRC_QR = "qr"
SRC_OCR = "ocr"
SRC_VLM = "vlm"
SRC_LOOKUP = "lookup"
SRC_COMPUTED = "computed"
SRC_MANUAL = "manual"   # 人工填写（详情页写入，融合层本身不产出）

# OCR 在读不到值时会把表头或字段名原样抄回来。这些不是值，是「我没读到」。
ECHO_WORDS = {
    "发票代码", "发票号码", "开票日期", "购买方名称", "购买方纳税人识别号",
    "销售方名称", "销售方纳税人识别号", "金额", "税额", "价税合计", "税率",
    "名称", "纳税人识别号", "不含税金额", "税额合计", "金额合计",
}

# OCR 返回的中文键 -> 内部英文字段名
OCR_KEY_MAP = {
    "发票代码": "invoice_code",
    "发票号码": "invoice_number",
    "开票日期": "issue_date",
    "购买方名称": "buyer_name",
    "购买方纳税人识别号": "buyer_tax_id",
    "销售方名称": "seller_name",
    "销售方纳税人识别号": "seller_tax_id",
    "金额": "amount",
    "税额": "tax_amount",
    "价税合计": "total_with_tax",
    "税率": "tax_rate",
}

NUMERIC_FIELDS = {"amount", "tax_amount", "total_with_tax"}

# 统一社会信用代码校验用
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


@dataclass
class FieldResult:
    value: Optional[str] = None
    source: Optional[str] = None
    confidence: str = CONF_MISSING
    candidates: Dict[str, Any] = dc_field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "candidates": self.candidates,
            "note": self.note,
        }


@dataclass
class FusionResult:
    fields: Dict[str, FieldResult] = dc_field(default_factory=dict)
    checks: List[Check] = dc_field(default_factory=list)
    verdict: str = VERDICT_OK
    nulled: List[str] = dc_field(default_factory=list)
    degraded: List[str] = dc_field(default_factory=list)

    @property
    def values(self) -> Dict[str, Optional[str]]:
        return {k: v.value for k, v in self.fields.items()}

    @property
    def low_confidence(self) -> List[str]:
        return [k for k, v in self.fields.items() if v.confidence == CONF_LOW]

    @property
    def failed_rules(self) -> List[str]:
        return [c.rule_id for c in self.checks if c.status == STATUS_FAIL]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pipeline": PIPELINE_VERSION,
            "verdict": self.verdict,
            "degraded": list(self.degraded),
            "nulled": list(self.nulled),
            "low_confidence": self.low_confidence,
            "fields": {k: v.as_dict() for k, v in self.fields.items()},
            "checks": [c.as_dict() for c in self.checks],
        }


# --------------------------------------------------------------------------
# 归一化
# --------------------------------------------------------------------------
def clean(field: str, value: Any) -> Optional[str]:
    """把原始值规约成可比对形式；识别不出或是回声则返回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    if s in ECHO_WORDS:
        return None
    if field in NUMERIC_FIELDS:
        m = re.search(r"\d+(?:\.\d+)?", s.replace(",", "").replace("，", ""))
        return f"{float(m.group()):.2f}" if m else None
    if field == "issue_date":
        m = re.search(r"(\d{4})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})", s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return s
    if field.endswith("tax_id") or field in ("invoice_code", "invoice_number"):
        return re.sub(r"[\s\-]", "", s.upper())
    if field == "tax_rate":
        return s.replace(" ", "")
    return re.sub(r"\s+", "", s)


def normalize_ocr(kv: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    if not kv:
        return {}
    return {en: clean(en, kv.get(cn)) for cn, en in OCR_KEY_MAP.items()}


def normalize_vlm(raw: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    if not raw:
        return {}
    out = {k: clean(k, v) for k, v in raw.items() if k not in ("buyer_entity_id",)}
    eid = raw.get("buyer_entity_id")
    out["buyer_entity_id"] = str(eid).strip().upper() if eid else None
    return out


def normalize_qr(qr: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    if not qr:
        return {}
    keys = ("invoice_code", "invoice_number", "issue_date", "amount")
    return {k: clean(k, qr.get(k)) for k in keys if qr.get(k) is not None}


# --------------------------------------------------------------------------
# 单字段融合
# --------------------------------------------------------------------------
def fuse_field(field: str, sources: List[tuple[str, Optional[str]]]) -> FieldResult:
    """sources 按优先级从高到低给出 (来源名, 已归一化的值)。

    一致 -> high；不一致或单源 -> low 并标记；二维码参与且有值 -> certain。
    """
    present = [(name, v) for name, v in sources if v is not None]
    cand = {name: v for name, v in sources if v is not None}

    if not present:
        return FieldResult(confidence=CONF_MISSING, candidates=cand,
                           note="所有来源均无值")

    top_name, top_value = present[0]
    distinct = {v for _, v in present}

    if top_name == SRC_QR:
        conf = CONF_CERTAIN
        note = "二维码解出"
        if len(distinct) > 1:
            note = "二维码解出，但与识别结果不一致"
    elif len(present) == 1:
        conf = CONF_LOW
        note = f"仅 {top_name} 有值，无交叉验证"
    elif len(distinct) == 1:
        conf = CONF_HIGH
        note = "多路一致"
    else:
        conf = CONF_LOW
        note = "多路不一致，取优先线"

    return FieldResult(value=top_value, source=top_name, confidence=conf,
                       candidates=cand, note=note)


# --------------------------------------------------------------------------
# 金额组：金额 A、税率 r、税额 T、价税合计 S 四项一起判定
# --------------------------------------------------------------------------
AMOUNT_GROUP = ("amount", "tax_rate", "tax_amount", "total_with_tax")


@dataclass(frozen=True)
class AmountRules:
    """金额组判定的可调参数，可在设置页修改（存 app_settings）。默认值即 51 张评测验证过的口径。"""
    add_tolerance: float = 0.01       # 加法校验 |A + T - S|：允许 1 分钱四舍五入
    mul_tolerance: float = 0.02       # 乘法校验 |A × r - T|：允许四舍五入
    # 场景 C：加法成立时，乘法最多允许这么大的差额（多行明细各自四舍五入的累计误差）。
    # 不设上限的话，b11 这种「2587.18 + 400.00 = 2987.18，加法恰好成立但数字全错」会被放行。
    rounding_tolerance: float = 0.05
    # 反推税率用的标准税率库，含历史税率（17%、16%、11%、10%）。
    # 不含 0：免税、零税率、不征税写法不同，反推不出该填哪一个，交给人。
    standard_rates: Tuple[float, ...] = (
        0.17, 0.16, 0.13, 0.11, 0.10, 0.09, 0.06, 0.05, 0.03, 0.015, 0.01)


DEFAULT_AMOUNT_RULES = AmountRules()

_EPS = 1e-9  # 浮点比较：0.01 的容差不能因为 0.010000000001 这类误差失效
_AMOUNT_LABEL = {"amount": "金额", "tax_rate": "税率",
                 "tax_amount": "税额", "total_with_tax": "价税合计"}


@dataclass
class AmountCheck:
    """金额组的判定结果。"""
    status: str                          # pass：放行；fail：交人工
    rule: str                            # 命中哪一层的哪一条，便于排查与统计
    values: Dict[str, Optional[str]]     # 四项的结果值（推导出的已填入）
    derived: Optional[str] = None        # 被推导补全或纠正的字段
    implicated: List[str] = dc_field(default_factory=list)  # 交人工时要复核的字段
    detail: str = ""


def _within(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + _EPS


def _fmt_rate(rate: float) -> str:
    return f"{rate * 100:g}%"


def _snap_rate(amount: float, tax: float, rules: AmountRules) -> Optional[float]:
    """由税额 ÷ 金额反推税率。恰好对上标准税率库里的一个才算数；
    对上多个说明金额太小、分辨不出，不猜。"""
    if not amount:
        return None
    hits = [r for r in rules.standard_rates if _within(amount * r, tax, rules.mul_tolerance)]
    return hits[0] if len(hits) == 1 else None


def reconcile_amounts(values: Dict[str, Optional[str]],
                      locked: Iterable[str] = (),
                      rules: AmountRules = DEFAULT_AMOUNT_RULES) -> AmountCheck:
    """金额组的三层判定。fuse() 与人工修改后的重算（review.reconcile_after_edit）共用。

    第一层  A、r、T、S 齐全，|A + T - S| ≤ 加法阈值（默认 0.01）且 |A × r - T| ≤ 乘法阈值（默认 0.02）：放行
    第二层  只缺一项或只有一项有偏差、其余三项自洽时自动推导：
            场景 A  加法成立、税率缺失或对不上：反推 T ÷ A，恰好对上标准税率就纠正并放行
            场景 C  加法成立、乘法差额在多行凑整上限内（默认 0.05）：以加法为准放行
            场景 B  缺金额、税额或价税合计：由加法补全，再用乘法校验，通过就放行
    第三层  其余交人工：两式都不成立；加法成立但乘法差额超出凑整范围、反推税率又不在库里；
            缺两项及以上；二维码读出的金额（locked）与其余三项对不上

    values  四项当前的字符串值
    locked  不允许推导改写的字段（二维码读出的、人工填写或确认过的）
    rules   阈值与标准税率库，默认值见 AmountRules
    """
    locked = set(locked)
    out = {k: values.get(k) for k in AMOUNT_GROUP}
    A = parse_amount(out["amount"])
    r = parse_rate(out["tax_rate"])
    T = parse_amount(out["tax_amount"])
    S = parse_amount(out["total_with_tax"])
    parsed = {"amount": A, "tax_rate": r, "tax_amount": T, "total_with_tax": S}
    missing = [k for k in AMOUNT_GROUP if parsed[k] is None]
    add_ok = None not in (A, T, S) and _within(A + T, S, rules.add_tolerance)
    mul_ok = None not in (A, r, T) and _within(A * r, T, rules.mul_tolerance)

    def passed(rule: str, detail: str = "", derived: Optional[str] = None,
               value: Optional[str] = None) -> AmountCheck:
        if derived:
            out[derived] = value
        return AmountCheck(STATUS_PASS, rule, out, derived, [], detail)

    # ---- 第一层：两式同时成立
    if add_ok and mul_ok:
        return passed("both")

    # ---- 第二层 场景 A / C：三个金额自洽（加法成立），问题出在税率
    if add_ok:
        std = None if "tax_rate" in locked else _snap_rate(A, T, rules)
        if std is not None:
            return passed("derive_rate", f"按 税额 ÷ 金额 反推税率 {_fmt_rate(std)}",
                          "tax_rate", _fmt_rate(std))
        if r is not None and _within(A * r, T, rules.rounding_tolerance):
            return passed("rounding",
                          f"加法成立，乘法相差 {abs(A * r - T):.2f} 元，按多行明细凑整处理")

    # ---- 第二层 场景 B：只缺一个金额，由加法补全，再用乘法校验
    if len(missing) == 1 and r is not None and missing[0] not in locked:
        gap = missing[0]
        if gap == "amount":
            a = round(S - T, 2)
            if _within(a * r, T, rules.mul_tolerance):
                return passed("derive_amount", "按 价税合计 − 税额 推算金额", gap, f"{a:.2f}")
        elif gap == "tax_amount":
            t = round(S - A, 2)
            if _within(A * r, t, rules.mul_tolerance):
                return passed("derive_tax", "按 价税合计 − 金额 推算税额", gap, f"{t:.2f}")
        elif gap == "total_with_tax" and mul_ok:
            return passed("derive_total", "按 金额 + 税额 推算价税合计", gap, f"{A + T:.2f}")

    # ---- 第三层：交人工
    implicated = [k for k in AMOUNT_GROUP if k not in locked]
    if len(missing) >= 2:
        rule = "insufficient"
        detail = "缺少" + "、".join(_AMOUNT_LABEL[k] for k in missing) + "，无法推算"
    elif add_ok and r is None:
        rule, detail = "rate_unknown", "缺少税率，按 税额 ÷ 金额 也对不上标准税率"
    elif add_ok:
        rule = "rate_mismatch"
        detail = (f"加法成立，但 金额 × 税率 与税额相差 {abs(A * r - T):.2f} 元，"
                  "反推税率也不在标准税率库")
    elif missing:
        rule = "unverifiable"
        detail = f"缺少{_AMOUNT_LABEL[missing[0]]}，其余三项对不上，无法推算"
    else:
        rule = "conflict"
        parts = [f"金额 + 税额 与价税合计相差 {abs(A + T - S):.2f} 元"]
        if not mul_ok:
            parts.append(f"金额 × 税率 与税额相差 {abs(A * r - T):.2f} 元")
        detail = "；".join(parts)
    return AmountCheck(STATUS_FAIL, rule, out, None, implicated, detail)


def fuse_amount_group(
    Q: Dict[str, Optional[str]],
    V: Dict[str, Optional[str]],
    O: Dict[str, Optional[str]],
    rules: AmountRules = DEFAULT_AMOUNT_RULES,
) -> Tuple[Dict[str, FieldResult], AmountCheck]:
    """金额组融合：每项取优先线的读数（金额：二维码 > 视觉模型 > OCR；其余：视觉模型 > OCR），
    再按 reconcile_amounts 的三层规则一起判定。

    另一路的读数只作参考、不参与判定：优先线四项自洽时，即使 OCR 读出了不同的数也照样放行
    （评测集 b21、b31 都是 OCR 把别的栏读进了金额栏）。
    """
    order = {
        "amount": ((SRC_QR, Q), (SRC_VLM, V), (SRC_OCR, O)),
        "tax_rate": ((SRC_VLM, V), (SRC_OCR, O)),
        "tax_amount": ((SRC_VLM, V), (SRC_OCR, O)),
        "total_with_tax": ((SRC_VLM, V), (SRC_OCR, O)),
    }
    cands: Dict[str, Dict[str, str]] = {}
    primary: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for key, sources in order.items():
        cands[key] = {name: src.get(key) for name, src in sources if src.get(key) is not None}
        primary[key] = next(iter(cands[key].items()), (None, None))

    locked = {"amount"} if primary["amount"][0] == SRC_QR else set()
    check = reconcile_amounts({k: primary[k][1] for k in AMOUNT_GROUP}, locked, rules)

    fields: Dict[str, FieldResult] = {}
    for key in AMOUNT_GROUP:
        src, val = primary[key]
        if check.status == STATUS_PASS and key == check.derived:
            fields[key] = FieldResult(check.values[key], SRC_COMPUTED, CONF_HIGH,
                                      cands[key], check.detail)
        elif check.status == STATUS_PASS:
            fields[key] = FieldResult(val, src, CONF_CERTAIN if src == SRC_QR else CONF_HIGH,
                                      cands[key], check.detail or "金额勾稽校验通过")
        else:
            conf = CONF_CERTAIN if src == SRC_QR else (CONF_LOW if val is not None else CONF_MISSING)
            fields[key] = FieldResult(val, src, conf, cands[key], check.detail)
    return fields, check


# --------------------------------------------------------------------------
# 额外校验规则
# --------------------------------------------------------------------------
def _uscc_valid(code: str) -> Optional[bool]:
    """18 位统一社会信用代码校验位。非 18 位返回 None（不适用）。"""
    if len(code) != 18 or any(c not in _USCC_CHARS for c in code):
        return None
    total = sum(_USCC_CHARS.index(code[i]) * _USCC_WEIGHTS[i] for i in range(17))
    rem = 31 - total % 31
    return _USCC_CHARS[0 if rem == 31 else rem] == code[17]


def check_tax_id_format(fields: Dict[str, FieldResult]) -> Check:
    problems, bad = [], []
    for key in ("buyer_tax_id", "seller_tax_id"):
        v = fields.get(key, FieldResult()).value
        if not v:
            continue
        if len(v) not in (15, 18):
            problems.append(f"{key} 位数为 {len(v)}，应为 15 或 18")
            bad.append(key)
            continue
        ok = _uscc_valid(v)
        if ok is False:
            problems.append(f"{key} 统一社会信用代码校验位不通过")
            bad.append(key)
    if not problems:
        checked = [k for k in ("buyer_tax_id", "seller_tax_id")
                   if fields.get(k, FieldResult()).value]
        return Check("tax_id_format", STATUS_PASS if checked else STATUS_SKIPPED,
                     "" if checked else "两个税号均为空", checked)
    return Check("tax_id_format", STATUS_FAIL, "；".join(problems), bad)


def check_invoice_number_format(fields: Dict[str, FieldResult]) -> Check:
    """专票代码 10 位、号码 8 位。位数不符最常见的成因是两栏互抄。"""
    problems, bad = [], []
    code = fields.get("invoice_code", FieldResult()).value
    num = fields.get("invoice_number", FieldResult()).value
    if code and not re.fullmatch(r"\d{10,12}", code):
        problems.append(f"发票代码位数异常: {code}")
        bad.append("invoice_code")
    if num and not re.fullmatch(r"\d{8,10}", num):
        problems.append(f"发票号码位数异常: {num}")
        bad.append("invoice_number")
    if code and num and code == num:
        problems.append("发票代码与号码相同")
        bad.extend(k for k in ("invoice_code", "invoice_number") if k not in bad)
    if problems:
        return Check("invoice_number_format", STATUS_FAIL, "；".join(problems), bad)
    if not code and not num:
        return Check("invoice_number_format", STATUS_SKIPPED, "代码与号码均为空")
    checked = [k for k, v in (("invoice_code", code), ("invoice_number", num)) if v]
    return Check("invoice_number_format", STATUS_PASS, fields=checked)


def check_amount_consistency(fields: Dict[str, FieldResult],
                             rules: AmountRules = DEFAULT_AMOUNT_RULES) -> Check:
    """金额组两式勾稽，判定口径见 reconcile_amounts。只看当前值是否自洽，不改写任何值。

    取代 v6 早期的 computed_vs_recognized（税额与价税合计一律按 金额 × 税率 计算）：
    那种做法在多行明细的票上会用算出来的数覆盖票面上正确的数。
    """
    values = {k: fields[k].value if k in fields else None for k in AMOUNT_GROUP}
    locked = {k for k in AMOUNT_GROUP
              if k in fields and fields[k].source in (SRC_QR, SRC_MANUAL)}
    res = reconcile_amounts(values, locked, rules)
    if res.status == STATUS_PASS:
        present = [k for k in AMOUNT_GROUP if values[k] is not None]
        return Check("amount_consistency", STATUS_PASS, res.detail, present)
    # 对不上的是整组：四项都标出来，包括人填写或确认过的——否则人把几项都确认了、
    # 整组仍然对不上时，页面上没有任何一项会提示。（退回待复核只针对没人看过的，见 implicated）
    return Check("amount_consistency", STATUS_FAIL, res.detail, list(AMOUNT_GROUP))


def check_cross_source(fields: Dict[str, FieldResult]) -> Check:
    low = [k for k, v in fields.items()
           if v.confidence == CONF_LOW and len({x for x in v.candidates.values()}) > 1]
    if low:
        detail = "；".join(
            f"{k}: " + ", ".join(f"{s}={v}" for s, v in fields[k].candidates.items())
            for k in low
        )
        return Check("cross_source_mismatch", STATUS_FAIL, detail, low)
    compared = [k for k, v in fields.items() if len(v.candidates) > 1]
    return Check("cross_source_mismatch", STATUS_PASS, fields=compared)


def run_checks(fields: Dict[str, FieldResult], entities: List[Dict],
               rules: AmountRules = DEFAULT_AMOUNT_RULES) -> List[Check]:
    """v6 的五条校验。

    fuse() 与人工修改后的重校验（review.revalidate）共用这一份，
    保证两处的规则集合与顺序一致。seller_is_entity 固定排在第一条。
    """
    flat = {k: v.value for k, v in fields.items()}
    return [
        check_seller_is_entity(flat, entities),
        check_amount_consistency(fields, rules),
        check_tax_id_format(fields),
        check_invoice_number_format(fields),
        check_cross_source(fields),
    ]


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def fuse(
    qr: Optional[Dict[str, Any]] = None,
    ocr: Optional[Dict[str, Any]] = None,
    vlm: Optional[Dict[str, Any]] = None,
    entities: Optional[List[Dict]] = None,
    rules: AmountRules = DEFAULT_AMOUNT_RULES,
) -> FusionResult:
    """把三路结果融合成一条记录。

    qr  二维码解码结果（英文键）；None 或空表示解码失败，自动走降级路径。
    ocr qwen3.5-ocr 的 kv_result（中文键）。
    vlm qwen-vl-max 的原始字段（英文键，含 buyer_entity_id）。
    entities 购买方主体清单；rules 金额组阈值。两者都可在设置页修改，由调用方传入当前值。
    """
    entities = entities or []
    Q, O, V = normalize_qr(qr), normalize_ocr(ocr), normalize_vlm(vlm)
    res = FusionResult()
    F = res.fields

    # ---- 二维码字段：解出即确定；失败降级到 VLM 优先（OCR 会把号码抄进代码栏）
    for key in ("invoice_code", "invoice_number", "issue_date"):
        F[key] = fuse_field(key, [
            (SRC_QR, Q.get(key)),
            (SRC_VLM, V.get(key)),
            (SRC_OCR, O.get(key)),
        ])
        if Q.get(key) is None:
            res.degraded.append(key)

    # ---- 金额组：金额（含二维码读出的金额）、税率、税额、价税合计一起判定
    amount_fields, _ = fuse_amount_group(Q, V, O, rules)
    F.update(amount_fields)
    if Q.get("amount") is None:
        res.degraded.append("amount")

    # ---- 购买方：主体清单查表，识别值只作校验
    eid = V.get("buyer_entity_id")
    entity = next((e for e in entities if e["id"] == eid), None)
    for key, ekey in (("buyer_name", "name"), ("buyer_tax_id", "tax_id")):
        # 闭集模式下 VLM 输出的是 buyer_entity_id 而不是名称/税号，
        # 直接取 V.get(key) 会拿到 None，让前端看起来像“VLM 什么都没说”，
        # 交叉校验在这个字段上等于没做。这里把它选中的主体展开成具体值，
        # 否则回退到它自由输出的值（非闭集 prompt 版本）。
        vlm_entity = next((e for e in entities if e["id"] == eid), None)
        vlm_val = V.get(key)
        if vlm_val is None and vlm_entity:
            vlm_val = clean(key, vlm_entity[ekey])
        cand = {k: v for k, v in ((SRC_VLM, vlm_val), (SRC_OCR, O.get(key)))
                if v is not None}
        if entity:
            val = entity[ekey]
            mismatch = [s for s, v in cand.items() if v != clean(key, val)]
            F[key] = FieldResult(
                value=val, source=SRC_LOOKUP,
                confidence=CONF_LOW if mismatch else CONF_HIGH,
                candidates=cand,
                note=(f"按主体 {eid} 查表；{'/'.join(mismatch)} 识别值不一致"
                      if mismatch else f"按主体 {eid} 查表"),
            )
        else:
            F[key] = fuse_field(key, [(SRC_VLM, vlm_val), (SRC_OCR, O.get(key))])
            F[key].note = (f"主体 id {eid!r} 不在清单中，回落到识别值"
                           if eid else "模型未给出主体 id，回落到识别值")
            F[key].confidence = CONF_LOW
            res.degraded.append(key)

    # ---- 销售方：名称与税号都是 OCR 优先（实测两路不一致时 OCR 7/7 全对）
    F["seller_name"] = fuse_field("seller_name", [
        (SRC_OCR, O.get("seller_name")), (SRC_VLM, V.get("seller_name"))])
    F["seller_tax_id"] = fuse_field("seller_tax_id", [
        (SRC_OCR, O.get("seller_tax_id")), (SRC_VLM, V.get("seller_tax_id"))])

    # ---- 项目名称：只有 VLM 提供。按产品要求，无论哪一路给出、给出几路，都要人工复核
    F["item_name"] = fuse_field("item_name", [(SRC_VLM, V.get("item_name"))])
    if F["item_name"].value is not None:
        F["item_name"].confidence = CONF_LOW
        F["item_name"].note = "项目名称固定人工复核"

    # ---- 校验层
    res.checks = run_checks(F, entities, rules)
    seller_check = res.checks[0]

    if seller_check.status == STATUS_FAIL:
        for k in ("seller_name", "seller_tax_id"):
            if F[k].value is not None:
                F[k].value = None
                F[k].confidence = CONF_MISSING
                F[k].note = "命中 seller_is_entity，已置空"
                res.nulled.append(k)
        res.verdict = VERDICT_REJECTED
    elif any(c.status == STATUS_FAIL for c in res.checks) or res.low_confidence:
        res.verdict = VERDICT_WARN
    else:
        res.verdict = VERDICT_OK

    return res
