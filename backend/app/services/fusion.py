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
   要么查表（购买方主体清单），要么计算（税额、价税合计），
   要么多路比对后取优先线。识别结果本身一律降级为校验证据。
3. 不一致本身就是产出。融合不追求猜对，而是在猜不准时如实标记，
   核心指标是静默错误率而不是准确率。

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
from typing import Any, Dict, List, Optional

from app.services.validation import (
    AMOUNT_TOLERANCE,
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
    problems = []
    for key in ("buyer_tax_id", "seller_tax_id"):
        v = fields.get(key, FieldResult()).value
        if not v:
            continue
        if len(v) not in (15, 18):
            problems.append(f"{key} 位数为 {len(v)}，应为 15 或 18")
            continue
        ok = _uscc_valid(v)
        if ok is False:
            problems.append(f"{key} 统一社会信用代码校验位不通过")
    if not problems:
        has_any = any(fields.get(k, FieldResult()).value for k in
                      ("buyer_tax_id", "seller_tax_id"))
        return Check("tax_id_format", STATUS_PASS if has_any else STATUS_SKIPPED,
                     "" if has_any else "两个税号均为空")
    return Check("tax_id_format", STATUS_FAIL, "；".join(problems))


def check_invoice_number_format(fields: Dict[str, FieldResult]) -> Check:
    """专票代码 10 位、号码 8 位。位数不符最常见的成因是两栏互抄。"""
    problems = []
    code = fields.get("invoice_code", FieldResult()).value
    num = fields.get("invoice_number", FieldResult()).value
    if code and not re.fullmatch(r"\d{10,12}", code):
        problems.append(f"发票代码位数异常: {code}")
    if num and not re.fullmatch(r"\d{8,10}", num):
        problems.append(f"发票号码位数异常: {num}")
    if code and num and code == num:
        problems.append("发票代码与号码相同")
    if problems:
        return Check("invoice_number_format", STATUS_FAIL, "；".join(problems))
    if not code and not num:
        return Check("invoice_number_format", STATUS_SKIPPED, "代码与号码均为空")
    return Check("invoice_number_format", STATUS_PASS)


def check_computed_vs_recognized(fields: Dict[str, FieldResult]) -> Check:
    """税额与价税合计是算出来的，识别值降级为校验值。

    v4 里 amount_sum 和 tax_rate_consistency 在 v6 变成恒真（数据本就按公式生成），
    所以它们的比对对象换成了「计算值 vs 各路识别值」。检测能力没丢，反而更强，
    因为第一次有了一个可信基准去比。
    """
    problems = []
    for key in ("tax_amount", "total_with_tax"):
        fr = fields.get(key)
        if not fr or fr.source != SRC_COMPUTED or fr.value is None:
            continue
        computed = parse_amount(fr.value)
        for src, raw in fr.candidates.items():
            if src == SRC_COMPUTED:
                continue
            got = parse_amount(raw)
            if got is None or computed is None:
                continue
            if abs(got - computed) > AMOUNT_TOLERANCE:
                problems.append(
                    f"{key}: 计算值 {computed:.2f}，{src} 识别为 {got:.2f}"
                )
    if problems:
        return Check("computed_vs_recognized", STATUS_FAIL, "；".join(problems))
    return Check("computed_vs_recognized", STATUS_PASS)


def check_cross_source(fields: Dict[str, FieldResult]) -> Check:
    low = [k for k, v in fields.items()
           if v.confidence == CONF_LOW and len({x for x in v.candidates.values()}) > 1]
    if low:
        detail = "；".join(
            f"{k}: " + ", ".join(f"{s}={v}" for s, v in fields[k].candidates.items())
            for k in low
        )
        return Check("cross_source_mismatch", STATUS_FAIL, detail)
    return Check("cross_source_mismatch", STATUS_PASS)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def fuse(
    qr: Optional[Dict[str, Any]] = None,
    ocr: Optional[Dict[str, Any]] = None,
    vlm: Optional[Dict[str, Any]] = None,
    entities: Optional[List[Dict]] = None,
) -> FusionResult:
    """把三路结果融合成一条记录。

    qr  二维码解码结果（英文键）；None 或空表示解码失败，自动走降级路径。
    ocr qwen3.5-ocr 的 kv_result（中文键）。
    vlm qwen-vl-max 的原始字段（英文键，含 buyer_entity_id）。
    """
    entities = entities or []
    Q, O, V = normalize_qr(qr), normalize_ocr(ocr), normalize_vlm(vlm)
    res = FusionResult()
    F = res.fields

    # ---- 二维码四字段：解出即确定；失败降级到 VLM 优先（OCR 会把号码抄进代码栏）
    for key in ("invoice_code", "invoice_number", "issue_date", "amount"):
        F[key] = fuse_field(key, [
            (SRC_QR, Q.get(key)),
            (SRC_VLM, V.get(key)),
            (SRC_OCR, O.get(key)),
        ])
        if Q.get(key) is None:
            res.degraded.append(key)

    # ---- 购买方：主体清单查表，识别值只作校验
    eid = V.get("buyer_entity_id")
    entity = next((e for e in entities if e["id"] == eid), None)
    for key, ekey in (("buyer_name", "name"), ("buyer_tax_id", "tax_id")):
        cand = {k: v for k, v in ((SRC_VLM, V.get(key)), (SRC_OCR, O.get(key)))
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
            F[key] = fuse_field(key, [(SRC_VLM, V.get(key)), (SRC_OCR, O.get(key))])
            F[key].note = (f"主体 id {eid!r} 不在清单中，回落到识别值"
                           if eid else "模型未给出主体 id，回落到识别值")
            F[key].confidence = CONF_LOW
            res.degraded.append(key)

    # ---- 销售方：名称与税号都是 OCR 优先（实测两路不一致时 OCR 7/7 全对）
    F["seller_name"] = fuse_field("seller_name", [
        (SRC_OCR, O.get("seller_name")), (SRC_VLM, V.get("seller_name"))])
    F["seller_tax_id"] = fuse_field("seller_tax_id", [
        (SRC_OCR, O.get("seller_tax_id")), (SRC_VLM, V.get("seller_tax_id"))])

    # ---- 税率：VLM 优先
    F["tax_rate"] = fuse_field("tax_rate", [
        (SRC_VLM, V.get("tax_rate")), (SRC_OCR, O.get("tax_rate"))])

    # ---- 项目名称：只有 VLM 提供
    F["item_name"] = fuse_field("item_name", [(SRC_VLM, V.get("item_name"))])

    # ---- 税额与价税合计：算出来的，识别值降级为校验值
    amount = parse_amount(F["amount"].value)
    rate = parse_rate(F["tax_rate"].value)
    recog_tax = {s: v for s, v in ((SRC_VLM, V.get("tax_amount")),
                                   (SRC_OCR, O.get("tax_amount"))) if v}
    recog_tot = {s: v for s, v in ((SRC_VLM, V.get("total_with_tax")),
                                   (SRC_OCR, O.get("total_with_tax"))) if v}

    if amount is not None and rate is not None:
        tax = round(amount * rate, 2)
        total = round(amount + tax, 2)
        base_conf = (CONF_CERTAIN
                     if F["amount"].confidence == CONF_CERTAIN
                     and F["tax_rate"].confidence in (CONF_CERTAIN, CONF_HIGH)
                     else CONF_HIGH)
        F["tax_amount"] = FieldResult(f"{tax:.2f}", SRC_COMPUTED, base_conf,
                                      recog_tax, "金额 x 税率")
        F["total_with_tax"] = FieldResult(f"{total:.2f}", SRC_COMPUTED, base_conf,
                                          recog_tot, "金额 + 税额")
    else:
        # 算不了就回落到识别值，并标记
        missing = "金额" if amount is None else "税率"
        for key, cands in (("tax_amount", recog_tax), ("total_with_tax", recog_tot)):
            src_list = [(SRC_VLM, V.get(key)), (SRC_OCR, O.get(key))]
            F[key] = fuse_field(key, src_list)
            F[key].confidence = CONF_LOW if F[key].value else CONF_MISSING
            F[key].note = f"{missing}缺失，无法计算，回落到识别值"
        res.degraded.append("computed_amounts")

    # ---- 校验层
    flat = {k: v.value for k, v in F.items()}
    seller_check = check_seller_is_entity(flat, entities)
    res.checks = [
        seller_check,
        check_computed_vs_recognized(F),
        check_tax_id_format(F),
        check_invoice_number_format(F),
        check_cross_source(F),
    ]

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
