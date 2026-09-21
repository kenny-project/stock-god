"""services/ttm.py 单测：TTM 基期计算（成分齐全 / 季报缺失回退 / 空数据）。"""
from services.ttm import compute_ttm


def _annual(fy=2025, ni=5541, dep=1602, capex=-1192):
    return {"fiscal_year": fy,
            "metrics": {"净利润": ni, "折旧摊销": dep, "资本支出": capex}}


def _q(fy, q, ni, dep=None, capex=None, form="10-Q"):
    m = {"净利润": ni}
    if dep is not None:
        m["折旧摊销"] = dep
    if capex is not None:
        m["资本支出"] = capex
    return {"fiscal_year": fy, "quarter": f"{fy}Q{q}", "form_type": form, "metrics": m}


def test_full_components_ttm():
    """年报 + 年后三季齐全（10-Q YTD 口径）→ 三成分与 OE 全部算出。"""
    quarters = [
        _q(2025, 1, 3180, 436, -277), _q(2025, 2, 2812, 833, -491),
        _q(2025, 3, 2666, 1231, -785),  # 上年同期
        _q(2026, 1, 3004, 393, -549), _q(2026, 2, 7370, 806, -1082),
        _q(2026, 3, 2002, 1202, -1578),
    ]
    ttm = compute_ttm(_annual(), quarters)
    assert ttm["base_period"] == "TTM 截至 2026Q3"
    # 净利润单季滚动：5541 + (3004−3180)+(7370−2812)+(2002−2666) = 9259
    assert ttm["net_income"] == 9259
    # 折旧摊销 YTD 累计：1602 + 1202 − 1231 = 1573
    assert ttm["depreciation"] == 1573
    # CapEx YTD 累计后取绝对值：−1192 + (−1578) − (−785) = −1985 → 1985
    assert ttm["capex"] == 1985
    assert ttm["maintenance_capex"] == 1191.0
    assert ttm["owner_earnings"] == 9259 + 1573 - 1191.0
    # 推导串含年报值与滚动明细
    assert "年报 5,541" in ttm["components"]
    assert "2026Q3(2,002−2,666)" in ttm["components"]
    assert "× 0.6" in ttm["components"]


def test_no_after_quarters_fallback_to_annual():
    """无年后季报 → 回退最新年报基期，成分直接取年报值。"""
    ttm = compute_ttm(_annual(), [_q(2025, 1, 3000, 400, -300)])
    assert ttm["base_period"] == "最新年报 FY2025"
    assert ttm["net_income"] == 5541
    assert ttm["depreciation"] == 1602
    assert ttm["capex"] == 1192  # 负值取绝对值
    assert ttm["owner_earnings"] == round(5541 + 1602 - 1192 * 0.6, 1)
    assert "回退最新年报基期" in ttm["components"]


def test_missing_prior_quarter_component_null():
    """缺上年同期季报 → 净利润置空（不编造），现金流 YTD 有同期仍可算。"""
    quarters = [
        _q(2025, 2, 2812, 833, -491), _q(2025, 3, 2666, 1231, -785),
        _q(2026, 1, 3004, 393, -549),  # 上年缺 2025Q1
        _q(2026, 2, 7370, 806, -1082), _q(2026, 3, 2002, 1202, -1578),
    ]
    ttm = compute_ttm(_annual(), quarters)
    assert ttm["net_income"] is None
    assert "无法滚动" in ttm["components"]
    # 现金流 YTD 只需 2025Q3 同期 → 仍可算
    assert ttm["depreciation"] == 1602 + 1202 - 1231
    assert ttm["capex"] == 1985
    # 净利润缺失 → 基期 OE 置空
    assert ttm["owner_earnings"] is None
    assert "基期 OE 置空" in ttm["components"]


def test_missing_annual_metric_noted():
    """年报缺折旧科目 → 该成分置空并在 components 注明，OE 置空。"""
    annual = _annual()
    del annual["metrics"]["折旧摊销"]
    quarters = [_q(2025, 1, 3180, 436, -277), _q(2026, 1, 3004, 393, -549)]
    ttm = compute_ttm(annual, quarters)
    assert ttm["depreciation"] is None
    assert "年报缺该科目" in ttm["components"]
    assert ttm["owner_earnings"] is None
    # 其余成分不受影响
    assert ttm["net_income"] == 5541 + 3004 - 3180


def test_mixed_forms_cashflow_null():
    """年后季报 10-Q/6-K 混合 → 现金流科目口径不一致置空，净利润仍滚动。"""
    quarters = [
        _q(2025, 1, 3180, 436, -277), _q(2025, 2, 2812, 833, -491),
        _q(2025, 3, 2666, 1231, -785),
        _q(2026, 1, 3004, 393, -549), _q(2026, 2, 7370, 806, -1082),
        _q(2026, 3, 2002, 1202, -1578, form="6-K"),
    ]
    ttm = compute_ttm(_annual(), quarters)
    assert ttm["net_income"] == 9259  # 利润表不受影响
    assert ttm["depreciation"] is None
    assert ttm["capex"] is None
    assert ttm["owner_earnings"] is None
    assert "口径不一致" in ttm["components"]


def test_all_6k_cashflow_rolling():
    """年后季报全 6-K（现金流单季披露）→ 现金流与利润表同滚动公式。"""
    quarters = [
        _q(2025, 1, 3180, 436, -277, form="6-K"),
        _q(2026, 1, 3004, 393, -549, form="6-K"),
    ]
    ttm = compute_ttm(_annual(), quarters)
    assert ttm["base_period"] == "TTM 截至 2026Q1"
    assert ttm["depreciation"] == 1602 + 393 - 436
    assert ttm["capex"] == abs(-1192 + -549 - -277)


def test_empty_data_returns_none():
    """空数据（无年报）→ None，不编造。"""
    assert compute_ttm(None, []) is None
    assert compute_ttm({"fiscal_year": 2025, "metrics": None}, []) is None
    assert compute_ttm({}, []) is None
