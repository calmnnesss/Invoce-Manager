"""融合层金额组判定（fusion.reconcile_amounts）与人工核对阶段（review.py）的业务不变量。

每条测试守护一条规则，写在各自的 docstring 里。数据形态取自评测集 b10
（即库中发票 3）：销售方两路不一致、项目名称只有视觉模型给出、金额四项自洽。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.fusion import FieldResult, check_tax_id_format, fuse, reconcile_amounts
from app.services.prompts import BUYER_ENTITIES
from app.services.review import (
    InvalidValue,
    apply_to_invoice,
    fusion_row_values,
    normalize_input,
    reconcile_after_edit,
    resolve_set,
    restore_auto,
    revalidate,
)

EVAL = Path(__file__).resolve().parents[1] / "eval"
LOGS = Path(__file__).resolve().parents[1] / "logs" / "llm"

ENTITY = BUYER_ENTITIES[0]  # E01，评测集的真实购买方

OCR_B10 = {
    "价税合计": "叁仟肆佰玖拾伍圆整", "发票代码": "4400154130", "发票号码": "12270859",
    "开票日期": "2016年06月12日", "税率": "17%", "税额": "税额",
    "购买方名称": "深圳市购机汇网络有限公司", "购买方纳税人识别号": "440300083885931",
    "金额": "金额", "销售方名称": "广州晶东贸易有限公司",
    "销售方纳税人识别号": "91440101664041243T",
}
VLM_B10 = {
    "buyer_entity_id": "E01", "invoice_number": "12270859", "invoice_code": "4400154130",
    "issue_date": "2016-06-12", "seller_name": "广州品乐贸易有限公司",
    "seller_tax_id": "91440101664041243T",
    "item_name": "*移动联通电信4G手机*小米 红米 3 全网通版 时尚金色",
    "total_with_tax": "3495.00", "amount": "2987.18", "tax_amount": "507.82",
    "tax_rate": "17%",
}


def rows_of(res):
    """与入库完全相同的映射（invoice_service 也调用 fusion_row_values）。"""
    return [SimpleNamespace(id=i, **fusion_row_values(k, fr))
            for i, (k, fr) in enumerate(res.fields.items(), start=1)]


def by_name(rows):
    return {r.field_name: r for r in rows}


def status_of(checks, rule_id):
    return next(c for c in checks if c.rule_id == rule_id).status


def b10_rows(**vlm_overrides):
    return rows_of(fuse(ocr=OCR_B10, vlm=dict(VLM_B10, **vlm_overrides), entities=BUYER_ENTITIES))


def group(amount, rate, tax, total):
    return {"amount": amount, "tax_rate": rate, "tax_amount": tax, "total_with_tax": total}


# --------------------------------------------------------------------------
# 1. 算法决策不变：入库后的重校验与 fuse() 给出同样的结论
# --------------------------------------------------------------------------
def _vlm_raw_from_logs(fname):
    """与 eval/simulate_fusion.py 相同：取该图最新一次 v3.1 + 四主体清单的原始响应。"""
    cands = []
    for p in LOGS.rglob(f"*_{fname}_*.log"):
        t = p.read_text(encoding="utf-8", errors="replace")
        if "Prompt     : v3.1" not in t or "no-E05" not in t:
            continue
        cands.append((p.name.split("_")[0], t))
    if not cands:
        return {}
    _, t = max(cands, key=lambda x: x[0])
    i = t.find("[1] 原始响应")
    j = t.find("{", i)
    depth = 0
    for k in range(j, len(t)):
        if t[k] == "{":
            depth += 1
        elif t[k] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[j:k + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def _eval_samples():
    ocr_files = sorted((EVAL / "ocr_probe_out").glob("*_px8192_rot0_*.json"))
    qr_path = EVAL / "qr_result.json"
    qr = json.loads(qr_path.read_text(encoding="utf-8")) if qr_path.exists() else {}
    for p in ocr_files:
        j = json.loads(p.read_text(encoding="utf-8"))
        f = j["file"] + ".jpg"
        yield f, qr.get(f), j.get("kv_result") or {}, _vlm_raw_from_logs(f)


def _assert_same_conclusion(res, checks, verdict, label):
    assert verdict == res.verdict, label
    got = [(c.rule_id, c.status, sorted(c.fields)) for c in checks]
    want = [(c.rule_id, c.status, sorted(c.fields)) for c in res.checks]
    assert got == want, label


def test_revalidate_reaches_fuse_conclusion_on_eval_set():
    """刚入库、没人动过的发票，重校验必须与 fuse() 的判定和逐条规则结论完全一致。

    守护「算法决策不变」：详情页每次操作后都会重校验，若口径漂移，
    一张票被人点开、什么都没改，判定就可能变。
    """
    samples = list(_eval_samples())
    if not samples:
        pytest.skip("eval/ocr_probe_out 下没有评测数据")
    for f, qr, ocr, vlm in samples:
        res = fuse(qr=qr, ocr=ocr, vlm=vlm, entities=BUYER_ENTITIES)
        checks, verdict = revalidate(rows_of(res), res.nulled, BUYER_ENTITIES)
        _assert_same_conclusion(res, checks, verdict, f)


def test_revalidate_keeps_fuse_rejection_after_nulling():
    """fuse() 置空了销售方的票（rejected），入库后重校验仍是 rejected。

    置空后两个销售方字段都是空的，规则本身只会得出 skipped；
    不沿用失败结论的话，判定会悄悄从 rejected 降成 warn。
    """
    # 销售方是 OCR 优先，所以要两路都把本公司读成销售方（买卖双方读反）才会触发
    ocr = dict(OCR_B10, 销售方名称=ENTITY["name"], 销售方纳税人识别号=ENTITY["tax_id"])
    vlm = dict(VLM_B10, seller_name=ENTITY["name"], seller_tax_id=ENTITY["tax_id"])
    res = fuse(ocr=ocr, vlm=vlm, entities=BUYER_ENTITIES)
    assert res.verdict == "rejected" and res.nulled

    rows = rows_of(res)
    checks, verdict = revalidate(rows, res.nulled, BUYER_ENTITIES)
    assert verdict == "rejected"
    assert status_of(checks, "seller_is_entity") == "fail"

    # 只确认别的字段，没填回销售方：仍然 rejected
    by_name(rows)["item_name"].resolved = 1
    _, verdict = revalidate(rows, res.nulled, BUYER_ENTITIES)
    assert verdict == "rejected"

    # 人把销售方两个字段都填成真实的卖方之后，拒绝才解除
    rows_by = by_name(rows)
    rows_by["seller_name"].final_value = "广州晶东贸易有限公司"
    rows_by["seller_tax_id"].final_value = "91440101664041243T"
    for k in ("seller_name", "seller_tax_id"):
        rows_by[k].source, rows_by[k].resolved = "manual", 1
    checks, verdict = revalidate(rows, res.nulled, BUYER_ENTITIES)
    assert status_of(checks, "seller_is_entity") == "pass"
    assert verdict != "rejected"


# --------------------------------------------------------------------------
# 2. 人工裁决消除冲突信号，但不改写机器当时的置信
# --------------------------------------------------------------------------
def test_confirming_conflict_clears_signal_but_keeps_machine_confidence():
    """确认销售方后「多路不一致」不再失败；全部核完后判定为 ok。

    confidence 记录的是机器当时的判断，页面靠它区分「自动核验」和「你已确认」，
    所以人工操作只动 resolved，不动 confidence。
    """
    rows = b10_rows()
    rows_by = by_name(rows)
    assert {r.field_name for r in rows if not r.resolved} == {"seller_name", "item_name"}

    rows_by["seller_name"].resolved = 1
    checks, verdict = revalidate(rows, [], BUYER_ENTITIES)
    assert status_of(checks, "cross_source_mismatch") == "pass"
    assert verdict == "warn"  # 项目名称还没人看

    rows_by["item_name"].resolved = 1
    _, verdict = revalidate(rows, [], BUYER_ENTITIES)
    assert verdict == "ok"
    assert rows_by["seller_name"].confidence == "low"


# --------------------------------------------------------------------------
# 3. 人明确填写的值不会被系统置空
# --------------------------------------------------------------------------
def test_human_seller_equal_to_entity_is_rejected_but_not_nulled():
    """人把销售方填成了本公司：判定 rejected（确认会被拦下），但值原样保留。"""
    rows = b10_rows()
    seller = by_name(rows)["seller_name"]
    seller.final_value, seller.source, seller.resolved = ENTITY["name"], "manual", 1

    checks, verdict = revalidate(rows, [], BUYER_ENTITIES)
    assert verdict == "rejected"
    assert status_of(checks, "seller_is_entity") == "fail"
    assert seller.final_value == ENTITY["name"]


# --------------------------------------------------------------------------
# 4. 金额组三层判定：尽量免人工，但不放过对不上的票
# --------------------------------------------------------------------------
def test_layer1_both_equations_hold_passes_without_human():
    """第一层：四项齐全且两式成立，整组放行，值取票面读数（不再用公式覆盖）。"""
    rows_by = by_name(b10_rows())
    for k in ("amount", "tax_rate", "tax_amount", "total_with_tax"):
        assert rows_by[k].resolved == 1 and rows_by[k].confidence == "high"
        assert rows_by[k].source == "vlm"
    assert rows_by["tax_amount"].final_value == "507.82"


def test_b1_discount_invoice_passes_with_its_own_numbers():
    """b1 是另一张带折扣行的票（1211.11 + 205.89 = 1417.00，1211.11 × 17% = 205.89），
    读数自洽就放行——评测真值把它当成了另一张票，不代表读错。"""
    res = reconcile_amounts(group("1211.11", "17%", "205.89", "1417.00"))
    assert res.status == "pass" and res.rule == "both"


def test_ocr_disagreement_does_not_block_consistent_primary_readings():
    """主读数四项自洽时，OCR 读出的不同数不影响放行（b31：OCR 把税额读成 507.80，
    还把它读进了金额栏）。"""
    rows_by = by_name(rows_of(fuse(
        ocr=dict(OCR_B10, 金额="507.80", 税额="507.80"), vlm=VLM_B10, entities=BUYER_ENTITIES)))
    assert rows_by["amount"].resolved == 1 and rows_by["amount"].final_value == "2987.18"
    assert rows_by["tax_amount"].final_value == "507.82"


def test_scenario_a_rate_is_derived_from_amounts():
    """场景 A：三金额自洽、税率缺失或读错时，反推 T ÷ A 对上标准税率就纠正并放行。"""
    res = reconcile_amounts(group("2987.18", None, "507.82", "3495.00"))
    assert res.status == "pass" and res.derived == "tax_rate" and res.values["tax_rate"] == "17%"
    res = reconcile_amounts(group("2987.18", "13%", "507.82", "3495.00"))
    assert res.values["tax_rate"] == "17%"

    rows_by = by_name(b10_rows(tax_rate="13%"))
    assert rows_by["tax_rate"].final_value == "17%" and rows_by["tax_rate"].source == "computed"


def test_scenario_a_does_not_guess_zero_rate():
    """税额为 0 时不自动填税率：免税、零税率、不征税写法不同，交给人。"""
    res = reconcile_amounts(group("100.00", None, "0.00", "100.00"))
    assert res.status == "fail" and res.rule == "rate_unknown"


def test_scenario_b_missing_amount_or_tax_is_filled_and_verified():
    """场景 B：只缺金额或税额时由加法补全，再用乘法校验，通过就放行；缺价税合计同理。"""
    res = reconcile_amounts(group(None, "17%", "507.82", "3495.00"))
    assert res.status == "pass" and res.derived == "amount" and res.values["amount"] == "2987.18"
    res = reconcile_amounts(group("2987.18", "17%", None, "3495.00"))
    assert res.derived == "tax_amount" and res.values["tax_amount"] == "507.82"
    res = reconcile_amounts(group("2987.18", "17%", "507.82", None))
    assert res.derived == "total_with_tax" and res.values["total_with_tax"] == "3495.00"
    # 补出来的值过不了乘法校验，就不放行
    assert reconcile_amounts(group(None, "13%", "507.82", "3495.00")).status == "fail"


def test_scenario_c_rounding_passes_only_within_limit():
    """场景 C：加法成立时，乘法差额在多行凑整范围（≤ 0.05 元）内以加法为准放行，超出就交人工。"""
    assert reconcile_amounts(group("1000.00", "13%", "130.04", "1130.04")).rule == "rounding"
    assert reconcile_amounts(group("1000.00", "13%", "130.06", "1130.06")).status == "fail"


def test_b11_addition_holds_but_numbers_wrong_goes_to_human():
    """b11：2587.18 + 400.00 = 2987.18 加法恰好成立，但乘法差 39.82 元、反推税率不在库里，
    必须交人工——「只看加法」会把这张数字全错的票静默放行。"""
    res = reconcile_amounts(group("2587.18", "17%", "400.00", "2987.18"))
    assert res.status == "fail" and res.rule == "rate_mismatch"
    assert set(res.implicated) == {"amount", "tax_rate", "tax_amount", "total_with_tax"}


def test_b18_shifted_columns_go_to_human():
    """b18：税额、价税合计读串了栏，两式都不成立，交人工。"""
    res = reconcile_amounts(group("2987.18", "17%", "210.64", "507.82"))
    assert res.status == "fail" and res.rule == "conflict"


def test_two_or_more_missing_goes_to_human():
    """缺两项及以上推不出来，交人工；四项全空也一样（否则识别整体失败的票会被自动确认）。"""
    assert reconcile_amounts(group(None, "17%", None, "3495.00")).rule == "insufficient"
    res = rows_of(fuse(ocr={}, vlm={}, entities=BUYER_ENTITIES))
    amount_rows = [r for r in res if r.field_name in ("amount", "tax_amount", "total_with_tax")]
    assert all(r.resolved == 0 for r in amount_rows)


def test_qr_amount_conflict_goes_to_human_and_qr_value_is_kept():
    """二维码读出的金额与其余三项对不上：交人工，二维码的值不被推导改写。"""
    rows_by = by_name(rows_of(fuse(qr={"amount": "3000.00"}, ocr=OCR_B10, vlm=VLM_B10,
                                   entities=BUYER_ENTITIES)))
    assert rows_by["amount"].final_value == "3000.00" and rows_by["amount"].confidence == "certain"
    assert rows_by["tax_amount"].resolved == 0 and rows_by["total_with_tax"].resolved == 0


def test_item_name_always_needs_human_review():
    """项目名称无论怎样都要人工复核（产品要求）。"""
    assert by_name(b10_rows())["item_name"].resolved == 0


# --------------------------------------------------------------------------
# 5. 人工修改后整组重判：人给的值不被改写，其余能放行就放行
# --------------------------------------------------------------------------
def test_fixing_shifted_columns_lets_the_rest_pass_automatically():
    """b18：人把税额、价税合计改对之后，整组自洽，没人看过的金额、税率自动放行。"""
    rows_by = by_name(b10_rows(tax_amount="210.64", total_with_tax="507.82"))
    assert rows_by["amount"].resolved == 0  # 第三层：整组交人工

    rows_by["tax_amount"].final_value, rows_by["tax_amount"].source = "507.82", "manual"
    rows_by["tax_amount"].resolved = 1
    reconcile_after_edit(rows_by, "tax_amount")
    assert rows_by["amount"].resolved == 0  # 价税合计还对不上

    rows_by["total_with_tax"].final_value, rows_by["total_with_tax"].source = "3495.00", "manual"
    rows_by["total_with_tax"].resolved = 1
    reconcile_after_edit(rows_by, "total_with_tax")
    assert rows_by["amount"].resolved == 1 and rows_by["amount"].confidence == "high"
    assert rows_by["tax_rate"].resolved == 1
    assert rows_by["tax_amount"].final_value == "507.82"  # 人给的值原样保留


def test_editing_an_auto_passed_amount_reopens_the_rest():
    """整组已自动放行后，人改了金额：其余三项和新金额矛盾，退回待复核，值不被改写。"""
    rows_by = by_name(b10_rows())
    rows_by["amount"].final_value, rows_by["amount"].source = "3000.00", "manual"
    reconcile_after_edit(rows_by, "amount")
    for k in ("tax_rate", "tax_amount", "total_with_tax"):
        assert rows_by[k].resolved == 0
    assert rows_by["tax_amount"].final_value == "507.82"


def test_filling_a_missing_amount_derives_the_other_missing_one():
    """缺金额和税额（交人工）；人填了金额后，税额由加法补全、乘法校验通过，整组放行。"""
    rows_by = by_name(b10_rows(amount=None, tax_amount=None))
    assert rows_by["amount"].resolved == 0 and rows_by["tax_amount"].resolved == 0

    rows_by["amount"].final_value, rows_by["amount"].source = "2987.18", "manual"
    rows_by["amount"].resolved = 1
    touched = reconcile_after_edit(rows_by, "amount")
    assert touched == ["tax_amount"]
    assert rows_by["tax_amount"].final_value == "507.82" and rows_by["tax_amount"].source == "computed"
    assert rows_by["total_with_tax"].resolved == 1


def test_restore_auto_derives_from_the_other_three():
    """「恢复自动推算」把人填的一项交还给公式，按其余三项推导；推不出来时明确报错。"""
    rows_by = by_name(b10_rows())
    tax = rows_by["tax_amount"]
    tax.final_value, tax.source, tax.resolved = "500.00", "manual", 1

    touched = restore_auto(rows_by, "tax_amount")
    assert touched == ["tax_amount"]
    assert tax.final_value == "507.82" and tax.source == "computed"

    rows_by["total_with_tax"].final_value = None
    with pytest.raises(InvalidValue):
        restore_auto(rows_by, "tax_amount")


# --------------------------------------------------------------------------
# 6. 来源归属
# --------------------------------------------------------------------------
def test_set_value_attributed_to_matching_source():
    """选中某一路的识别值记为那一路；与当前值相同按确认处理；新值记为 manual。"""
    seller = by_name(b10_rows())["seller_name"]
    assert resolve_set(seller, "广州品乐贸易有限公司") == ("广州品乐贸易有限公司", "vlm", False)
    assert resolve_set(seller, " 广州晶东贸易有限公司 ") == ("广州晶东贸易有限公司", "ocr", True)
    assert resolve_set(seller, "广州某某贸易有限公司") == ("广州某某贸易有限公司", "manual", False)


def test_set_on_amount_group_or_lookup_rows_is_manual():
    """金额组与查表值被人改了就是人的判断：即使等于某路识别值也记为 manual，
    否则下次金额组重判时会被当成没人看过的机器值改写。"""
    rows_by = by_name(b10_rows())
    tax = rows_by["tax_amount"]
    tax.final_value, tax.llm_value = "439.82", "400.00"  # 发票 4 的形态
    assert resolve_set(tax, "400") == ("400.00", "manual", False)
    assert resolve_set(tax, "439.82")[2] is True

    buyer = rows_by["buyer_name"]
    assert resolve_set(buyer, "深圳市购机汇科技有限公司")[1] == "manual"


# --------------------------------------------------------------------------
# 7. 输入校验在写入之前完成
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field,raw,expected", [
    ("issue_date", "2016/6/12", "2016-06-12"),
    ("issue_date", "2016年06月12日", "2016-06-12"),
    ("amount", "1,234.5", "1234.50"),
    ("amount", "-100", "-100.00"),
    ("tax_rate", "1", "1%"),
    ("tax_rate", "13 %", "13%"),
    ("tax_rate", "免税", "免税"),
    ("invoice_code", "4400 1541 30", "4400154130"),
    ("seller_name", "  广州 晶东 贸易有限公司 ", "广州 晶东 贸易有限公司"),
    ("invoice_code", "   ", None),
])
def test_normalize_input_accepts(field, raw, expected):
    assert normalize_input(field, raw) == expected


@pytest.mark.parametrize("field,raw", [
    ("issue_date", "2016-13-45"),
    ("issue_date", "昨天"),
    ("amount", "abc"),
    ("amount", "12345678901.00"),   # 超过 Numeric(12,2) 的 10 位整数
    ("amount", "1.234"),
    ("tax_rate", "abc"),
    ("tax_rate", "0.13"),           # 有歧义：13% 还是 0.13%，不猜
    ("invoice_code", "4" * 21),     # 超过列宽 20
    ("seller_tax_id", "9144*0101"),
])
def test_normalize_input_rejects(field, raw):
    with pytest.raises(InvalidValue):
        normalize_input(field, raw)


def test_invalid_input_leaves_row_untouched():
    """校验失败时行上的任何属性都不变，接口直接返回 400。"""
    rows_by = by_name(b10_rows())
    for field, raw in (("amount", "abc"), ("seller_name", "   ")):
        row = rows_by[field]
        snapshot = dict(vars(row))
        with pytest.raises(InvalidValue):
            resolve_set(row, raw)
        assert vars(row) == snapshot


def test_optional_field_may_be_cleared():
    """非必填字段（数电票没有发票代码）允许清空；必填字段不允许。"""
    code = by_name(b10_rows())["invoice_code"]
    assert resolve_set(code, "") == (None, "manual", False)


def test_apply_to_invoice_converts_or_leaves_column_untouched():
    invoice = SimpleNamespace(issue_date="orig", amount="orig")
    apply_to_invoice(invoice, "amount", "3000.00")
    assert str(invoice.amount) == "3000.00"
    with pytest.raises(InvalidValue):
        apply_to_invoice(invoice, "issue_date", "2016-02-30")
    assert invoice.issue_date == "orig"


# --------------------------------------------------------------------------
# 8. 规则结果能落到具体字段
# --------------------------------------------------------------------------
def test_tax_id_rule_names_only_the_failing_field():
    """税号规则失败时只点名出错的那个税号，页面据此把提示贴到对应字段下。"""
    check = check_tax_id_format({
        "buyer_tax_id": FieldResult(value="440300083885931"),
        "seller_tax_id": FieldResult(value="9144010166404124"),  # 16 位
    })
    assert check.status == "fail"
    assert check.fields == ["seller_tax_id"]
