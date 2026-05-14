#!/usr/bin/env python3
"""
SEC Filing Analysis - CLI 入口
分析下载的 SEC 财报（10-K/10-Q）
"""

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Optional

from .sec_10k import Sec10KAnalyzer
from .sec_10q import Sec10QAnalyzer
from .extraction import extract_key_metrics, extract_cash_flows, extract_detailed_profit_items
from .report import (
    detect_filing_type,
    extract_fiscal_period,
    generate_extraction_md,
    generate_analysis_report,
    ANALYSIS_DIR,
)

# 目录配置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "sec_filings")


def get_analyzer(filing_type: str):
    """根据财报类型获取对应的分析器"""
    if filing_type == "10-K":
        return Sec10KAnalyzer()
    elif filing_type == "10-Q":
        return Sec10QAnalyzer()
    else:
        raise ValueError(f"不支持的财报类型: {filing_type}")


def analyze_single_filing(
    ticker: str,
    htm_path: str,
    force: bool = False,
) -> Optional[str]:
    """分析单个财报文件"""
    filename = os.path.basename(htm_path)
    print(f"\n📄 正在分析: {filename}")

    # 读取 HTML 文件
    try:
        with open(htm_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as e:
        print(f"   ❌ 读取文件失败: {e}")
        return None

    # 检测财报类型
    filing_type = detect_filing_type(filename, html_content)
    print(f"   类型: {filing_type}")

    # 提取财年/季度
    fiscal_period = extract_fiscal_period(filename, filing_type)
    print(f"   期间: {fiscal_period}")

    # 获取对应的分析器
    analyzer = get_analyzer(filing_type)

    # 分析财报
    result = analyzer.analyze(html_content)
    text = result["text"]
    body_start = result["body_start"]
    sections = result["sections"]

    print(f"   文本长度: {len(text):,} 字符")
    print(f"   正文开始位置: {body_start:,}")

    # 打印章节检测结果
    for section_type, section_data in sections.items():
        if section_data.get("found"):
            content = section_data.get("content", "")
            print(f"   ✅ 找到 {section_type} ({len(content):,} 字符)")
        else:
            print(f"   ⚠️  未找到 {section_type}")

    # 提取关键指标
    metrics = extract_key_metrics(text)
    if metrics:
        print(f"   ✅ 找到 {len(metrics)} 个关键指标")

    # 提取现金流（需要 HTML 内容来解析 XBRL 标签）
    cash_flows = extract_cash_flows(html_content)
    if cash_flows:
        print(f"   ✅ 找到 {len(cash_flows)} 个现金流指标")

    # 提取详细利润项目
    detailed_items = extract_detailed_profit_items(text)
    if detailed_items:
        print(f"   ✅ 找到 {len(detailed_items)} 个详细利润项目")

    # ====== 计算现金流质量指标 ======
    if cash_flows and metrics:
        # CFO vs 净利润 = 经营现金流 / 净利润
        if "operating" in cash_flows and "net_income_num" in metrics and metrics["net_income_num"] > 0:
            op_match = re.search(r'[\$]?([\d,]+)M', cash_flows["operating"])
            if op_match:
                cfo_value = int(op_match.group(1).replace(",", ""))
                if cash_flows["operating"].startswith("("):
                    cfo_value = -cfo_value
                cfo_to_ni = cfo_value / metrics["net_income_num"]
                cash_flows["cfo_to_net_income"] = f"{cfo_to_ni:.2f}"
                cash_flows["cfo_to_net_income_num"] = cfo_to_ni

        # FCF 利润率 = FCF / 营收 × 100%
        if "free_cash_flow_num" in cash_flows and "revenue_num" in metrics and metrics["revenue_num"] > 0:
            fcf_margin = cash_flows["free_cash_flow_num"] / metrics["revenue_num"] * 100
            cash_flows["fcf_margin"] = f"{fcf_margin:.1f}%"
            cash_flows["fcf_margin_num"] = fcf_margin

        # CapEx/营收 = 资本支出 / 营收 × 100%
        if "capex_num" in cash_flows and "revenue_num" in metrics and metrics["revenue_num"] > 0:
            capex_to_rev = abs(cash_flows["capex_num"]) / metrics["revenue_num"] * 100
            cash_flows["capex_to_revenue"] = f"{capex_to_rev:.1f}%"
            cash_flows["capex_to_revenue_num"] = capex_to_rev

    # 生成提取文件
    output_path = generate_extraction_md(
        ticker=ticker,
        filing_type=filing_type,
        fiscal_period=fiscal_period,
        sections=sections,
        metrics=metrics,
        cash_flows=cash_flows,
        detailed_items=detailed_items,
        force=force,
    )

    print(f"   📁 输出: {output_path}")
    return output_path


def find_filing_files(ticker: str) -> list:
    """查找所有 .htm 文件"""
    ticker_dir = os.path.join(REPORTS_DIR, ticker.upper())
    if not os.path.exists(ticker_dir):
        print(f"❌ 目录不存在: {ticker_dir}")
        return []

    files = []
    for f in os.listdir(ticker_dir):
        if f.endswith(".htm") and not f.endswith("_index.html"):
            files.append(os.path.join(ticker_dir, f))

    return sorted(files)


def analyze_ticker(
    ticker: str,
    form: Optional[str] = None,
    force: bool = False,
    latest: bool = True,
    all_files: bool = False,
    file: Optional[str] = None,
):
    """分析指定公司的所有财报"""
    print(f"\n{'='*60}")
    print(f"📊 分析 {ticker.upper()} 的 SEC 财报")
    print(f"{'='*60}")

    # 查找所有财报文件
    files = find_filing_files(ticker)
    if not files:
        print("❌ 未找到财报文件")
        return

    print(f"\n📁 找到 {len(files)} 个财报文件")

    # 过滤表单类型
    if form:
        form_types = [f.strip().upper() for f in form.split(",")]
        # 支持多种文件名格式：10-K, 10k, 10_k
        files = [f for f in files if any(
            ft.replace("-", "") in os.path.basename(f).upper().replace("-", "").replace("_", "")
            for ft in form_types
        )]
        print(f"📋 过滤后: {len(files)} 个 {form} 文件")

    # 根据参数选择要分析的文件
    if file:
        # 分析指定文件
        target_file = None
        for f in files:
            if file in os.path.basename(f):
                target_file = f
                break
        if not target_file:
            print(f"❌ 未找到文件: {file}")
            return
        files = [target_file]
    elif latest:
        # 只分析最新的
        files = [files[-1]]
    elif not all_files:
        # 默认只分析最新的
        files = [files[-1]]

    print(f"\n🔍 将分析 {len(files)} 个文件\n")

    # 分析每个文件
    for f in files:
        analyze_single_filing(ticker, f, force=force)

    # 生成聚合分析报告
    if len(files) > 1 or all_files:
        print(f"\n📊 生成聚合分析报告...")
        report_path = generate_analysis_report(ticker, force=force)
        if report_path:
            print(f"📁 报告: {report_path}")

    print(f"\n{'='*60}")
    print(f"✅ 分析完成")
    print(f"{'='*60}")


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description="SEC Filing Analysis - 分析下载的 SEC 财报",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m sec_analysis MSFT                    # 分析最新财报
  python -m sec_analysis MSFT --all              # 分析所有财报
  python -m sec_analysis MSFT --form 10-K        # 只分析 10-K
  python -m sec_analysis MSFT --form 10-Q        # 只分析 10-Q
  python -m sec_analysis MSFT --force            # 强制重新提取
        """,
    )

    parser.add_argument("ticker", help="股票代码（如 MSFT, AAPL）")
    parser.add_argument("--form", help="表单类型过滤（10-K, 10-Q）")
    parser.add_argument("--force", action="store_true", help="强制重新提取已存在的文件")
    parser.add_argument("--latest", action="store_true", default=True, help="只分析最新的财报（默认）")
    parser.add_argument("--all", action="store_true", help="分析所有已下载的财报")
    parser.add_argument("--file", help="分析指定的单个文件")

    args = parser.parse_args()

    analyze_ticker(
        ticker=args.ticker,
        form=args.form,
        force=args.force,
        latest=args.latest if not args.all else False,
        all_files=args.all,
        file=args.file,
    )


if __name__ == "__main__":
    main()
