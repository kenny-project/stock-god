#!/usr/bin/env python3
"""
利润表单季列审计（dry-run，只读不改）

扫描 reports/sec_analysis/{TICKER}/10-Q_*.md，用启发式标记疑似"利润表取到
YTD 累计列而非三个月单季列"的期：

  1. 净利率 > 阈值（默认 60%）——正常经营很少达到，常见于两类原因：
     取错累计列，或真实的大额一次性损益（需人工核对原文）
  2. 同年 NI 环比出现累计特征：Q(n) > Q(n-1)×1.7 且营收 Q(n) < Q(n-1)×1.4
     ——累计列混入时 NI 翻倍而营收增幅温和

启发式会同时命中"一次性损益"（如 GOOGL 2026Q2 的巨额投资利得），
标记≠取错列，仅提示人工复核。核对方法：打开对应 10-Q 原文，比对
iXBRL NetIncomeLoss 在"三个月"context（80-100 天 duration）的值。

用法:
  python3 scripts/audit_quarter_cols.py                # 审计全部 ticker
  python3 scripts/audit_quarter_cols.py GOOGL QCOM     # 只审计指定 ticker
  python3 scripts/audit_quarter_cols.py --margin 50    # 调整净利率阈值
"""

import argparse
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYSIS_DIR = os.path.join(PROJECT_ROOT, "reports", "sec_analysis")


def parse_quarter_md(path):
    """从 10-Q_*.md 提取 营收/净利润/净利率（百万单位数值），缺失返回 None"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    def _money(label):
        m = re.search(rf"\|\s*{label}\s*\|\s*\(?[$¥]?([\d,]+)M?(?:\))?\s*\|", content)
        return int(m.group(1).replace(",", "")) if m else None

    revenue, net_income = _money("营收"), _money("净利润")
    m = re.search(r"\|\s*净利率\s*\|\s*\(?([\d.]+)%", content)
    margin = float(m.group(1)) if m else (
        round(net_income / revenue * 100, 1)
        if net_income is not None and revenue else None)
    return {"revenue": revenue, "net_income": net_income, "net_margin": margin}


def parse_period(filename):
    """10-Q_2026Q2.md -> (2026, 2)；无法解析返回 None"""
    m = re.match(r"10-Q_(\d{4})Q([1-4])\.md$", filename)
    return (int(m.group(1)), int(m.group(2))) if m else None


def audit_ticker(ticker, margin_threshold, ni_jump, rev_cap):
    """审计单个 ticker，返回 (records, suspects)；records 按期间升序"""
    ticker_dir = os.path.join(ANALYSIS_DIR, ticker)
    records = []
    for f in os.listdir(ticker_dir):
        period = parse_period(f)
        if not period:
            continue
        rec = parse_quarter_md(os.path.join(ticker_dir, f))
        rec.update(ticker=ticker, year=period[0], q=period[1], file=f)
        records.append(rec)
    records.sort(key=lambda r: (r["year"], r["q"]))

    suspects = []
    for rec in records:
        if rec["net_margin"] is not None and rec["net_margin"] > margin_threshold:
            suspects.append((
                rec,
                f"净利率 {rec['net_margin']:.1f}% > {margin_threshold}%"
                f"（NI={rec['net_income']:,} / 营收={rec['revenue']:,}）"))

    by_year = {}
    for rec in records:
        by_year.setdefault(rec["year"], []).append(rec)
    for year, recs in sorted(by_year.items()):
        for prev, cur in zip(recs, recs[1:]):
            if not (prev["net_income"] and cur["net_income"]
                    and prev["revenue"] and cur["revenue"]):
                continue
            ni_ratio = cur["net_income"] / prev["net_income"]
            rev_ratio = cur["revenue"] / prev["revenue"]
            if ni_ratio > ni_jump and rev_ratio < rev_cap:
                suspects.append((
                    cur,
                    f"同年环比累计特征: NI Q{cur['q']}/Q{prev['q']}="
                    f"{ni_ratio:.2f}x > {ni_jump}x 而营收仅 {rev_ratio:.2f}x < {rev_cap}x"))
    return records, suspects


def main():
    parser = argparse.ArgumentParser(description="10-Q 利润表单季列审计（dry-run）")
    parser.add_argument("tickers", nargs="*", help="只审计指定 ticker（默认全部）")
    parser.add_argument("--margin", type=float, default=60.0, help="净利率阈值（%%，默认 60）")
    parser.add_argument("--ni-jump", type=float, default=1.7, help="NI 环比跳变阈值（默认 1.7）")
    parser.add_argument("--rev-cap", type=float, default=1.4, help="营收环比上限（默认 1.4）")
    args = parser.parse_args()

    if not os.path.isdir(ANALYSIS_DIR):
        print(f"目录不存在: {ANALYSIS_DIR}")
        sys.exit(1)

    tickers = args.tickers or sorted(
        d for d in os.listdir(ANALYSIS_DIR)
        if os.path.isdir(os.path.join(ANALYSIS_DIR, d)))

    total_periods, all_suspects = 0, []
    for ticker in tickers:
        ticker_dir = os.path.join(ANALYSIS_DIR, ticker)
        if not os.path.isdir(ticker_dir):
            print(f"⚠️  跳过 {ticker}: {ticker_dir} 不存在")
            continue
        records, suspects = audit_ticker(ticker, args.margin, args.ni_jump, args.rev_cap)
        total_periods += len(records)
        series = "  ".join(
            f"Q{r['q']}:{r['net_income']:,}" if r["net_income"] is not None else f"Q{r['q']}:N/A"
            for r in records if r["year"] == records[-1]["year"]) if records else ""
        print(f"\n== {ticker}（{len(records)} 期 10-Q）最新年 NI 序列: {series}")
        for rec, reason in suspects:
            print(f"   🚩 疑似 [{ticker} {rec['year']}Q{rec['q']}] {reason}")
            print(f"      文件: {os.path.join(ticker, rec['file'])}")
            all_suspects.append((ticker, rec, reason))

    print(f"\n{'='*60}")
    print(f"审计完成（dry-run，未修改任何文件）: 共 {len(tickers)} 个 ticker / "
          f"{total_periods} 期 10-Q，疑似 {len(all_suspects)} 期")
    if all_suspects:
        print("注意：启发式会同时命中'真实大额一次性损益'，标记≠取错列；")
        print("请用原文 iXBRL NetIncomeLoss 的三个月 context（80-100 天）核对后再定论。")


if __name__ == "__main__":
    main()
