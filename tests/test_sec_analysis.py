#!/usr/bin/env python3
"""
sec_analysis 回归/修复验证测试

运行: python3 -m unittest discover -s tests -v
覆盖的 bug（2026-09 修复）:
  1. EPS 正则跨行匹配到 "Revenue increased $50.1 billion"
  2. COGS 匹配到分部数据而非利润表 Total cost of revenue
  3. D&A XBRL 自定义标签 (msft:DepreciationAmortizationAndOther) 未覆盖
  4. D&A 文本回退缺少 "Depreciation, amortization, and other" 模式
  5. HTMLTextExtractor 把相邻 span 拆开的单词用空格连接 (ITEM 1. B USINESS)
  6. --form 过滤依赖文件名子串 (msft-20260630.htm 不含 "10k")
  7. "最新" 文件按字母序选取 (msft-10k_20220630.htm 排最后)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from sec_analysis.base import HTMLTextExtractor, FilingAnalyzer  # noqa: E402
from sec_analysis.extraction import (  # noqa: E402
    extract_key_metrics, extract_cash_flows, validate_metrics,
)


def _ix(name, ctx, value, scale=None, unit="U_USD"):
    s = f' scale="{scale}"' if scale is not None else ""
    return f'<ix:nonFraction contextRef="{ctx}" unitRef="{unit}"{s} name="{name}">{value}</ix:nonFraction>'


class TestXBRLMetrics(unittest.TestCase):
    """XBRL 优先提取：利润表/资产负债表科目带会计期上下文，避免文本启发式抓错期间"""

    def test_revenue_picks_latest_year_and_consolidated(self):
        """PDD VIE 表列序：母公司/子公司/VIE/抵消/合并 — 应取最新年合并列（最大值）"""
        html = (
            _ix("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "D2023_a", "892,863", 3)
            + _ix("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "D2023_b", "194,028,064", 3)
            + _ix("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "D2023_c", "247,639,205", 3)
            + _ix("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "D2025_a", "627,344", 3)
            + _ix("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "D2025_b", "411,097,558", 3)
            + _ix("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "D2025_c", "431,845,713", 3, "Unit_Standard_CNY")
        )
        m = extract_key_metrics("Total revenues 247,639,205", html)
        self.assertEqual(m["revenue_num"], 431846)
        self.assertEqual(m["revenue"], "¥431,846M")

    def test_net_income_uses_xbrl(self):
        html = (
            _ix("us-gaap:NetIncomeLoss", "D2023", "60,026,544", 3)
            + _ix("us-gaap:NetIncomeLoss", "D2025", "122,843,120", 3, "Unit_Standard_CNY")
        )
        m = extract_key_metrics("Net income 60,026,544", html)
        self.assertEqual(m["net_income_num"], 122843)

    def test_eps_from_xbrl(self):
        html = (
            _ix("us-gaap:EarningsPerShareBasic", "D2025", "14.51")
            + _ix("us-gaap:EarningsPerShareDiluted", "D2025", "14.42")
        )
        m = extract_key_metrics("", html)
        self.assertEqual(m["eps"], "$14.42")

    def test_falls_back_to_text_without_html(self):
        m = extract_key_metrics("Total revenues 331,839 Net income 133,749")
        self.assertEqual(m["revenue_num"], 331839)
        self.assertEqual(m["net_income_num"], 133749)

    def test_assets_from_xbrl(self):
        html = (
            _ix("us-gaap:Assets", "I2023", "500,000,000", 3)
            + _ix("us-gaap:Assets", "I2025", "760,000,000", 3)
        )
        m = extract_key_metrics("", html)
        self.assertEqual(m["total_assets_num"], 760000)

    def test_xbrl_year_from_context_period_not_guid(self):
        """MSFT 用 GUID contextRef — 会计期必须取 <xbrli:context> 的 period 定义，
        而非 GUID 中恰好出现的 20xx 数字：正确科目的 GUID 无年份数字（得 ""），
        而脚注科目的 GUID 含 "2073" 反而成为"最大年份"被选中"""
        html = (
            '<xbrli:context id="C_real_fy2026"><xbrli:period>'
            '<xbrli:startDate>2025-07-01</xbrli:startDate>'
            '<xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>'
            '<xbrli:context id="C_footnote-3ab952207329"><xbrli:period>'
            '<xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>'
            + _ix("us-gaap:Assets", "C_real_fy2026", "758,376,000", 3)
            + _ix("us-gaap:Assets", "C_footnote-3ab952207329", "72,000,000", 3)
        )
        m = extract_key_metrics("", html)
        self.assertEqual(m["total_assets_num"], 758376)
from sec_analysis.main import sort_filing_files, filter_files_by_form  # noqa: E402
from sec_analysis.sec_10k import Sec10KAnalyzer  # noqa: E402
from sec_analysis.report import detect_filing_type, extract_fiscal_period  # noqa: E402
from sec_analysis.main import get_analyzer  # noqa: E402


class TestEPSExtraction(unittest.TestCase):
    def test_diluted_eps_without_dollar(self):
        """MSFT FY2026 实际格式：Diluted earnings per share 17.95 13.64 32%（无 $ 前缀）"""
        text = (
            "Diluted earnings per share 17.95 13.64 32% Adjusted net income (non-GAAP) 128,786 "
            "105,452 22% Revenue increased $50.1 billion or 18% driven by growth in Microsoft Cloud."
        )
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["eps"], "$17.95")

    def test_prefers_diluted_when_both_present(self):
        text = "Basic earnings per share $2.81 Diluted earnings per share $2.75"
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["eps"], "$2.75")

    def test_basic_only(self):
        text = "Basic earnings per share $2.81"
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["eps"], "$2.81")

    def test_eps_label_colon_style(self):
        """AAPL 风格：Earnings per share: Basic 6.16 Diluted 6.08"""
        text = "Earnings per share: Basic 6.16 5.13 Diluted 6.08 5.08"
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["eps"], "$6.08")

    def test_eps_not_confused_by_prose(self):
        """正文中 "per share increased 32%" 不应抓到整数百分比"""
        text = "our diluted earnings per share increased 32% year over year"
        metrics = extract_key_metrics(text)
        self.assertNotIn("eps", metrics)


class TestCostOfRevenue(unittest.TestCase):
    def test_prefers_total_cost_of_revenue(self):
        """分部表的 Cost of revenue 在前，利润表 Total cost of revenue 在后"""
        text = (
            "Productivity Revenue $139,996 Cost of revenue 25,017 22,422 "
            "Intelligent Cloud Revenue $137,791 Cost of revenue 57,876 "
            "Total Revenue $331,839 Cost of revenue 106,374 87,831 "
            "Total cost of revenue 106,374 87,831 74,109"
        )
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["cost_of_revenue_num"], 106374)

    def test_takes_max_without_total(self):
        text = "Cost of revenue 25,017 22,422 Cost of revenue 106,374 87,831"
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["cost_of_revenue_num"], 106374)

    def test_single_cost_of_sales(self):
        text = "Revenue 51,365 Cost of sales 28,925 Gross profit 22,440"
        metrics = extract_key_metrics(text)
        self.assertEqual(metrics["cost_of_revenue_num"], 28925)


class TestDepreciationAmortization(unittest.TestCase):
    def test_xbrl_custom_namespace_tag(self):
        """MSFT FY2026 用自定义标签 msft:DepreciationAmortizationAndOther"""
        html = (
            '<ix:nonFraction name="msft:DepreciationAmortizationAndOther" '
            'contextRef="C1" scale="6" sign="+">38,534</ix:nonFraction>'
        )
        cf = extract_cash_flows(html)
        self.assertEqual(cf["depreciation_amortization_num"], 38534)

    def test_xbrl_us_gaap_tag(self):
        html = '<ix:nonFraction name="us-gaap:DepreciationDepletionAndAmortization">12,675</ix:nonFraction>'
        cf = extract_cash_flows(html)
        self.assertEqual(cf["depreciation_amortization_num"], 12675)

    def test_xbrl_ignores_accumulated(self):
        """AccumulatedDepreciation 是资产负债表科目，不是当期折旧"""
        html = '<ix:nonFraction name="us-gaap:AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment">74,109</ix:nonFraction>'
        cf = extract_cash_flows(html)
        self.assertNotIn("depreciation_amortization", cf)

    def test_text_fallback_and_other(self):
        """文本格式：Depreciation, amortization, and other 14,460 12,675"""
        text = "Depreciation, amortization, and other 14,460 12,675 11,616 Stock-based compensation"
        cf = extract_cash_flows(text)
        self.assertEqual(cf["depreciation_amortization_num"], 14460)


class TestHTMLTextExtractor(unittest.TestCase):
    def _text(self, html):
        ext = HTMLTextExtractor()
        ext.feed(html)
        return ext.get_text()

    def test_joins_word_split_across_inline_spans(self):
        """MSFT FY2026 标题: <span>ITEM 1. B</span><span>USINESS</span>"""
        html = '<p><span>ITEM 1. B</span><span>USINESS</span></p>'
        self.assertIn("ITEM 1. BUSINESS", self._text(html))

    def test_space_preserved_between_blocks(self):
        html = "<table><tr><td>Total revenue</td><td>331,839</td></tr></table>"
        text = self._text(html)
        self.assertIn("Total revenue 331,839", text)

    def test_inline_spans_without_source_space_merge(self):
        """HTML 源码中相邻 span 无空格 → 视觉上也无空格（同一单词），应合并"""
        html = "<p><span>Total</span><span>revenue</span></p>"
        self.assertIn("Totalrevenue", self._text(html))


class TestFilingFileSelection(unittest.TestCase):
    def _write(self, path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_sort_by_date_desc(self):
        files = [
            "/r/msft-20230630.htm",
            "/r/msft-10k_20220630.htm",
            "/r/msft-20260630.htm",
            "/r/msft-20250630.htm",
        ]
        self.assertEqual(
            sort_filing_files(files),
            [
                "/r/msft-20260630.htm",
                "/r/msft-20250630.htm",
                "/r/msft-20230630.htm",
                "/r/msft-10k_20220630.htm",
            ],
        )

    def test_filter_by_form_uses_content(self):
        """文件名无表单信息时，应从 XBRL 内容判断类型"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f10k = os.path.join(d, "msft-20260630.htm")
            f10q = os.path.join(d, "aapl-20260331.htm")
            self._write(f10k, "<dei:DocumentFiscalPeriodFocus>FY</dei:DocumentFiscalPeriodFocus>")
            self._write(f10q, "<dei:DocumentFiscalPeriodFocus>Q2</dei:DocumentFiscalPeriodFocus>")
            result = filter_files_by_form([f10k, f10q], "10-K")
            self.assertEqual(result, [f10k])

    def test_filter_by_form_from_filename(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f10k = os.path.join(d, "msft-10k_20220630.htm")
            self._write(f10k, "anything")
            result = filter_files_by_form([f10k], "10-K")
            self.assertEqual(result, [f10k])


class _TestAnalyzer(FilingAnalyzer):
    @property
    def FILING_TYPE(self):
        return "10-K"

    @property
    def SECTION_PATTERNS(self):
        return {"business": [r"item\s*1[.\s:—–-]+\s*business"]}

    @property
    def EXPECTED_SECTIONS(self):
        return ["business"]


class TestSectionExtraction(unittest.TestCase):
    def test_body_heading_found_not_toc(self):
        """TOC 干净匹配在前，正文标题被 span 拆开 — 应定位到正文而非 TOC"""
        html = (
            "<p>Table of Contents</p>"
            "<p>Item 1. Business 3</p>"
            "<p>Item 1A. Risk Factors 14</p>"
            "<p><span>ITEM 1. B</span><span>USINESS</span></p>"
            "<p>GENERAL Microsoft is a technology company. " + "Detail " * 50 + "</p>"
            "<p>ITEM 1A. RISK FACTORS Some risk.</p>"
        )
        analyzer = _TestAnalyzer()
        result = analyzer.analyze(html)
        content = result["sections"]["business"]["content"]
        self.assertIn("GENERAL", content)
        self.assertIn("technology company", content)
        self.assertNotIn("Risk Factors 14", content)


class TestForeignIssuer20F(unittest.TestCase):
    """特殊公司分支：外国私人发行人（如 PDD/BABA）申报 20-F 而非 10-K"""

    def test_detect_20f_from_form_declaration(self):
        text = "UNITED STATES SECURITIES AND EXCHANGE COMMISSION FORM 20-F ANNUAL REPORT ..."
        self.assertEqual(detect_filing_type("pdd-20241231.htm", text), "20-F")

    def test_detect_20f_from_xbrl(self):
        text = '<dei:DocumentType>20-F</dei:DocumentType>'
        self.assertEqual(detect_filing_type("pdd-20241231.htm", text), "20-F")

    def test_get_analyzer_20f_uses_annual_semantics(self):
        analyzer = get_analyzer("20-F")
        self.assertIsInstance(analyzer, Sec10KAnalyzer)

    def test_filter_files_by_form_20f(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f20f = os.path.join(d, "pdd-20241231.htm")
            with open(f20f, "w", encoding="utf-8") as f:
                f.write("FORM 20-F ANNUAL REPORT")
            self.assertEqual(filter_files_by_form([f20f], "20-F"), [f20f])

    def test_fiscal_period_from_20f_filename(self):
        """PDD 文件名形如 pdd-20251231x20f.htm，日期后还有后缀"""
        self.assertEqual(extract_fiscal_period("pdd-20251231x20f.htm", "20-F"), "FY2025")


class TestUnitNormalization(unittest.TestCase):
    """外国发行人单位/币种归一化：(RMB in thousands) 与 XBRL scale 属性"""

    def test_rmb_thousands_text_scaled_to_millions(self):
        text = (
            "(RMB in thousands, except share and per share data) "
            "Total revenues 393,838,241 Net income 60,026,544 Total assets 500,000,000 "
            "Total liabilities 200,000,000"
        )
        m = extract_key_metrics(text)
        self.assertEqual(m["revenue_num"], 393838)
        self.assertEqual(m["revenue"], "¥393,838M")
        self.assertEqual(m["net_income_num"], 60027)
        self.assertEqual(m["total_assets_num"], 500000)
        self.assertEqual(m["stockholders_equity_num"], 300000)

    def test_us_millions_unchanged(self):
        text = "(in millions, except per share data) Total revenues 331,839 Net income 133,749"
        m = extract_key_metrics(text)
        self.assertEqual(m["revenue_num"], 331839)
        self.assertEqual(m["revenue"], "$331,839M")

    def test_rmb_eps_symbol(self):
        text = "(RMB in thousands, except share and per share data) Diluted earnings per share 10.29"
        m = extract_key_metrics(text)
        self.assertEqual(m["eps"], "¥10.29")

    def test_xbrl_scale_thousands(self):
        html = '<ix:nonFraction name="msft:DepreciationAmortizationAndOther" scale="3">57,768,053</ix:nonFraction>'
        cf = extract_cash_flows(html)
        self.assertEqual(cf["depreciation_amortization_num"], 57768)

    def test_xbrl_scale_millions(self):
        html = '<ix:nonFraction name="msft:DepreciationAmortizationAndOther" scale="6" unitRef="U_USD">38,534</ix:nonFraction>'
        cf = extract_cash_flows(html)
        self.assertEqual(cf["depreciation_amortization_num"], 38534)

    def test_xbrl_not_double_scaled_with_rmb_text(self):
        """XBRL 已按 scale 换算，文本归一化不得再乘一次"""
        html = (
            "(RMB in thousands, except share and per share data) "
            '<ix:nonFraction contextRef="C1" unitRef="Unit_Standard_CNY" '
            'name="us-gaap:NetCashProvidedByUsedInOperatingActivities" scale="3">94,162,531</ix:nonFraction>'
        )
        cf = extract_cash_flows(html)
        self.assertEqual(cf["operating_num"], 94163)
        self.assertEqual(cf["operating"], "¥94,163M")

    def test_xbrl_picks_latest_period_regardless_of_column_order(self):
        """MSFT 最新年在前、PDD 最新年在后 — 都应取最新会计期"""
        html = (
            '<ix:nonFraction contextRef="Duration_2023" unitRef="U_USD" '
            'name="us-gaap:NetCashProvidedByUsedInOperatingActivities" scale="6">94,162</ix:nonFraction>'
            '<ix:nonFraction contextRef="Duration_2025" unitRef="U_USD" '
            'name="us-gaap:NetCashProvidedByUsedInOperatingActivities" scale="6">118,000</ix:nonFraction>'
        )
        cf = extract_cash_flows(html)
        self.assertEqual(cf["operating_num"], 118000)

    def test_fcf_not_rescaled_and_currency_follows_operating(self):
        """FCF 由 XBRL 输入派生时不得再按文本单位声明缩放（105794×0.001=106 的双重缩放 bug），
        且币种应随经营现金流（¥），而非硬编码 $"""
        html = (
            "(RMB in thousands, except share and per share data) "
            '<ix:nonFraction contextRef="D2025" unitRef="Unit_Standard_CNY" '
            'name="us-gaap:NetCashProvidedByUsedInOperatingActivities" scale="3">106,939,251</ix:nonFraction>'
            '<ix:nonFraction contextRef="D2025" unitRef="Unit_Standard_CNY" '
            'name="pdd:PaymentsToAcquirePropertyEquipmentAndSoftwareAndIntangibleAssets" '
            'scale="3">1,145,000</ix:nonFraction>'
        )
        cf = extract_cash_flows(html)
        self.assertEqual(cf["operating_num"], 106939)
        self.assertEqual(cf["capex_num"], -1145)
        self.assertEqual(cf["free_cash_flow_num"], 105794)
        self.assertEqual(cf["free_cash_flow"], "¥105,794M")

    def test_xbrl_capex_custom_namespace_tag(self):
        """PDD 用自定义标签 pdd:PaymentsToAcquirePropertyEquipmentAndSoftwareAndIntangibleAssets，
        capex 匹配应面向任意命名空间、按 Property/Equipment/Productive 语义筛选"""
        html = (
            '<ix:nonFraction contextRef="D2025" unitRef="Unit_Standard_CNY" '
            'name="pdd:PaymentsToAcquirePropertyEquipmentAndSoftwareAndIntangibleAssets" '
            'scale="3">583,879</ix:nonFraction>'
        )
        cf = extract_cash_flows(html)
        self.assertEqual(cf["capex_num"], -584)
        self.assertEqual(cf["capex"], "(¥584M)")


class TestMSFTFY2026Integration(unittest.TestCase):
    """真实文件集成测试（需已下载 MSFT FY2026 10-K）"""

    MSFT_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports", "sec_filings", "MSFT", "msft-20260630.htm",
    )

    def setUp(self):
        if not os.path.exists(self.MSFT_FILE):
            self.skipTest("MSFT FY2026 10-K 未下载")

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(cls.MSFT_FILE):
            return
        with open(cls.MSFT_FILE, encoding="utf-8", errors="ignore") as f:
            html = f.read()
        cls.html = html
        analyzer = Sec10KAnalyzer()
        result = analyzer.analyze(html)
        cls.sections = result["sections"]
        cls.metrics = extract_key_metrics(result["text"])
        cls.cash_flows = extract_cash_flows(html)

    def test_eps(self):
        self.assertEqual(self.metrics["eps"], "$17.95")

    def test_cogs(self):
        self.assertEqual(self.metrics["cost_of_revenue_num"], 106374)
        self.assertEqual(self.metrics["gross_margin"], "67.9%")

    def test_depreciation_amortization(self):
        self.assertEqual(self.cash_flows["depreciation_amortization_num"], 38534)

    def test_business_section_is_body_not_toc(self):
        content = self.sections["business"]["content"]
        self.assertGreater(len(content), 2000)
        self.assertNotIn("Item 1A. Risk Factors 14", content)


class TestDataQualityValidation(unittest.TestCase):
    """数据质量校验：异常值必须置空并告警，不得保留可疑数值（财务数据不可造假）"""

    def test_absurd_gross_margin_blanked_with_warning(self):
        """毛利率 -34952.8%（营收/成本口径冲突）必须置空并产生告警"""
        metrics = {
            "revenue": "$72M", "revenue_num": 72,
            "cost_of_revenue": "$25,238M", "cost_of_revenue_num": 25238,
            "gross_margin": "-34952.8%",
        }
        warnings = validate_metrics(metrics, {})
        self.assertNotIn("gross_margin", metrics)
        self.assertTrue(any("毛利率" in w for w in warnings))

    def test_normal_ratios_untouched(self):
        metrics = {
            "revenue": "$331,839M", "revenue_num": 331839,
            "total_assets": "$758,376M", "total_assets_num": 758376,
            "gross_margin": "67.9%", "net_margin": "40.3%",
            "debt_to_asset_ratio": "41.7%", "current_ratio": "1.66",
        }
        warnings = validate_metrics(metrics, {})
        self.assertEqual(warnings, [])
        self.assertIn("gross_margin", metrics)

    def test_nonpositive_revenue_blanked(self):
        """营收非正数属于提取错误，置空并告警"""
        metrics = {"revenue": "$0M", "revenue_num": 0,
                   "total_assets": "$1M", "total_assets_num": 1}
        warnings = validate_metrics(metrics, {})
        self.assertNotIn("revenue", metrics)
        self.assertTrue(warnings)

    def test_abnormal_cash_flow_ratio_blanked(self):
        cf = {"fcf_margin": "99999.0%", "fcf_margin_num": 99999.0}
        warnings = validate_metrics({}, cf)
        self.assertNotIn("fcf_margin", cf)
        self.assertTrue(any("FCF利润率" in w for w in warnings))

    def test_failed_extraction_stays_missing(self):
        """获取失败的字段保持缺失（缺失告警机制已覆盖），不得补默认值"""
        m = extract_key_metrics("some text without any financial numbers")
        self.assertNotIn("revenue", m)
        self.assertNotIn("net_income", m)


class TestNoSimulatedValues(unittest.TestCase):
    """不得用近似/模拟值冒充真实数据"""

    def test_primary_fields_not_fabricated_from_revenue(self):
        """主营字段无真实分部数据来源时，不得用营收/成本近似填充"""
        m = extract_key_metrics("Total revenues 331,839 Cost of revenue 106,374")
        self.assertNotIn("primary_revenue", m)
        self.assertNotIn("primary_cost", m)


class TestFiscalPeriodDetection(unittest.TestCase):
    """财期标签：按公司财年截止月推算季度；无日期文件用正文回退"""

    def test_quarter_by_fy_end_month(self):
        """10-Q 季度按财年截止月推算，而非硬编码 6 月财年"""
        # AAPL 财年止于 9 月：2024-12-28 是 FY2025 Q1
        self.assertEqual(
            extract_fiscal_period("aapl-20241228.htm", "10-Q", fy_end_month=9),
            "2025Q1")
        # MSFT 财年止于 6 月：2022-09-30 是 FY2023 Q1（旧行为误标 2022Q1）
        self.assertEqual(
            extract_fiscal_period("msft-20220930.htm", "10-Q", fy_end_month=6),
            "2023Q1")
        self.assertEqual(
            extract_fiscal_period("msft-20211231.htm", "10-Q", fy_end_month=6),
            "2022Q2")
        # GOOGL 财年止于 12 月：2025-03-31 是 FY2025 Q1
        self.assertEqual(
            extract_fiscal_period("googl-20250331.htm", "10-Q", fy_end_month=12),
            "2025Q1")

    def test_quarter_by_fy_end_month_445(self):
        """PFE 等 4-4-5 财历：季末偏移 4/7/10 个月也按最近季度归类"""
        self.assertEqual(
            extract_fiscal_period("pfe-20250330.htm", "10-Q", fy_end_month=12),
            "2025Q1")
        self.assertEqual(
            extract_fiscal_period("pfe-20250629.htm", "10-Q", fy_end_month=12),
            "2025Q2")
        self.assertEqual(
            extract_fiscal_period("pfe-20250928.htm", "10-Q", fy_end_month=12),
            "2025Q3")

    def test_annual_period(self):
        self.assertEqual(
            extract_fiscal_period("msft-20260630.htm", "10-K", fy_end_month=6),
            "FY2026")
        self.assertEqual(
            extract_fiscal_period("pdd-20251231x20f.htm", "20-F", fy_end_month=12),
            "FY2025")

    def test_6k_quarter_from_text_title(self):
        """PDD 6-K 文件名无 8 位日期，从正文标题（如 'Second Quarter 2026
        Unaudited Financial Results'）提取财期，避免全部落为 Unknown 撞名"""
        text = ("FORM 6-K ... PDD Holdings Inc. Announces Second Quarter 2026 "
                "Unaudited Financial Results ...")
        self.assertEqual(
            extract_fiscal_period("PDD_6K_2026-08-25.htm", "6-K", text=text),
            "2026Q2")
        text_q1 = "FORM 6-K ... First Quarter of 2025 Results ..."
        self.assertEqual(
            extract_fiscal_period("PDD_6K_2025-05-27.htm", "6-K", text=text_q1),
            "2025Q1")

    def test_6k_without_quarter_info_is_unknown(self):
        text = "FORM 6-K ... Notice of Annual General Meeting ..."
        self.assertEqual(
            extract_fiscal_period("PDD_6K_2021-12-03.htm", "6-K", text=text),
            "Unknown")

    def test_6k_detected_from_filename_and_form(self):
        self.assertEqual(detect_filing_type("PDD_6K_2026-08-25.htm"), "6-K")
        self.assertEqual(detect_filing_type("whatever.htm", "FORM 6-K ..."), "6-K")

    def test_6k_uses_10q_analyzer(self):
        """6-K 盈利公告与 10-Q 结构相近，复用 10-Q 分析器"""
        from sec_analysis.sec_10q import Sec10QAnalyzer
        self.assertIsInstance(get_analyzer("6-K"), Sec10QAnalyzer)


class TestNetCashIntegrity(unittest.TestCase):
    """净现金不得用 现金-总负债 近似伪造（无债务数据时宁缺勿错）"""

    def test_net_cash_not_computed_from_total_liabilities(self):
        from sec_analysis.extraction import _compute_derived_metrics
        m = {"cash_equivalents_num": 108901, "total_liabilities_num": 216660,
             "revenue_num": 112400, "revenue": "¥112,400M"}
        _compute_derived_metrics(m)
        self.assertNotIn("net_cash", m)
        self.assertNotIn("net_cash_num", m)


class TestNoXbrlNarrativeExtraction(unittest.TestCase):
    """无 iXBRL 的 6-K 盈利公告（如 PDD）：表格列为[前期, 本期, US$]，
    表格取第一个数会拿到前期值 —— 只从叙述句提取本期数据，表格值宁缺勿错"""

    HTML = "<html><body>no ix:nonFraction tags here</body></html>"
    TEXT = (
        "PDD Holdings Inc. Announces Second Quarter 2026 Unaudited Financial Results. "
        "Total revenues in the quarter were RMB112.4 billion (US$16.6 billion), an increase of 8% "
        "from RMB104.0 billion in the same quarter of 2025. "
        "Operating profit in the quarter was RMB27.8 billion (US$4.1 billion), an increase of 8%. "
        "Net income attributable to ordinary shareholders in the quarter was RMB27.2 billion "
        "(US$4.0 billion), a decrease from RMB30.8 billion in the same quarter of 2025. "
        "Net cash generated from operating activities was RMB25.7 billion (US$3.8 billion), "
        "compared with RMB21.6 billion in the same quarter of 2025. "
        "Revenues 103,985 112,358 16,560 Total Assets 630,045 663,407 97,774 "
        "Net income attributable to ordinary shareholders 30,754 27,182 4,006"
    )

    def test_narrative_metrics_current_period(self):
        m = extract_key_metrics(self.TEXT, html=self.HTML)
        self.assertEqual(m["revenue"], "¥112,400M")
        self.assertEqual(m["revenue_num"], 112400)
        self.assertEqual(m["net_income"], "¥27,200M")
        self.assertEqual(m["net_income_num"], 27200)
        self.assertEqual(m["operating_income"], "¥27,800M")

    def test_table_values_suppressed(self):
        """表格取列序不可靠（本期在第二列），不得输出前期值冒充本期"""
        m = extract_key_metrics(self.TEXT, html=self.HTML)
        self.assertNotIn("total_assets", m)
        # 表格第一列的前期净利润 30,754 不得出现
        self.assertNotEqual(m.get("net_income_num"), 30754)

    def test_narrative_cash_flow_with_html_tags(self):
        """传入的是原始 HTML，叙述句中可能夹杂标签（RMB</FONT>25.7 billion）"""
        from sec_analysis.extraction import extract_cash_flows
        html = ("<html><body>Net cash generated from operating activities was "
                "RMB</FONT>25.7 billion (US$3.8 billion), compared with "
                "RMB21.6 billion in the same quarter of 2025.</body></html>")
        cf = extract_cash_flows(html)
        self.assertEqual(cf["operating"], "¥25,700M")
        self.assertEqual(cf["operating_num"], 25700)

    def test_narrative_cash_flow(self):
        from sec_analysis.extraction import extract_cash_flows
        cf = extract_cash_flows(self.HTML.replace("</body>",
            self.TEXT + "</body>"))
        self.assertEqual(cf["operating"], "¥25,700M")

    def test_narrative_million_unit(self):
        """早期 6-K 叙述句用 million（如 'RMB23,046.2 million'）"""
        text = ("Total revenues in the quarter were RMB23,046.2 million "
                "(US$3,569.4 million), an increase of 89%. "
                "Net income attributable to ordinary shareholders in the quarter "
                "was RMB2,414.6 million (US$374.0 million).")
        m = extract_key_metrics(text, html=self.HTML)
        self.assertEqual(m["revenue"], "¥23,046M")
        self.assertEqual(m["revenue_num"], 23046)
        self.assertEqual(m["net_income_num"], 2415)

    def test_narrative_usd_currency(self):
        text = "Total revenues in the quarter were US$8.5 billion, an increase of 8%."
        m = extract_key_metrics(text, html=self.HTML)
        self.assertEqual(m["revenue"], "$8,500M")


class TestAggregateParsing(unittest.TestCase):
    """聚合报告解析：20-F/6-K 类型识别与 ¥ 货币数值"""

    def _write_md(self, tmpdir, name, body):
        import os
        os.makedirs(tmpdir, exist_ok=True)
        path = os.path.join(tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_filing_type_20f_and_6k(self):
        from sec_analysis.report import parse_extraction_md
        import tempfile
        body = ("## 财务指标\n\n| 指标 | 数值 |\n|:---|:---|\n"
                "| 营收 | $1,000M |\n")
        with tempfile.TemporaryDirectory() as d:
            p20f = self._write_md(d, "20-F_FY2025.md", body)
            p6k = self._write_md(d, "6-K_2026Q2.md", body)
            self.assertEqual(parse_extraction_md(p20f)["filing_type"], "20-F")
            self.assertEqual(parse_extraction_md(p6k)["filing_type"], "6-K")

    def test_parse_rmb_amount(self):
        from sec_analysis.report import parse_extraction_md
        import tempfile
        body = ("# PDD 6-K 2026Q2\n\n## 财务指标\n\n"
                "| 指标 | 数值 |\n|:---|:---|\n"
                "| 营收 | ¥112,400M |\n"
                "| 净利润 | ¥27,200M |\n")
        with tempfile.TemporaryDirectory() as d:
            p = self._write_md(d, "6-K_2026Q2.md", body)
            record = parse_extraction_md(p)
            self.assertEqual(record["revenue"], 112400)
            self.assertEqual(record["net_income"], 27200)
            self.assertEqual(record["currency"], "¥")

    def test_parse_usd_no_currency_key(self):
        from sec_analysis.report import parse_extraction_md
        import tempfile
        body = ("# MSFT 10-K FY2026\n\n## 财务指标\n\n"
                "| 指标 | 数值 |\n|:---|:---|\n"
                "| 营收 | $331,839M |\n")
        with tempfile.TemporaryDirectory() as d:
            p = self._write_md(d, "10-K_FY2026.md", body)
            record = parse_extraction_md(p)
            self.assertNotIn("currency", record)


class TestQuarterlyDurationSelection(unittest.TestCase):
    """10-Q 收益表科目应取单季（≈3个月 duration）context，
    而非同年内数值最大的 YTD 累计 context"""

    def _html(self, facts, contexts):
        ctx_xml = "".join(
            f'<xbrli:context id="{cid}"><xbrli:period>'
            + (f"<xbrli:startDate>{s}</xbrli:startDate>" if s else "")
            + f"<xbrli:endDate>{e}</xbrli:endDate></xbrli:period></xbrli:context>"
            for cid, s, e in contexts)
        return f"<html><xbrli:xbrl>{ctx_xml}{facts}</xbrli:xbrl></html>"

    CTX = [
        ("Q3", "2026-03-29", "2026-06-27"),     # 单季 3 个月
        ("YTD9", "2025-09-28", "2026-06-27"),   # 9 个月累计
        ("INST", None, "2026-06-27"),           # 时点（资产负债表）
    ]

    def test_quarterly_income_picks_3month(self):
        html = self._html(
            _ix("us-gaap:Revenues", "Q3", "95,000", 6)
            + _ix("us-gaap:Revenues", "YTD9", "364,357", 6),
            self.CTX)
        m = extract_key_metrics("no text", html=html, quarterly=True)
        self.assertEqual(m["revenue_num"], 95000)

    def test_annual_keeps_max_within_year(self):
        """10-K/20-F 场景（quarterly=False）保持原行为：同年最大值"""
        html = self._html(
            _ix("us-gaap:Revenues", "Q3", "95,000", 6)
            + _ix("us-gaap:Revenues", "YTD9", "364,357", 6),
            self.CTX)
        m = extract_key_metrics("no text", html=html, quarterly=False)
        self.assertEqual(m["revenue_num"], 364357)

    def test_quarterly_keeps_instant_balance_sheet(self):
        """资产负债表为时点 context，不受单季筛选影响"""
        html = self._html(
            _ix("us-gaap:Assets", "INST", "630,045", 6)
            + _ix("us-gaap:Revenues", "Q3", "95,000", 6)
            + _ix("us-gaap:Revenues", "YTD9", "364,357", 6),
            self.CTX)
        m = extract_key_metrics("no text", html=html, quarterly=True)
        self.assertEqual(m["total_assets_num"], 630045)
        self.assertEqual(m["revenue_num"], 95000)

    def test_quarterly_eps_picks_3month(self):
        html = self._html(
            _ix("us-gaap:EarningsPerShareDiluted", "Q3", "1.05")
            + _ix("us-gaap:EarningsPerShareDiluted", "YTD9", "3.24"),
            self.CTX)
        m = extract_key_metrics("no text", html=html, quarterly=True)
        self.assertEqual(m["eps"], "$1.05")

    def test_455_quarter_within_bounds(self):
        """4-4-5 财历单季 84-91 天，正常识别"""
        ctx = [("Q1", "2025-12-29", "2026-04-03"), ("INST", None, "2026-04-03")]
        html = self._html(_ix("us-gaap:Revenues", "Q1", "21,000", 6), ctx)
        m = extract_key_metrics("no text", html=html, quarterly=True)
        self.assertEqual(m["revenue_num"], 21000)


class TestSharesOutstanding(unittest.TestCase):
    """封面 dei:EntityCommonStockSharesOutstanding 提取 —— DCF 每股估值的兜底数据源
    （无 Futu 行情时从 SEC 财报封面取流通股数；6-K 无此标签则缺失，不伪造）"""

    def test_dei_shares_extracted(self):
        html = ('<html><ix:nonFraction contextRef="C1" unitRef="shares" '
                'name="dei:EntityCommonStockSharesOutstanding" '
                'decimals="INF">7,507,980,444</ix:nonFraction></html>')
        m = extract_key_metrics("no text", html=html)
        self.assertEqual(m["shares_outstanding_num"], 7507980444)

    def test_dei_sums_share_classes_with_scale(self):
        """多类股（GOOGL A/B/C、NKE A/B）求和；scale 语义: 值×10^scale=实际股数
        （GOOGL scale=6 值 5,941 表示 5.941B；QCOM scale=6 值 1,050 表示 1.05B）"""
        html = ('<html>'
                '<ix:nonFraction contextRef="C1" name="dei:EntityCommonStockSharesOutstanding" scale="6">5,941</ix:nonFraction>'
                '<ix:nonFraction contextRef="C2" name="dei:EntityCommonStockSharesOutstanding" scale="6">882</ix:nonFraction>'
                '<ix:nonFraction contextRef="C3" name="dei:EntityCommonStockSharesOutstanding" scale="0">12,345</ix:nonFraction>'
                '</html>')
        m = extract_key_metrics("no text", html=html)
        self.assertEqual(m["shares_outstanding_num"], 5941 * 10**6 + 882 * 10**6 + 12345)

    def test_no_dei_no_fabricated_shares(self):
        html = ('<html><ix:nonFraction contextRef="C1" unitRef="U_USD" '
                'name="us-gaap:Revenues">95,000</ix:nonFraction></html>')
        m = extract_key_metrics("no text", html=html)
        self.assertNotIn("shares_outstanding_num", m)

    def test_extraction_md_roundtrip(self):
        """生成 md 写入「流通股数 | 7,508.0M」，解析回读为百万股浮点"""
        from sec_analysis.report import parse_extraction_md
        body = ("# MSFT 10-K FY2026\n\n## 财务指标\n\n"
                "| 指标 | 数值 |\n|:---|:---|\n"
                "| 营收 | $331,839M |\n"
                "| 流通股数 | 7,508.0M |\n")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "10-K_FY2026.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            record = parse_extraction_md(p)
        self.assertEqual(record["shares_outstanding"], 7508.0)


if __name__ == "__main__":
    unittest.main()
