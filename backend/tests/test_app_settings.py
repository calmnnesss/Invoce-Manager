"""设置页的业务配置（app_settings）：校验与规范化、载入与保存、接口，以及配置对识别与判定的实际影响。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.app_setting import AppSetting
from app.services import app_settings, llm_service
from app.services.app_settings import (
    InvalidSetting,
    amount_rules_to_dict,
    normalize_amount_rules,
    normalize_buyer_entities,
)
from app.services.fusion import DEFAULT_AMOUNT_RULES, AmountRules, fuse_amount_group, reconcile_amounts
from app.services.prompts import (
    _ENTITY_TABLE_SLOT,
    DEFAULT_BUYER_ENTITIES,
    PROMPT_VERSIONS,
    get_vision_prompt,
)

CUSTOM = [{"id": "E01", "name": "杭州示例科技有限公司", "tax_id": "91330100MA2XXXXX0A"}]
DEFAULT_RULES_DICT = amount_rules_to_dict(DEFAULT_AMOUNT_RULES)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # 每条测试从出厂配置开始，结束后恢复；也不写 LLM 调试日志
    monkeypatch.setattr(app_settings, "_current", app_settings.defaults())
    monkeypatch.setattr(llm_service, "_resolve_dump_dir", lambda: None)


class FakeSession:
    """够 app_settings.load / save 与设置接口用的最小 AsyncSession 替身，不碰真实数据库。"""

    def __init__(self, stored=None, fail_commit=False):
        self.rows = {k: AppSetting(key=k, value=v) for k, v in (stored or {}).items()}
        self.others = []
        self.fail_commit = fail_commit
        self.committed = False

    async def execute(self, _stmt):
        rows = list(self.rows.values())
        return SimpleNamespace(scalars=lambda: iter(rows))

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, obj):
        if isinstance(obj, AppSetting):
            self.rows[obj.key] = obj
        else:
            self.others.append(obj)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("数据库不可用")
        self.committed = True


def group(amount, rate, tax, total):
    return {"amount": amount, "tax_rate": rate, "tax_amount": tax, "total_with_tax": total}


# --------------------------------------------------------------------------
# 1. 主体清单的校验与规范化
# --------------------------------------------------------------------------
def test_entities_are_normalized_and_keep_ids():
    """名称去首尾空格，税号去空格与连字符并转大写；已有主体保留 id，新增的接着最大编号往后排。"""
    out = normalize_buyer_entities([
        {"id": "E03", "name": "  甲公司 ", "tax_id": "91440300 ma5f-xxxx1a"},
        {"name": "乙公司", "tax_id": "440300083885999"},
        {"id": "e01", "name": "丙公司", "tax_id": "440300083885998"},
    ])
    assert out == [
        {"id": "E03", "name": "甲公司", "tax_id": "91440300MA5FXXXX1A"},
        {"id": "E04", "name": "乙公司", "tax_id": "440300083885999"},
        {"id": "E01", "name": "丙公司", "tax_id": "440300083885998"},
    ]


def test_duplicate_entity_id_gets_a_new_one():
    """id 在日志里用来对应主体，不能重复：后出现的重复 id 重新分配。"""
    out = normalize_buyer_entities([
        {"id": "E01", "name": "甲公司", "tax_id": "440300083885991"},
        {"id": "E01", "name": "乙公司", "tax_id": "440300083885992"},
    ])
    assert [e["id"] for e in out] == ["E01", "E02"]


@pytest.mark.parametrize("items, message", [
    ([], "至少保留一个"),
    ([{"name": " ", "tax_id": "440300083885931"}], "名称不能为空"),
    ([{"name": "甲", "tax_id": "4403000838859"}], "15 或 18 位"),
    ([{"name": "甲", "tax_id": "4403000838859312"}], "15 或 18 位"),
    ([{"name": "甲", "tax_id": "44030008388593！"}], "15 或 18 位"),
    ([{"name": "甲 公司", "tax_id": "440300083885991"},
      {"name": "甲公司", "tax_id": "440300083885992"}], "名称与前面的主体重复"),
    ([{"name": "甲", "tax_id": "440300083885991"},
      {"name": "乙", "tax_id": "440300083885991"}], "纳税人识别号与前面的主体重复"),
    ([{"name": f"公司{i}", "tax_id": f"4403000838{i:05d}"} for i in range(51)], "最多 50"),
    ({"name": "甲", "tax_id": "440300083885991"}, "格式不正确"),
    (["甲"], "第 1 行：格式不正确"),
])
def test_invalid_entities_are_rejected(items, message):
    with pytest.raises(InvalidSetting, match=message):
        normalize_buyer_entities(items)


# --------------------------------------------------------------------------
# 2. 金额规则的校验与规范化
# --------------------------------------------------------------------------
def test_default_rules_round_trip_exactly():
    """接口与存储用百分数；来回转换后与出厂规则逐位相等，税率库不能多出浮点误差。"""
    assert normalize_amount_rules(DEFAULT_RULES_DICT) == DEFAULT_AMOUNT_RULES
    assert DEFAULT_RULES_DICT["standard_rates"] == [17, 16, 13, 11, 10, 9, 6, 5, 3, 1.5, 1]


def test_rates_are_deduplicated_and_sorted():
    rules = normalize_amount_rules(dict(DEFAULT_RULES_DICT, standard_rates=[6, 13, 6, 1.5]))
    assert rules.standard_rates == (0.13, 0.06, 0.015)


@pytest.mark.parametrize("changes, message", [
    ({"add_tolerance": None}, "加法校验阈值需要填写数字"),
    ({"add_tolerance": -0.01}, "加法校验阈值应在 0 到 1 元之间"),
    ({"mul_tolerance": 1.5}, "乘法校验阈值应在 0 到 1 元之间"),
    ({"rounding_tolerance": 6}, "多行明细凑整上限应在 0 到 5 元之间"),
    ({"add_tolerance": 0.001}, "最多精确到分"),
    ({"rounding_tolerance": 0.01}, "不能小于乘法校验阈值"),
    ({"standard_rates": []}, "至少保留一个税率"),
    ({"standard_rates": [0]}, "大于 0%"),
    ({"standard_rates": [100]}, "小于 100%"),
    ({"standard_rates": ["abc"]}, "需要填写数字"),
    ({"standard_rates": [13.555]}, "两位小数"),
    ({"standard_rates": list(range(1, 22))}, "最多 20"),
])
def test_invalid_rules_are_rejected(changes, message):
    with pytest.raises(InvalidSetting, match=message):
        normalize_amount_rules(dict(DEFAULT_RULES_DICT, **changes))


def test_missing_rule_field_is_rejected():
    data = dict(DEFAULT_RULES_DICT)
    del data["mul_tolerance"]
    with pytest.raises(InvalidSetting, match="乘法校验阈值需要填写数字"):
        normalize_amount_rules(data)
    with pytest.raises(InvalidSetting, match="格式不正确"):
        normalize_amount_rules([])


# --------------------------------------------------------------------------
# 3. 规则确实作用在金额组三层判定上
# --------------------------------------------------------------------------
def test_add_tolerance_controls_layer_one():
    v = group("100.00", "13%", "13.00", "113.03")          # 加法差 0.03
    assert reconcile_amounts(v).rule == "conflict"
    assert reconcile_amounts(v, rules=AmountRules(add_tolerance=0.05)).rule == "both"


def test_mul_and_rounding_tolerances():
    v = group("100.00", "13%", "13.03", "113.03")          # 加法成立，乘法差 0.03
    assert reconcile_amounts(v).rule == "rounding"          # 出厂：在 0.05 的凑整上限内放行
    assert reconcile_amounts(v, rules=AmountRules(mul_tolerance=0.05)).rule == "both"
    strict = AmountRules(rounding_tolerance=0.02)
    assert reconcile_amounts(v, rules=strict).rule == "rate_mismatch"   # 收紧后交人工


def test_rate_library_controls_rate_derivation():
    v = group("100.00", None, "8.00", "108.00")             # 反推税率 8%，出厂库里没有
    assert reconcile_amounts(v).rule == "rate_unknown"
    custom = AmountRules(standard_rates=DEFAULT_AMOUNT_RULES.standard_rates + (0.08,))
    check = reconcile_amounts(v, rules=custom)
    assert check.rule == "derive_rate" and check.values["tax_rate"] == "8%"

    fields, check = fuse_amount_group({}, v, {}, custom)
    assert check.status == "pass"
    assert (fields["tax_rate"].value, fields["tax_rate"].source) == ("8%", "computed")


# --------------------------------------------------------------------------
# 4. 主体清单确实作用在 prompt 与响应解析上
# --------------------------------------------------------------------------
def test_prompt_renders_given_entities():
    _, default_prompt, _ = get_vision_prompt("v3.1")
    assert get_vision_prompt("v3.1", DEFAULT_BUYER_ENTITIES)[1] == default_prompt
    assert DEFAULT_BUYER_ENTITIES[1]["name"] in default_prompt

    _, custom_prompt, _ = get_vision_prompt("v3.1", CUSTOM)
    assert "E01    杭州示例科技有限公司    91330100MA2XXXXX0A" in custom_prompt
    assert DEFAULT_BUYER_ENTITIES[1]["name"] not in custom_prompt


@pytest.mark.parametrize("version", sorted(PROMPT_VERSIONS))
def test_no_prompt_keeps_the_placeholder(version):
    for entities in (None, CUSTOM):
        assert _ENTITY_TABLE_SLOT not in get_vision_prompt(version, entities)[1]


def test_response_parsing_uses_the_same_snapshot():
    """模型选的主体 id 按渲染 prompt 时那份清单查表；销售方落在清单里照样被拒。"""
    content = json.dumps({
        "buyer_entity_id": "E01", "invoice_number": "12345678",
        "seller_name": "杭州示例科技有限公司", "seller_tax_id": "91330100MA2XXXXX0A",
    })
    cfg = app_settings._snapshot(CUSTOM, DEFAULT_AMOUNT_RULES, "test")
    fields = llm_service.LLMService()._parse_json_response(content, "test", cfg=cfg)
    assert (fields["buyer_name"], fields["buyer_tax_id"]) == ("杭州示例科技有限公司", "91330100MA2XXXXX0A")
    assert fields["seller_name"] is None and fields["seller_tax_id"] is None

    # 不传快照时读当前配置
    fields = llm_service.LLMService()._parse_json_response(content, "test")
    assert fields["buyer_name"] == DEFAULT_BUYER_ENTITIES[0]["name"]
    assert fields["seller_name"] == "杭州示例科技有限公司"


# --------------------------------------------------------------------------
# 5. 载入与保存
# --------------------------------------------------------------------------
def test_entities_property_returns_copies():
    app_settings.current().entities[0]["name"] = "改掉"
    assert app_settings.current().entities[0]["name"] == DEFAULT_BUYER_ENTITIES[0]["name"]


def test_load_reads_stored_values():
    db = FakeSession({"buyer_entities": CUSTOM,
                      "amount_rules": dict(DEFAULT_RULES_DICT, add_tolerance=0.03)})
    cfg = asyncio.run(app_settings.load(db))
    assert app_settings.current() is cfg
    assert cfg.entities == CUSTOM
    assert cfg.amount_rules == AmountRules(add_tolerance=0.03)
    assert cfg.entities_version.startswith("db#")


def test_load_falls_back_to_defaults_on_invalid_values():
    """库里的值坏了不能阻止启动：记日志、用出厂值。"""
    db = FakeSession({"buyer_entities": [{"name": "", "tax_id": "x"}],
                      "amount_rules": {"add_tolerance": 9}})
    assert asyncio.run(app_settings.load(db)) == app_settings.defaults()


def test_save_swaps_snapshot_only_after_commit():
    rules = AmountRules(add_tolerance=0.03)
    with pytest.raises(RuntimeError):
        asyncio.run(app_settings.save(FakeSession(fail_commit=True), amount_rules=rules))
    assert app_settings.current() == app_settings.defaults()

    db = FakeSession()
    cfg = asyncio.run(app_settings.save(db, amount_rules=rules))
    assert db.committed and app_settings.current() is cfg
    assert cfg.amount_rules == rules
    assert cfg.entities == app_settings.defaults().entities   # 只保存了规则，清单不变
    assert db.rows["amount_rules"].value == amount_rules_to_dict(rules)
    assert "buyer_entities" not in db.rows


# --------------------------------------------------------------------------
# 6. 接口
# --------------------------------------------------------------------------
@pytest.fixture
def api():
    db = FakeSession()

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app), db
    app.dependency_overrides.pop(get_db, None)


def test_get_review_settings(api):
    client, _ = api
    body = client.get("/api/settings/review").json()
    assert body["buyer_entities"] == DEFAULT_BUYER_ENTITIES
    assert body["amount_rules"] == DEFAULT_RULES_DICT
    assert body["defaults"] == {"buyer_entities": DEFAULT_BUYER_ENTITIES,
                                "amount_rules": DEFAULT_RULES_DICT}


def test_put_buyer_entities(api):
    client, db = api
    res = client.put("/api/settings/buyer-entities",
                     json={"entities": [{"name": "甲", "tax_id": "bad"}]})
    assert res.status_code == 400 and "15 或 18 位" in res.json()["detail"]
    assert app_settings.current() == app_settings.defaults() and not db.committed

    res = client.put("/api/settings/buyer-entities", json={"entities": [
        {"id": "E01", "name": " 杭州示例科技有限公司", "tax_id": "91330100ma2xxxxx0a"},
        {"name": "新主体有限公司", "tax_id": "440300083885999"},
    ]})
    assert res.status_code == 200
    saved = [CUSTOM[0], {"id": "E02", "name": "新主体有限公司", "tax_id": "440300083885999"}]
    assert res.json()["buyer_entities"] == saved
    assert app_settings.current().entities == saved
    assert db.rows["buyer_entities"].value == saved
    audit, = db.others
    assert (audit.entity_type, audit.action) == ("settings", "update_buyer_entities")
    assert audit.old_value == {"value": DEFAULT_BUYER_ENTITIES}


def test_put_amount_rules(api):
    client, _ = api
    res = client.put("/api/settings/amount-rules",
                     json=dict(DEFAULT_RULES_DICT, add_tolerance=None))
    assert res.status_code == 400 and res.json()["detail"] == "加法校验阈值需要填写数字"

    res = client.put("/api/settings/amount-rules",
                     json=dict(DEFAULT_RULES_DICT, mul_tolerance=0.03, standard_rates=[13, 8]))
    assert res.status_code == 200
    assert res.json()["amount_rules"]["standard_rates"] == [13, 8]
    assert app_settings.current().amount_rules == AmountRules(mul_tolerance=0.03,
                                                              standard_rates=(0.13, 0.08))
