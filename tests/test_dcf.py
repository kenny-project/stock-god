#!/usr/bin/env python3
"""
dcf.py 数据读取与 TTM 基期测试

覆盖（2026-09 修复）:
  1. 20-F 被当作 10-Q 过滤掉、¥ 金额正则不识别 → PDD 无法估值
  2. 基期只用最新年报，滞后最多 3 个季度 → 用季度数据构造 TTM 基期
     - 利润表科目（10-Q/6-K 均为单季值）: TTM = 年报 + Σ年后单季 − Σ上年同期
     - 现金流科目在 10-Q 中为 YTD 累计: TTM = 年报 + 最新YTD − 上年同期YTD
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import dcf  # noqa: E402


def _annual_md(revenue, ni, cfo, capex, fcf, da, currency="$"):
    c = currency
    return f"""# TEST 10-K FY2025

## 财务指标

| 指标 | 数值 |
|:---|:---|
| 营收 | {c}{revenue}M |
| 净利润 | {c}{ni}M |

## 现金流（YTD 累计）

| 项目 | 数值 |
|:---|:---|
| 经营现金流 | {c}{cfo}M |
| 资本支出 | ({c}{capex}M) |
| 自由现金流 | {c}{fcf}M |
| 折旧摊销 | {c}{da}M |
"""


def _quarter_md(period, revenue, cfo, form="10-Q"):
    return f"""# TEST {form} {period}

## 财务指标

| 指标 | 数值 |
|:---|:---|
| 营收 | ${revenue}M |

## 现金流（YTD 累计）

| 项目 | 数值 |
|:---|:---|
| 经营现金流 | ${cfo}M |
"""


def _write_dir(files):
    tmp = tempfile.mkdtemp()
    for ticker, name, body in files:
        d = os.path.join(tmp, ticker)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)
    return tmp


class TestReadSecRecords(unittest.TestCase):
    def setUp(self):
        self._old_dir = dcf.ANALYSIS_DIR

    def tearDown(self):
        dcf.ANALYSIS_DIR = self._old_dir

    def test_shares_outstanding_parsed(self):
        """封面流通股数（百万股）随年报记录读出，供每股估值兜底"""
        dcf.ANALYSIS_DIR = _write_dir([
            ("MSFT", "10-K_FY2026.md",
             _annual_md(331839, 133749, 118548, 41000, 90000, 22000)
             + "\n| 流通股数 | 7,508.0M |\n"),
        ])
        annual = dcf.read_sec_analysis("US.MSFT")
        self.assertEqual(annual[0]["shares_outstanding"], 7508.0)

    def test_20f_is_annual_and_rmb_parsed(self):
        dcf.ANALYSIS_DIR = _write_dir([
            ("PDD", "20-F_FY2025.md",
             _annual_md(431846, 97843, 105794, 58406, 105794, 4700, currency="¥")),
        ])
        annual = dcf.read_sec_analysis("US.PDD")
        self.assertEqual(len(annual), 1)
        self.assertEqual(annual[0]["revenue"], 431846)
        self.assertEqual(annual[0]["capex"], -58406)
        self.assertEqual(annual[0]["currency"], "¥")

    def test_6k_is_quarterly_record(self):
        dcf.ANALYSIS_DIR = _write_dir([
            ("PDD", "6-K_2026Q2.md", _quarter_md("2026Q2", 112400, 25700, form="6-K")),
        ])
        self.assertEqual(dcf.read_sec_analysis("US.PDD"), [])  # 6-K 不算年报
        quarterly = dcf.read_sec_quarterly("US.PDD")
        self.assertEqual(len(quarterly), 1)
        self.assertEqual(quarterly[0]["form"], "6-K")
        self.assertEqual(quarterly[0]["revenue"], 112400)


class TestComputeTTM(unittest.TestCase):
    def test_income_metrics_sum_quarters(self):
        """单季值（利润表/6-K）：TTM = 年报 + Σ年后单季 − Σ上年同期单季"""
        annual = [{"period": "FY2025", "revenue": 331839, "net_income": 101832,
                   "form": "10-K", "currency": "$"}]
        quarterly = [
            {"period": "2026Q1", "revenue": 77673, "net_income": 24667, "form": "10-Q"},
            {"period": "2026Q2", "revenue": 84000, "net_income": 26000, "form": "10-Q"},
            {"period": "2025Q1", "revenue": 65585, "net_income": 22359, "form": "10-Q"},
            {"period": "2025Q2", "revenue": 76441, "net_income": 23434, "form": "10-Q"},
        ]
        ttm = dcf.compute_ttm(annual, quarterly)
        # 331839 + (77673+84000) - (65585+76441) = 351486
        self.assertEqual(ttm["revenue"], 351486)
        self.assertEqual(ttm["net_income"], 101832 + 24667 + 26000 - 22359 - 23434)
        self.assertIn("2026Q2", ttm["base_period"])

    def test_cfo_ytd_formula_for_10q(self):
        """10-Q 现金流量表为 YTD 累计：TTM = 年报 + 最新YTD − 上年同期YTD"""
        annual = [{"period": "FY2025", "cfo": 118548, "form": "10-K"}]
        quarterly = [
            {"period": "2026Q3", "cfo": 116996, "form": "10-Q"},  # 9个月 YTD
            {"period": "2025Q3", "cfo": 98315, "form": "10-Q"},   # 上年同期 9个月 YTD
            {"period": "2026Q1", "cfo": 45057, "form": "10-Q"},   # 干扰项（3个月 YTD）
        ]
        ttm = dcf.compute_ttm(annual, quarterly)
        self.assertEqual(ttm["cfo"], 118548 + 116996 - 98315)

    def test_cfo_per_quarter_for_6k(self):
        """6-K 现金流为单季值，与利润表同公式"""
        annual = [{"period": "FY2025", "cfo": 105794, "form": "20-F"}]
        quarterly = [
            {"period": "2026Q1", "cfo": 30000, "form": "6-K"},
            {"period": "2025Q1", "cfo": 25000, "form": "6-K"},
        ]
        ttm = dcf.compute_ttm(annual, quarterly)
        self.assertEqual(ttm["cfo"], 105794 + 30000 - 25000)

    def test_missing_prior_quarter_metric_skipped(self):
        """上年同期缺失该指标时，不伪造 TTM（财务数据不可造假）"""
        annual = [{"period": "FY2025", "revenue": 100, "depreciation": 10, "form": "10-K"}]
        quarterly = [
            {"period": "2026Q1", "revenue": 30, "depreciation": 3, "form": "10-Q"},
            {"period": "2025Q1", "revenue": 25, "form": "10-Q"},  # 无折旧
        ]
        ttm = dcf.compute_ttm(annual, quarterly)
        self.assertEqual(ttm["revenue"], 105)
        self.assertNotIn("depreciation", ttm)

    def test_no_quarters_after_annual(self):
        """年报后无新季报（如 MSFT FY2026 后暂无）→ 无 TTM，沿用年报基期"""
        annual = [{"period": "FY2026", "revenue": 331839, "form": "10-K"}]
        quarterly = [{"period": "2026Q3", "revenue": 80000, "form": "10-Q"}]  # 年报期内
        self.assertEqual(dcf.compute_ttm(annual, quarterly), {})

    def test_quarters_before_annual_ignored(self):
        """更早财年的季度记录不得混入 TTM"""
        annual = [{"period": "FY2025", "revenue": 100, "form": "10-K"}]
        quarterly = [
            {"period": "2026Q1", "revenue": 30, "form": "10-Q"},
            {"period": "2025Q1", "revenue": 25, "form": "10-Q"},
            {"period": "2024Q1", "revenue": 20, "form": "10-Q"},  # 更早，忽略
            {"period": "2024Q2", "revenue": 21, "form": "10-Q"},
        ]
        ttm = dcf.compute_ttm(annual, quarterly)
        self.assertEqual(ttm["revenue"], 105)


class TestSensitivityAnalysis(unittest.TestCase):
    """敏感性分析不得在无股数时输出 $0（财报口径为企业价值，除以 0 无意义）"""

    def test_no_shares_outputs_enterprise_value_not_zero(self):
        """shares_outstanding=0（无 Futu 行情）→ 输出企业内在价值（M），不得为 $0"""
        table = dcf.sensitivity_analysis(
            1000, 0, 0,
            [0.05], [0.10], 0.03, 5
        )
        cell = table[0]["r_10%"]
        self.assertNotIn("$0", cell)
        self.assertTrue(cell.endswith("M"), f"应为企业价值口径（M 结尾）: {cell}")
        # 手工计算企业内在价值以对账
        expected = dcf.dcf_valuation(1000, 0.05, 0.10, 0.03, 5, 0, 0)["intrinsic_value"]
        self.assertIn(f"{expected:,.0f}", cell)

    def test_with_shares_outputs_per_share(self):
        """shares_outstanding>0 → 维持每股口径"""
        table = dcf.sensitivity_analysis(
            1000, 100, 50,
            [0.05], [0.10], 0.03, 5
        )
        cell = table[0]["r_10%"]
        self.assertFalse(cell.endswith("M"))
        self.assertNotIn("$0", cell)

    def test_currency_prefix(self):
        """货币符号可配置（PDD 为 ¥）"""
        table = dcf.sensitivity_analysis(
            1000, 0, 0,
            [0.05], [0.10], 0.03, 5, currency="¥"
        )
        self.assertTrue(table[0]["r_10%"].startswith("¥"))


class TestGenerateReport(unittest.TestCase):
    def _base_args(self):
        dcf_result = dcf.dcf_valuation(1000, 0.05, 0.10, 0.03, 5, 0, 0)
        sensitivity = dcf.sensitivity_analysis(1000, 0, 0, [0.05], [0.10], 0.03, 5)
        return dcf_result, sensitivity

    def test_report_currency_threading(self):
        """货币符号贯穿报告：¥ 数据不得出现硬编码 $"""
        dcf_result, sensitivity = self._base_args()
        oe_data = [{"year": "FY2025", "net_income": 100, "revenue": 500,
                    "fcf": 90, "cfo": 110, "depreciation": 10, "capex": 20,
                    "maintenance_capex": 12, "owner_earnings": 98}]
        report = dcf.generate_report(
            "US.PDD", dcf_result, sensitivity, oe_data, None,
            {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.03,
             "years": 5, "currency": "¥", "base_period": "TTM 截至 2026Q2"},
        )
        self.assertIn("¥", report)
        self.assertIn("TTM 截至 2026Q2", report)
        self.assertNotIn("${", report)  # 不应残留硬编码美元模板

    def test_report_caption_dynamic(self):
        """无股数时敏感性分析标题为股权价值口径，有股数时为每股"""
        dcf_result, sensitivity = self._base_args()
        report = dcf.generate_report(
            "US.TEST", dcf_result, sensitivity, [], None,
            {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.03,
             "years": 5, "currency": "$"},
        )
        self.assertIn("股权内在价值", report)
        self.assertNotIn("每股内在价值：", report)


class TestOwnerEarningsFromRecord(unittest.TestCase):
    """capex 在提取文件中为负数（现金流出，如 "资本支出 | ($1,192M)"），
    不得因符号约定导致 OE 公式被跳过而静默回退 FCF（QCOM 全部年份曾中招）"""

    def test_negative_capex_uses_oe_formula(self):
        oe, mcapex = dcf.owner_earnings_for_record(5541, 1602, -1192, 12820)
        self.assertAlmostEqual(oe, 5541 + 1602 - 1192 * 0.6)
        self.assertAlmostEqual(mcapex, 1192 * 0.6)

    def test_missing_capex_falls_back_fcf(self):
        oe, mcapex = dcf.owner_earnings_for_record(1000, 100, 0, 900)
        self.assertEqual(oe, 900)
        self.assertEqual(mcapex, 0)

    def test_missing_depreciation_falls_back_fcf(self):
        oe, mcapex = dcf.owner_earnings_for_record(1000, 0, -500, 900)
        self.assertEqual(oe, 900)


class TestLatestCoverShares(unittest.TestCase):
    """封面股数兜底：最新季报封面 > 最新年报封面（此前 reversed 拼接顺序相反）"""

    def test_prefers_latest_quarterly_cover(self):
        quarterly = [{"period": "2025Q3", "shares_outstanding": 1000.0},
                     {"period": "2026Q3", "shares_outstanding": 1050.0}]
        annual = [{"period": "FY2025", "shares_outstanding": 1071.0}]
        shares, period = dcf.latest_cover_shares(quarterly, annual)
        self.assertEqual(shares, 1050.0)
        self.assertEqual(period, "2026Q3")

    def test_annual_when_no_quarterly(self):
        annual = [{"period": "FY2024", "shares_outstanding": 900.0},
                  {"period": "FY2025", "shares_outstanding": 1071.0}]
        shares, period = dcf.latest_cover_shares([], annual)
        self.assertEqual(shares, 1071.0)

    def test_none_when_missing(self):
        self.assertEqual(dcf.latest_cover_shares([], []), (None, None))


if __name__ == "__main__":
    unittest.main()
