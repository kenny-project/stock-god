"""sec_analysis 解析器回归测试：10-Q 利润表必须取"三个月"单季列。

10-Q 收益表同页披露 Three/Six Months 多组列，取错列会把 YTD 累计值
冒充单季，导致 compute_ttm/DCF 显著失真。契约（scripts/dcf.py compute_ttm）：
利润表科目=单季值，现金流科目=YTD 累计。本文件用最小 iXBRL 样例锁定：
  1. 同时含单季/累计 context 时取单季列（含 EPS）
  2. 仅含累计 context（识别不出单季）时置空告警，绝不用文本回退值编造
  3. 资产负债表 instant 科目不受单季筛选影响
"""

import os
import sys

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from sec_analysis.extraction import extract_key_metrics  # noqa: E402


def _context(ctx_id, start=None, end=None, instant=None):
    if instant:
        return (f'<xbrli:context id="{ctx_id}">'
                f"<xbrli:instant>{instant}</xbrli:instant></xbrli:context>")
    return (f'<xbrli:context id="{ctx_id}">'
            f"<xbrli:startDate>{start}</xbrli:startDate>"
            f"<xbrli:endDate>{end}</xbrli:endDate></xbrli:context>")


def _fact(name, ctx, value, scale="6", sign=""):
    sign_attr = ' sign="-"' if sign == "-" else ""
    return (f'<ix:nonFraction name="{name}" contextRef="{ctx}" unitRef="u1"'
            f'{sign_attr} scale="{scale}">{value}</ix:nonFraction>')


def _quarter_html():
    """模拟 10-Q Q2 收益表：单季 + YTD 两组列（列序同 GOOGL：上年季/本年季/上年累计/本年累计）"""
    contexts = "".join([
        _context("c-py-q", "2025-04-01", "2025-06-30"),
        _context("c-q", "2026-04-01", "2026-06-30"),
        _context("c-py-ytd", "2025-01-01", "2025-06-30"),
        _context("c-ytd", "2026-01-01", "2026-06-30"),
        _context("c-inst", instant="2026-06-30"),
    ])
    facts = "".join([
        # 收益表（单季列 + 累计列并存；累计值大于单季值）
        _fact("us-gaap:Revenues", "c-py-q", "90,000"),
        _fact("us-gaap:Revenues", "c-q", "100,000"),
        _fact("us-gaap:Revenues", "c-py-ytd", "180,000"),
        _fact("us-gaap:Revenues", "c-ytd", "210,000"),
        _fact("us-gaap:NetIncomeLoss", "c-py-q", "28,196"),
        _fact("us-gaap:NetIncomeLoss", "c-q", "49,615"),
        _fact("us-gaap:NetIncomeLoss", "c-py-ytd", "62,736"),
        _fact("us-gaap:NetIncomeLoss", "c-ytd", "112,193"),
        _fact("us-gaap:EarningsPerShareDiluted", "c-py-q", "5.11", scale="0"),
        _fact("us-gaap:EarningsPerShareDiluted", "c-q", "4.00", scale="0"),
        _fact("us-gaap:EarningsPerShareDiluted", "c-ytd", "9.11", scale="0"),
        # 资产负债表 instant 科目
        _fact("us-gaap:Assets", "c-inst", "921,983"),
    ])
    return f"<html><body>{contexts}{facts}</body></html>"


def test_quarter_column_selected_over_ytd():
    """同时含 3/6 个月列 → 净利润/营收/EPS 必须取三个月单季列，不是累计列"""
    m = extract_key_metrics("", _quarter_html(), quarterly=True)
    assert m["net_income_num"] == 49615          # 单季，而非 YTD 112,193
    assert m["revenue_num"] == 100000            # 单季，而非 YTD 210,000
    assert m["eps"] == "$4.00"                   # 单季 EPS，而非 YTD 9.11
    # instant 科目不受单季筛选影响
    assert m["total_assets_num"] == 921983


def test_annual_10k_ignores_quarter_filter():
    """annual（quarterly=False）场景保持原行为：取最新年最大实例（10-K 无累计列问题）"""
    m = extract_key_metrics("", _quarter_html(), quarterly=False)
    # 年报口径：同年份实例取最大值 → YTD 112,193 / 210,000
    assert m["net_income_num"] == 112193
    assert m["revenue_num"] == 210000


def test_ytd_only_filing_drops_text_fallback(capsys):
    """仅披露 YTD 累计 context（无单季）→ 置空并告警，不得用文本回退值编造单季"""
    contexts = "".join([
        _context("c-py-ytd", "2025-01-01", "2025-06-30"),
        _context("c-ytd", "2026-01-01", "2026-06-30"),
    ])
    facts = "".join([
        _fact("us-gaap:Revenues", "c-py-ytd", "180,000"),
        _fact("us-gaap:Revenues", "c-ytd", "210,000"),
        _fact("us-gaap:NetIncomeLoss", "c-py-ytd", "62,736"),
        _fact("us-gaap:NetIncomeLoss", "c-ytd", "112,193"),
        _fact("us-gaap:EarningsPerShareDiluted", "c-ytd", "9.11", scale="0"),
    ])
    html = f"<html><body>{contexts}{facts}</body></html>"
    # 文本回退会取到累计值 —— 必须被置空而非输出
    text = "Revenues $ 210,000 Net income 112,193 Diluted net income per share $ 9.11"
    m = extract_key_metrics(text, html, quarterly=True)
    assert "net_income" not in m and "net_income_num" not in m
    assert "revenue" not in m and "revenue_num" not in m
    assert "eps" not in m
    out = capsys.readouterr().out
    assert "单季 context" in out and "置空" in out


def test_quarter_facts_survive_when_text_missing():
    """正常 10-Q：单季 context 可识别时 XBRL 单季值正常输出（不误伤）"""
    m = extract_key_metrics("", _quarter_html(), quarterly=True)
    assert m["net_income"] == "$49,615M"
