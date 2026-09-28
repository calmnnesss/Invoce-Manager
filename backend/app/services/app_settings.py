"""设置页可修改的业务配置：购买方主体清单、金额组校验规则。

存储
----
app_settings 表，一个配置项一行（value 是 JSON）。表里还没有的配置项用出厂默认值：
主体清单见 app/services/buyer_entities.json，金额规则见 fusion.AmountRules。

读取
----
识别在线程池里同步执行，不方便查库。所以启动时把配置载入内存，之后所有读取都拿
内存里的只读快照 current()；设置页保存时先写库，提交成功后再替换快照。
单进程部署下这样足够；多进程部署需要改成定期重载或加变更通知。

改动的影响
----------
- 主体清单会渲染进视觉模型的 prompt，并用于「销售方不能是本公司」的校验：只影响之后
  识别的发票，以及之后对已有发票的核对操作（重校验用当前清单）。
- 金额规则用于金额组三层判定：同上，已入库发票的判定不会自动重算
  （可用 backend/eval/refuse_amounts.py 按当前规则重跑金额组）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting
from app.services.fusion import DEFAULT_AMOUNT_RULES, AmountRules
from app.services.prompts import DEFAULT_BUYER_ENTITIES, DEFAULT_ENTITIES_LABEL, entities_version

logger = logging.getLogger(__name__)

KEY_BUYER_ENTITIES = "buyer_entities"
KEY_AMOUNT_RULES = "amount_rules"

MAX_ENTITIES = 50
MAX_RATES = 20
# 统一社会信用代码 18 位，旧的税务登记号 15 位
_TAX_ID = re.compile(r"[0-9A-Z]{15}|[0-9A-Z]{18}")
_ENTITY_ID = re.compile(r"E(\d{2,})")


class InvalidSetting(ValueError):
    """配置不合法。消息是中文，可以直接展示给用户。"""


@dataclass(frozen=True)
class ReviewSettings:
    """当前生效的业务配置（只读快照）。"""
    buyer_entities: Tuple[Tuple[str, str, str], ...]   # (id, name, tax_id)
    amount_rules: AmountRules
    entities_version: str                            # 清单指纹，写进每条 LLM 日志

    @property
    def entities(self) -> List[Dict[str, str]]:
        """融合层与 prompt 需要的形式。每次返回新列表，调用方随便改也不影响快照。"""
        return [{"id": i, "name": n, "tax_id": t} for i, n, t in self.buyer_entities]


def _snapshot(entities: List[Dict[str, str]], rules: AmountRules, label: str) -> ReviewSettings:
    return ReviewSettings(
        buyer_entities=tuple((e["id"], e["name"], e["tax_id"]) for e in entities),
        amount_rules=rules,
        entities_version=entities_version(entities, label),
    )


_DEFAULT = _snapshot(list(DEFAULT_BUYER_ENTITIES), DEFAULT_AMOUNT_RULES, DEFAULT_ENTITIES_LABEL)
_current: ReviewSettings = _DEFAULT


def current() -> ReviewSettings:
    return _current


def defaults() -> ReviewSettings:
    return _DEFAULT


# --------------------------------------------------------------------------
# 校验与规范化（纯函数）
# --------------------------------------------------------------------------
def normalize_buyer_entities(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """校验并规范化主体清单。不合法抛 InvalidSetting。

    已有主体的 id 原样保留（日志与历史发票的备注里用的是 id）；新增的按现有最大编号往后排。
    """
    if isinstance(items, (str, bytes, Mapping)):
        raise InvalidSetting("主体清单格式不正确")
    items = list(items)
    if not items:
        raise InvalidSetting("至少保留一个购买方主体")
    if len(items) > MAX_ENTITIES:
        raise InvalidSetting(f"最多 {MAX_ENTITIES} 个购买方主体")

    out: List[Dict[str, str]] = []
    seen_names, seen_taxes, used_ids = set(), set(), set()
    for i, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise InvalidSetting(f"第 {i} 行：格式不正确")
        name = str(item.get("name") or "").strip()
        tax_id = re.sub(r"[\s\-]", "", str(item.get("tax_id") or "").upper())
        if not name:
            raise InvalidSetting(f"第 {i} 行：名称不能为空")
        if len(name) > 255:
            raise InvalidSetting(f"第 {i} 行：名称过长，最多 255 个字符")
        if not _TAX_ID.fullmatch(tax_id):
            raise InvalidSetting(f"第 {i} 行：纳税人识别号应为 15 或 18 位数字或大写字母")
        name_key = re.sub(r"\s+", "", name)
        if name_key in seen_names:
            raise InvalidSetting(f"第 {i} 行：名称与前面的主体重复")
        if tax_id in seen_taxes:
            raise InvalidSetting(f"第 {i} 行：纳税人识别号与前面的主体重复")
        seen_names.add(name_key)
        seen_taxes.add(tax_id)

        eid = str(item.get("id") or "").strip().upper()
        keep = bool(_ENTITY_ID.fullmatch(eid)) and eid not in used_ids
        if keep:
            used_ids.add(eid)
        out.append({"id": eid if keep else "", "name": name, "tax_id": tax_id})

    next_no = max((int(_ENTITY_ID.fullmatch(e)[1]) for e in used_ids), default=0) + 1
    for e in out:
        if not e["id"]:
            e["id"] = f"E{next_no:02d}"
            next_no += 1
    return out


def _money(data: Mapping[str, Any], key: str, label: str, upper: float) -> float:
    try:
        value = float(data[key])
    except (KeyError, TypeError, ValueError):
        raise InvalidSetting(f"{label}需要填写数字") from None
    if not 0 <= value <= upper:
        raise InvalidSetting(f"{label}应在 0 到 {upper:g} 元之间")
    if round(value, 2) != value:
        raise InvalidSetting(f"{label}最多精确到分（两位小数）")
    return value


def normalize_amount_rules(data: Mapping[str, Any]) -> AmountRules:
    """校验并规范化金额规则。税率库按百分数给（13 表示 13%）。不合法抛 InvalidSetting。"""
    if not isinstance(data, Mapping):
        raise InvalidSetting("金额规则格式不正确")
    add = _money(data, "add_tolerance", "加法校验阈值", 1)
    mul = _money(data, "mul_tolerance", "乘法校验阈值", 1)
    rounding = _money(data, "rounding_tolerance", "多行明细凑整上限", 5)
    if rounding < mul:
        raise InvalidSetting("多行明细凑整上限不能小于乘法校验阈值")

    raw_rates = data.get("standard_rates")
    if not isinstance(raw_rates, (list, tuple)) or not raw_rates:
        raise InvalidSetting("标准税率库至少保留一个税率")
    if len(raw_rates) > MAX_RATES:
        raise InvalidSetting(f"标准税率库最多 {MAX_RATES} 个税率")
    percents = set()
    for raw in raw_rates:
        try:
            p = float(raw)
        except (TypeError, ValueError):
            raise InvalidSetting("税率需要填写数字，例如 13 表示 13%") from None
        if not 0 < p < 100:
            raise InvalidSetting("税率应大于 0% 且小于 100%（0% 不参与反推：免税、零税率、不征税写法不同）")
        if round(p, 2) != p:
            raise InvalidSetting("税率最多两位小数，例如 1.5")
        percents.add(p)
    rates = tuple(round(p / 100, 6) for p in sorted(percents, reverse=True))
    return AmountRules(add_tolerance=add, mul_tolerance=mul,
                       rounding_tolerance=rounding, standard_rates=rates)


def amount_rules_to_dict(rules: AmountRules) -> Dict[str, Any]:
    """接口与存储用的形式：税率库用百分数（0.17 -> 17）。"""
    return {
        "add_tolerance": rules.add_tolerance,
        "mul_tolerance": rules.mul_tolerance,
        "rounding_tolerance": rules.rounding_tolerance,
        "standard_rates": [round(r * 100, 4) for r in rules.standard_rates],
    }


# --------------------------------------------------------------------------
# 载入与保存
# --------------------------------------------------------------------------
async def load(db: AsyncSession) -> ReviewSettings:
    """从数据库载入配置，替换内存快照。存储的值不合法时回落到出厂值并记日志，不阻止启动。"""
    global _current
    rows = {r.key: r.value for r in (await db.execute(select(AppSetting))).scalars()}

    entities = list(DEFAULT_BUYER_ENTITIES)
    label = DEFAULT_ENTITIES_LABEL
    if KEY_BUYER_ENTITIES in rows:
        try:
            entities = normalize_buyer_entities(rows[KEY_BUYER_ENTITIES])
            label = "db"
        except InvalidSetting as e:
            logger.warning("app_settings.%s 不合法，使用出厂清单: %s", KEY_BUYER_ENTITIES, e)

    rules = DEFAULT_AMOUNT_RULES
    if KEY_AMOUNT_RULES in rows:
        try:
            rules = normalize_amount_rules(rows[KEY_AMOUNT_RULES])
        except InvalidSetting as e:
            logger.warning("app_settings.%s 不合法，使用出厂规则: %s", KEY_AMOUNT_RULES, e)

    _current = _snapshot(entities, rules, label)
    logger.info("业务配置已载入：主体清单 %s，金额规则 %s", _current.entities_version, rules)
    return _current


async def _upsert(db: AsyncSession, key: str, value: Any) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


async def save(
    db: AsyncSession,
    buyer_entities: Optional[List[Dict[str, str]]] = None,
    amount_rules: Optional[AmountRules] = None,
) -> ReviewSettings:
    """写库并提交，成功后替换内存快照。入参应已经过 normalize_* 校验。"""
    global _current
    if buyer_entities is not None:
        await _upsert(db, KEY_BUYER_ENTITIES, buyer_entities)
    if amount_rules is not None:
        await _upsert(db, KEY_AMOUNT_RULES, amount_rules_to_dict(amount_rules))
    await db.commit()

    entities = buyer_entities if buyer_entities is not None else _current.entities
    label = "db" if buyer_entities is not None else _current.entities_version.split("#")[0]
    rules = amount_rules if amount_rules is not None else _current.amount_rules
    _current = _snapshot(entities, rules, label)
    return _current
