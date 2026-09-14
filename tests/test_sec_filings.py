#!/usr/bin/env python3
"""
sec_filings 下载完整性测试

运行: python3 -m unittest discover -s tests -v
覆盖的 bug（2026-09 修复）:
  1. data.sec.gov submissions 的 recent 字段最多 1000 条，申报频繁的公司
     (如 GOOGL，内部人 Form 4 多) 更早记录在 filings.files 分页中，被忽略后
     目标年份显示"无匹配财报"
  2. 按 filingDate 年份分组会把财年 Q1（上年日历年度申报）划出窗口，
     且把上一年度的 10-K 划入窗口（如 PFE FY2021 年报 2022-02 申报）
     —— 应按 reportDate + 财年截止月分组，锚定最新年报财年
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from sec_filings import (  # noqa: E402
    group_filings_by_fiscal_year, merge_submissions_pages, ANNUAL_FORMS,
)


def _mk(forms, filing_dates, report_dates):
    return {
        "form": forms, "filingDate": filing_dates, "reportDate": report_dates,
        "accessionNumber": [f"0000000{i:04d}" for i in range(len(forms))],
        "primaryDocument": [f"doc-{i}.htm" for i in range(len(forms))],
    }


class TestMergeSubmissionsPages(unittest.TestCase):
    """recent 被截断到 1000 条时，必须合并 filings.files 分页"""

    def test_merge_older_pages(self):
        main = {
            "filings": {
                "recent": _mk(["10-Q"], ["2026-07-25"], ["2026-06-30"]),
                "files": [{"name": "CIK1-submissions-001.json"}],
            }
        }
        page = _mk(["10-K", "10-Q"], ["2023-02-03", "2022-10-25"], ["2022-12-31", "2022-09-30"])
        merged = merge_submissions_pages(main, [page])
        self.assertEqual(merged["form"], ["10-Q", "10-K", "10-Q"])
        self.assertEqual(merged["filingDate"][0], "2026-07-25")  # recent 在前（最新在前）
        self.assertEqual(merged["reportDate"][-1], "2022-09-30")

    def test_no_files(self):
        main = {"filings": {"recent": _mk(["10-K"], ["2026-02-05"], ["2025-12-31"])}}
        merged = merge_submissions_pages(main, [])
        self.assertEqual(merged["form"], ["10-K"])


class TestFiscalYearGrouping(unittest.TestCase):
    """财年分组：锚定最新年报财年，按 reportDate + 财年截止月归类"""

    def test_msft_style_fy_june(self):
        """MSFT 财年止于 6 月：FY2022 Q1 于 2021-09 申报、period 2021-09-30，
        应归入 FY2022 而非被申报年窗口漏掉"""
        # 窗口 FY2022..FY2026（锚定最新年报 FY2026；FY2027 季报已申报但年报未出，
        # 不属于 5 年窗口）
        forms = (["10-Q", "10-Q", "10-Q", "10-K"]    # FY2022: Q1(2021-09-30)...10-K(2022-06-30)
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2023
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2024
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2025
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2026
                 + ["10-Q"])                          # FY2027 Q1（窗口外）
        report = ["2021-09-30", "2021-12-31", "2022-03-31", "2022-06-30",
                  "2022-09-30", "2022-12-31", "2023-03-31", "2023-06-30",
                  "2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30",
                  "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
                  "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
                  "2026-09-30"]
        filing = report  # 简化：申报日=期末日
        groups = group_filings_by_fiscal_year(forms, filing, report, years=5)
        self.assertEqual(sorted(groups.keys()), [2022, 2023, 2024, 2025, 2026])
        for fy, idx in groups.items():
            n_annual = sum(1 for i in idx if forms[i] in ANNUAL_FORMS)
            self.assertEqual(n_annual, 1, f"FY{fy} 应恰有 1 份年报")
            self.assertEqual(len(idx), 4, f"FY{fy} 应恰有 4 份财报")

    def test_pfe_fy2021_10k_excluded(self):
        """PFE 财年止于 12 月：窗口 FY2022..FY2026 时，FY2021 年报
        （period 2021-12-31，2022-02 申报）不应被按申报年划入窗口"""
        forms = (["10-K"]                             # FY2021（窗口外）
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2022
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2023
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2024
                 + ["10-Q", "10-Q", "10-Q", "10-K"]   # FY2025
                 + ["10-K"])                          # FY2026（锚定）
        report = ["2021-12-31",
                  "2022-04-03", "2022-07-03", "2022-10-02", "2022-12-31",
                  "2023-04-02", "2023-07-02", "2023-10-01", "2023-12-31",
                  "2024-03-31", "2024-06-30", "2024-09-29", "2024-12-31",
                  "2025-03-30", "2025-06-29", "2025-09-28", "2025-12-31",
                  "2026-12-31"]
        groups = group_filings_by_fiscal_year(forms, report, report, years=5)
        self.assertNotIn(2021, groups)
        self.assertEqual(sorted(groups.keys()), [2022, 2023, 2024, 2025, 2026])
        for fy, idx in groups.items():
            if fy == 2026:  # 锚定年（最新年报）的季报尚未出齐，仅校验含 1 份年报
                self.assertEqual(sum(1 for i in idx if forms[i] in ANNUAL_FORMS), 1)
                continue
            self.assertEqual(len(idx), 4, f"FY{fy} 应恰有 4 份财报")

    def test_insider_noise_ignored(self):
        """Form 4 等噪音表单不参与分组，也不影响锚定"""
        forms = ["4", "4", "10-K", "4", "10-Q", "10-Q", "10-Q"]
        report = ["2026-08-01", "2026-08-02", "2025-12-31", "2026-08-03",
                  "2025-03-31", "2025-06-30", "2025-09-30"]
        groups = group_filings_by_fiscal_year(forms, report, report, years=3)
        for fy, idx in groups.items():
            self.assertTrue(all(forms[i] in ("10-K", "10-Q") for i in idx))

    def test_interim_6k_excluded(self):
        """6-K（外国发行人中期报告）不参与 10-K/10-Q 财年分组"""
        forms = ["6-K", "20-F", "6-K", "6-K"]
        report = ["2025-05-14", "2025-04-30", "2024-11-12", "2025-03-18"]
        groups = group_filings_by_fiscal_year(forms, report, report, years=5)
        for fy, idx in groups.items():
            self.assertTrue(all(forms[i] in ANNUAL_FORMS for i in idx))

    def test_quarterly_only_new_ipo(self):
        """新上市公司（如 SPCX）尚无年报、只有季报 → 锚定最新季报财年，不得返回空"""
        forms = ["10-Q"]
        report = ["2026-06-30"]
        groups = group_filings_by_fiscal_year(forms, ["2026-08-04"], report, years=5)
        self.assertEqual(groups, {2026: [0]})

    def test_no_annual_or_quarterly_returns_empty(self):
        """既无年报也无季报（如仅 S-1）→ 返回空，由调用方报错"""
        forms = ["S-1", "424B4"]
        report = ["2026-05-01", "2026-05-02"]
        groups = group_filings_by_fiscal_year(forms, report, report, years=5)
        self.assertEqual(groups, {})


if __name__ == "__main__":
    unittest.main()
