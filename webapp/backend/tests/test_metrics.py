import os
from services.metrics import parse_analysis_metrics, parse_dcf_valuation

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_analysis_metrics():
    with open(os.path.join(FIX, "10-K_FY2024.md"), encoding="utf-8") as f:
        text = f.read()
    m = parse_analysis_metrics(text)
    assert m["净利润"] == 5700          # $5,700M → 百万单位数值
    assert m["营收"] == 51362
    assert m["净资产收益率"] == 39.5     # 百分数保留数值
    assert m["经营现金流"] == 7429
    assert "每股收益" in m


def test_parse_dcf_valuation():
    with open(os.path.join(FIX, "US.NKE_DCF.md"), encoding="utf-8") as f:
        text = f.read()
    v = parse_dcf_valuation(text)
    assert v["intrinsic_value_musd"] == 47099
    assert v["price"] == 36.80
    assert len(v["owner_earnings_by_year"]) == 5   # FY2022..FY2026
    assert v["owner_earnings_by_year"][0]["oe"] == 6308
