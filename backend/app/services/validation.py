"""发票字段后处理校验（Postprocess v4）。

设计原则：
  1. 纯函数 —— 不碰数据库、不发网络请求，因此可以直接拿历史日志离线回放，
     验证规则效果不需要重新调用模型。
  2. 不改写值 —— 除了 seller_is_entity 触发时按要求置空，其余规则只报警，
     绝不猜测或修正字段值。
  3. 三态判定 —— pass / fail / skipped。字段缺失导致算不了的情况必须单列为
     skipped，混进 pass 会让通过率虚高，混进 fail 会误伤。

三条规则：
  seller_is_entity       销售方落在报销主体清单中 -> rejected（销售方置空）
  amount_sum             |金额 + 税额 - 价税合计| > 0.01 -> warn
  tax_rate_consistency   |税额 - 金额 x 税率| > 0.01 -> warn

税率一致性用差值而非比值（tax_amount / amount 与 tax_rate 相比），原因：
  - amount 为 0 时比值会除零
  - 小额发票的分位四舍五入会让比值偏差被放大
差值写法与 amount_sum 同量纲，阈值可以共用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional

POSTPROCESS_VERSION = "v4"

# 金额类比较的容差（元）。发票金额精确到分，0.01 允许一个分位的舍入。
AMOUNT_TOLERANCE = 0.01

VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_REJECTED = "rejected"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_SKIPPED = "skipped"


@dataclass
class Check:
    rule_id: str
    status: str
    detail: str = ""
    # 结果涉及的字段：失败时是出问题的字段，通过时是实际参与校验的字段（可能为空）。
    # 前端据此把问题贴到具体字段下，不必去解析 detail 文本。
    fields: List[str] = dc_field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"rule_id": self.rule_id, "status": self.status,
                "detail": self.detail, "fields": list(self.fields)}


@dataclass
class ValidationResult:
    verdict: str
    checks: List[Check] = dc_field(default_factory=list)
    fields: Dict[str, Any] = dc_field(default_factory=dict)
    nulled: List[str] = dc_field(default_factory=list)

    @property
    def failed(self) -> List[str]:
        return [c.rule_id for c in self.checks if c.status == STATUS_FAIL]

    @property
    def skipped(self) -> List[str]:
        return [c.rule_id for c in self.checks if c.status == STATUS_SKIPPED]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "nulled": list(self.nulled),
            "checks": [c.as_dict() for c in self.checks],
        }


# --------------------------------------------------------------------------
# 解析helper —— 只做「能不能读成数」的判断，不改写原值
# --------------------------------------------------------------------------
def parse_amount(value: Any) -> Optional[float]:
    """把字段值读成金额。读不出来返回 None（交给调用方判为 skipped）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("，", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def parse_rate(value: Any) -> Optional[float]:
    """把税率读成小数。'17%' -> 0.17，'免税'/'不征税' -> 0.0。读不出返回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if any(k in s for k in ("免税", "不征税", "免征", "零税率")):
        return 0.0
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        v = float(m.group())
    except ValueError:
        return None
    # "17%" 与 "0.17" 都可能出现
    return v / 100.0 if "%" in s or v > 1 else v


def _norm_tax_id(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[\s\-]", "", str(value)).upper()


def _norm_name(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value))


# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------
def check_seller_is_entity(fields: Dict[str, Any], entities: List[Dict]) -> Check:
    """销售方不可能是报销主体清单里的公司 —— 这是硬矛盾。"""
    names = {_norm_name(e["name"]) for e in entities}
    taxes = {_norm_tax_id(e["tax_id"]) for e in entities}

    sn = _norm_name(fields.get("seller_name"))
    st = _norm_tax_id(fields.get("seller_tax_id"))

    if not sn and not st:
        return Check("seller_is_entity", STATUS_SKIPPED, "销售方名称与税号均为空")

    hits, hit_fields = [], []
    if sn and sn in names:
        hits.append(f"名称命中清单: {fields.get('seller_name')}")
        hit_fields.append("seller_name")
    if st and st in taxes:
        hits.append(f"税号命中清单: {fields.get('seller_tax_id')}")
        hit_fields.append("seller_tax_id")

    if hits:
        return Check("seller_is_entity", STATUS_FAIL, "；".join(hits), hit_fields)
    checked = [k for k, v in (("seller_name", sn), ("seller_tax_id", st)) if v]
    return Check("seller_is_entity", STATUS_PASS, fields=checked)


def check_amount_sum(fields: Dict[str, Any]) -> Check:
    """金额 + 税额 = 价税合计。"""
    a = parse_amount(fields.get("amount"))
    t = parse_amount(fields.get("tax_amount"))
    tot = parse_amount(fields.get("total_with_tax"))

    involved = ["amount", "tax_amount", "total_with_tax"]
    missing = [k for k, v in (("amount", a), ("tax_amount", t), ("total_with_tax", tot))
               if v is None]
    if missing:
        return Check("amount_sum", STATUS_SKIPPED, f"字段缺失或非数值: {', '.join(missing)}")

    diff = abs(a + t - tot)
    if diff > AMOUNT_TOLERANCE:
        return Check(
            "amount_sum", STATUS_FAIL,
            f"{a:.2f} + {t:.2f} = {a + t:.2f}，与价税合计 {tot:.2f} 相差 {diff:.2f}",
            involved,
        )
    return Check("amount_sum", STATUS_PASS, fields=involved)


def check_tax_rate_consistency(fields: Dict[str, Any]) -> Check:
    """税额应约等于 金额 x 税率。"""
    a = parse_amount(fields.get("amount"))
    t = parse_amount(fields.get("tax_amount"))
    r = parse_rate(fields.get("tax_rate"))

    missing = []
    if a is None:
        missing.append("amount")
    if t is None:
        missing.append("tax_amount")
    if r is None:
        missing.append("tax_rate")
    if missing:
        return Check("tax_rate_consistency", STATUS_SKIPPED,
                     f"字段缺失或无法解析: {', '.join(missing)}")

    involved = ["amount", "tax_amount", "tax_rate"]
    expected = a * r
    diff = abs(t - expected)
    if diff > AMOUNT_TOLERANCE:
        return Check(
            "tax_rate_consistency", STATUS_FAIL,
            f"金额 {a:.2f} x 税率 {r:.4f} = {expected:.2f}，"
            f"与税额 {t:.2f} 相差 {diff:.2f}",
            involved,
        )
    return Check("tax_rate_consistency", STATUS_PASS, fields=involved)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def validate(fields: Dict[str, Any], entities: List[Dict]) -> ValidationResult:
    """对一条抽取结果跑全部校验。

    返回的 fields 是副本；只有 seller_is_entity 触发时会把销售方两个字段置空，
    其余规则一律不改写值。
    """
    out = dict(fields)
    checks = [
        check_seller_is_entity(fields, entities),
        check_amount_sum(fields),
        check_tax_rate_consistency(fields),
    ]

    nulled: List[str] = []
    verdict = VERDICT_OK
    for c in checks:
        if c.status != STATUS_FAIL:
            continue
        if c.rule_id == "seller_is_entity":
            for k in ("seller_name", "seller_tax_id"):
                if out.get(k) is not None:
                    out[k] = None
                    nulled.append(k)
            verdict = VERDICT_REJECTED
        elif verdict != VERDICT_REJECTED:
            verdict = VERDICT_WARN

    return ValidationResult(verdict=verdict, checks=checks, fields=out, nulled=nulled)
