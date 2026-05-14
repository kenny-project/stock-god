#!/usr/bin/env python3
"""
巴菲特式现金流折现估值模型 (DCF)
基于 Warren Buffett 的 "Owner Earnings" 理念实现。

Owner Earnings = 净利润 + 折旧摊销 - 维护性资本支出
内在价值 = Σ(Owner Earnings × (1+g)^t / (1+r)^t) + 终值 / (1+r)^n

数据来源：
  1. Futu OpenD（实时行情：股价、市值、流通股数）
  2. sec_analysis 提取的 .md 文件（历史财务数据：营收、净利润、折旧、CapEx 等）

用法：
    python3 dcf.py US.NKE
    python3 dcf.py QCOM --growth 8 --discount 10 --terminal-growth 3
    python3 dcf.py AAPL --years 10 --safety 0.25
"""

import argparse
import importlib.util
import os
import re
import sys
from datetime import datetime

# 目录配置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYSIS_DIR = os.path.join(PROJECT_ROOT, "reports", "sec_analysis")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")


# ── 数据获取 ────────────────────────────────────────────

def get_futu_snapshot(ticker):
    """从 Futu OpenD 获取实时行情快照"""
    try:
        futuapi_path = os.path.expanduser("~/.openclaw/skills/futuapi/scripts/common.py")
        spec = importlib.util.spec_from_file_location("_fc", futuapi_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_fc"] = mod
        spec.loader.exec_module(mod)

        ctx = mod.create_quote_context()
        try:
            symbol = ticker.split(".")[-1] if "." in ticker else ticker
            if not symbol.startswith("US.") and not symbol.startswith("HK."):
                symbol = f"US.{symbol}"

            ret, data = ctx.get_market_snapshot([symbol])
            if ret != 0 or data is None or data.empty:
                return None

            row = data.iloc[0]
            return {
                "name": str(row.get("name", "")),
                "price": float(row.get("last_price", 0) or 0),
                "marketCap": float(row.get("total_market_val", 0) or 0),
                "sharesOutstanding": float(row.get("outstanding_shares", 0) or 0),
                "pe": float(row.get("pe_ttm_ratio", 0) or 0),
                "pb": float(row.get("pb_ratio", 0) or 0),
            }
        finally:
            mod.safe_close(ctx)
    except Exception as e:
        print(f"  ⚠️  Futu 获取行情失败: {e}")
        return None


# ── SEC 分析数据读取 ────────────────────────────────────

def read_sec_analysis(ticker):
    """从 sec_analysis 提取的 .md 文件中读取历史财务数据"""
    # 去掉市场前缀（如 US.NKE -> NKE）
    symbol = ticker.split(".")[-1].upper() if "." in ticker else ticker.upper()
    analysis_dir = os.path.join(ANALYSIS_DIR, symbol)
    if not os.path.exists(analysis_dir):
        return []

    history = []
    md_files = sorted([f for f in os.listdir(analysis_dir) if f.endswith(".md")])

    for md_file in md_files:
        filepath = os.path.join(analysis_dir, md_file)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        record = {"source": md_file, "type": "10-K" if "10-K" in md_file else "10-Q"}

        # 提取期间（优先从文件名提取，如 10-K_FY2025.md -> FY2025）
        period_match = re.search(r"_(\w+)\.md$", md_file)
        if period_match:
            record["period"] = period_match.group(1)
        else:
            # 备用：从标题行提取（如 # AAPL 10-K FY2025）
            title_match = re.search(r"^#\s+\w+\s+10-[KQ]\s+(\S+)", content, re.MULTILINE)
            if title_match:
                record["period"] = title_match.group(1)

        # 提取财务数据
        # 营收
        rev_match = re.search(r"\|\s*营收\s*\|\s*\$?([\d,]+)M?\s*\|", content)
        if rev_match:
            record["revenue"] = int(rev_match.group(1).replace(",", ""))

        # 净利润
        ni_match = re.search(r"\|\s*净利润\s*\|\s*\$?([\d,]+)M?\s*\|", content)
        if ni_match:
            record["net_income"] = int(ni_match.group(1).replace(",", ""))

        # 营业利润
        oi_match = re.search(r"\|\s*营业利润\s*\|\s*\$?([\d,]+)M?\s*\|", content)
        if oi_match:
            record["operating_income"] = int(oi_match.group(1).replace(",", ""))

        # 自由现金流
        fcf_match = re.search(r"\|\s*自由现金流\s*\|\s*(?:\()?\$?([\d,]+)M?(?:\))?\s*\|", content)
        if fcf_match:
            val = int(fcf_match.group(1).replace(",", ""))
            if "($" in content.split("自由现金流")[1][:30]:
                val = -val
            record["fcf"] = val

        # 经营现金流
        cfo_match = re.search(r"\|\s*经营现金流\s*\|\s*(?:\()?\$?([\d,]+)M?(?:\))?\s*\|", content)
        if cfo_match:
            val = int(cfo_match.group(1).replace(",", ""))
            if "($" in content.split("经营现金流")[1][:30]:
                val = -val
            record["cfo"] = val

        # 折旧/摊销（兼容 "折旧/摊销" 和 "折旧摊销"，数值可能带括号表示负数）
        da_match = re.search(r"\|\s*折旧/?摊销\s*\|\s*(?:\()?\$?([\d,]+)M?(?:\))?\s*\|", content)
        if da_match:
            record["depreciation"] = int(da_match.group(1).replace(",", ""))

        # 资本支出 (CapEx)，数值可能带括号表示负数如 ($430M)
        capex_match = re.search(r"\|\s*资本支出\s*\|\s*(?:\()?\$?([\d,]+)M?(?:\))?\s*\|", content)
        if capex_match:
            record["capex"] = int(capex_match.group(1).replace(",", ""))

        # 总资产
        ta_match = re.search(r"\|\s*总资产\s*\|\s*\$?([\d,]+)M?\s*\|", content)
        if ta_match:
            record["total_assets"] = int(ta_match.group(1).replace(",", ""))

        # 股东权益
        eq_match = re.search(r"\|\s*股东权益\s*\|\s*\$?([\d,]+)M?\s*\|", content)
        if eq_match:
            record["equity"] = int(eq_match.group(1).replace(",", ""))

        # EPS
        eps_match = re.search(r"\|\s*每股收益.*?\|\s*\$?([\d.]+)\s*\|", content)
        if eps_match:
            record["eps"] = float(eps_match.group(1))

        if record.get("revenue") or record.get("net_income"):
            history.append(record)

    # 只保留年报数据（10-K），用于 DCF 计算
    annual = [h for h in history if h.get("type") == "10-K"]
    return annual


# ── 数据验证 ────────────────────────────────────────────

def validate_sec_data(sec_data):
    """
    验证 SEC 提取数据的质量和合理性。

    检查项：
    1. 必填字段是否存在（营收、净利润）
    2. 关键字段是否为 0（折旧、CapEx、现金流）
    3. 数值是否在合理范围内（相对营收的比例）
    4. 交叉验证（FCF ≈ CFO - CapEx）

    返回 (errors, warnings):
      errors: 严重问题，应终止计算
      warnings: 警告，数据可能有问题但可继续
    """
    errors = []
    warnings = []

    if not sec_data:
        errors.append("无 SEC 数据可验证")
        return errors, warnings

    for record in sec_data:
        period = record.get("period", "未知")
        source = record.get("source", "未知")
        revenue = record.get("revenue", 0)

        # ── 1. 必填字段检查 ──
        if not revenue or revenue <= 0:
            errors.append(f"[{period}] 营收缺失或为 0 — 无法进行 DCF 估值")

        net_income = record.get("net_income")
        if net_income is None:
            errors.append(f"[{period}] 净利润缺失 — Owner Earnings 无法计算")

        # ── 2. 关键字段为 0 检查 ──
        depreciation = record.get("depreciation", 0)
        capex = record.get("capex", 0)
        fcf = record.get("fcf", 0)
        cfo = record.get("cfo", 0)

        if depreciation == 0 and revenue > 0:
            warnings.append(
                f"[{period}] 折旧/摊销 = 0 — 对于有固定资产的公司这不正常 "
                f"(营收 ${revenue:,}M)"
            )

        if capex == 0 and revenue > 0:
            warnings.append(
                f"[{period}] 资本支出 = 0 — 对于有固定资产的公司这不正常 "
                f"(营收 ${revenue:,}M)"
            )

        if cfo == 0 and revenue > 0:
            warnings.append(
                f"[{period}] 经营现金流 = 0 — 可能提取失败"
            )

        if fcf == 0 and revenue > 0:
            warnings.append(
                f"[{period}] 自由现金流 = 0 — 可能提取失败"
            )

        # ── 3. 数值范围检查 ──
        if revenue > 0:
            # 折旧通常占营收 1-8%
            if depreciation > 0:
                dep_ratio = depreciation / revenue
                if dep_ratio > 0.15:
                    warnings.append(
                        f"[{period}] 折旧/摊销占营收 {dep_ratio*100:.1f}% — 异常偏高（通常 <8%）"
                    )
                elif dep_ratio < 0.005:
                    warnings.append(
                        f"[{period}] 折旧/摊销占营收 {dep_ratio*100:.2f}% — 异常偏低（通常 >1%）"
                    )

            # CapEx 通常占营收 2-12%
            if capex > 0:
                capex_ratio = capex / revenue
                if capex_ratio > 0.20:
                    warnings.append(
                        f"[{period}] CapEx 占营收 {capex_ratio*100:.1f}% — 异常偏高（通常 <12%）"
                    )
                elif capex_ratio < 0.005:
                    warnings.append(
                        f"[{period}] CapEx 占营收 {capex_ratio*100:.2f}% — 异常偏低（通常 >2%）"
                    )

            # 净利润占营收 -20% ~ 50%（极端情况除外）
            if net_income is not None:
                ni_ratio = net_income / revenue
                if ni_ratio > 0.50:
                    warnings.append(
                        f"[{period}] 净利率 {ni_ratio*100:.1f}% — 异常偏高（通常 <40%）"
                    )
                elif ni_ratio < -0.20:
                    warnings.append(
                        f"[{period}] 净利率 {ni_ratio*100:.1f}% — 严重亏损"
                    )

        # ── 4. 交叉验证 ──
        if cfo > 0 and capex > 0 and fcf > 0:
            expected_fcf = cfo - capex
            diff_pct = abs(fcf - expected_fcf) / max(cfo, 1) * 100
            if diff_pct > 20:
                warnings.append(
                    f"[{period}] FCF({fcf:,}M) 与 CFO-CapEx({expected_fcf:,}M) "
                    f"差异 {diff_pct:.0f}% — 数据可能不一致"
                )

        # ── 5. Owner Earnings 可行性预检 ──
        if net_income is not None and revenue > 0:
            if depreciation == 0 and capex == 0:
                # 两个关键输入都缺失，Owner Earnings 会退化为 FCF
                warnings.append(
                    f"[{period}] 折旧和 CapEx 均为 0 — Owner Earnings 将退化为 FCF，"
                    f"估值结果不可靠"
                )

    return errors, warnings


# ── DCF 核心计算 ────────────────────────────────────────

def calculate_owner_earnings(net_income, depreciation, maintenance_capex):
    """
    巴菲特 Owner Earnings 定义：
    Owner Earnings = 净利润 + 折旧/摊销 - 维护性资本支出

    注：维护性资本支出 ≈ 总资本支出 × 60%（经验值，维持现有业务所需的最低资本支出）
    """
    return net_income + depreciation - maintenance_capex


def estimate_growth_rate(historical_fcf, historical_revenue=None):
    """
    估算未来增长率：
    1. 基于历史 FCF 的复合增长率
    2. 基于历史营收增长率（辅助参考）
    3. 保守上限：15%（巴菲特原则：不预测过高增长）

    返回 (fcf_cagr, revenue_cagr, recommended_growth)
    """
    fcf_cagr = None
    revenue_cagr = None

    # FCF CAGR
    if len(historical_fcf) >= 2:
        positive_fcf = [f for f in historical_fcf if f > 0]
        if len(positive_fcf) >= 2:
            first, last = positive_fcf[0], positive_fcf[-1]
            years = len(positive_fcf) - 1
            if first > 0 and last > 0:
                fcf_cagr = (last / first) ** (1 / years) - 1

    # 营收 CAGR
    if historical_revenue and len(historical_revenue) >= 2:
        first, last = historical_revenue[0], historical_revenue[-1]
        years = len(historical_revenue) - 1
        if first > 0 and last > 0:
            revenue_cagr = (last / first) ** (1 / years) - 1

    # 推荐增长率：
    # 1. 只考虑正值的 CAGR（负增长不代表未来也会负增长）
    # 2. 取 FCF CAGR 和营收 CAGR 中的较低值（保守原则）
    # 3. 上限 15%，下限 2%
    candidates = [g for g in [fcf_cagr, revenue_cagr] if g is not None and g > 0]
    if candidates:
        recommended = min(min(candidates), 0.15)
        recommended = max(recommended, 0.02)
    else:
        # 所有 CAGR 都为负或无数据，默认 2%（保守）
        recommended = 0.02

    return fcf_cagr, revenue_cagr, recommended


def dcf_valuation(
    base_owner_earnings,
    growth_rate,
    discount_rate,
    terminal_growth_rate,
    projection_years,
    shares_outstanding,
    current_price,
):
    """
    DCF 估值计算

    参数：
        base_owner_earnings: 基期 Owner Earnings（百万美元）
        growth_rate: 预测期增长率（如 0.08 表示 8%）
        discount_rate: 折现率 / WACC（如 0.10 表示 10%）
        terminal_growth_rate: 永续增长率（如 0.03 表示 3%）
        projection_years: 预测年数
        shares_outstanding: 流通股数（百万股）
        current_price: 当前股价（美元）

    返回：
        dict 包含详细计算过程和结果
    """
    result = {
        "base_earnings": base_owner_earnings,
        "growth_rate": growth_rate,
        "discount_rate": discount_rate,
        "terminal_growth": terminal_growth_rate,
        "projection_years": projection_years,
        "shares_outstanding": shares_outstanding,
        "current_price": current_price,
        "projections": [],
    }

    # 防御性检查
    if discount_rate <= terminal_growth_rate:
        # 折现率必须大于永续增长率
        result["error"] = "折现率必须大于永续增长率"
        return result

    if base_owner_earnings <= 0:
        result["error"] = "基期 Owner Earnings 为负，无法进行 DCF 估值"
        return result

    # ── 第一步：预测未来现金流 ──
    total_pv = 0
    for year in range(1, projection_years + 1):
        # 预测第 t 年的 Owner Earnings
        projected_earnings = base_owner_earnings * (1 + growth_rate) ** year
        # 折现到当前
        pv = projected_earnings / (1 + discount_rate) ** year
        total_pv += pv

        result["projections"].append({
            "year": year,
            "projected_earnings": round(projected_earnings, 2),
            "present_value": round(pv, 2),
            "cumulative_pv": round(total_pv, 2),
        })

    result["sum_pv_projections"] = round(total_pv, 2)

    # ── 第二步：计算终值 ──
    # 终值 = 最后一年的 Owner Earnings × (1 + g) / (r - g)
    terminal_earnings = base_owner_earnings * (1 + growth_rate) ** projection_years
    terminal_value = terminal_earnings * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate)
    terminal_pv = terminal_value / (1 + discount_rate) ** projection_years

    result["terminal_value"] = round(terminal_value, 2)
    result["terminal_pv"] = round(terminal_pv, 2)

    # ── 第三步：企业内在价值 ──
    intrinsic_value = total_pv + terminal_pv
    result["intrinsic_value"] = round(intrinsic_value, 2)

    # ── 第四步：每股内在价值 ──
    if shares_outstanding > 0:
        per_share_value = intrinsic_value / shares_outstanding
        result["intrinsic_value_per_share"] = round(per_share_value, 2)

        # 安全边际价格
        result["margin_of_safety_25"] = round(per_share_value * 0.75, 2)
        result["margin_of_safety_50"] = round(per_share_value * 0.50, 2)

        # 当前估值状态
        if current_price > 0:
            upside = (per_share_value / current_price - 1) * 100
            result["upside_pct"] = round(upside, 1)
            if upside > 30:
                result["verdict"] = "严重低估"
            elif upside > 10:
                result["verdict"] = "低估"
            elif upside > -10:
                result["verdict"] = "合理估值"
            elif upside > -30:
                result["verdict"] = "高估"
            else:
                result["verdict"] = "严重高估"

    # ── 终值占比（敏感性指标）──
    if intrinsic_value > 0:
        result["terminal_pct"] = round(terminal_pv / intrinsic_value * 100, 1)

    return result


def sensitivity_analysis(
    base_owner_earnings, shares_outstanding, current_price,
    growth_rates, discount_rates, terminal_growth_rate, projection_years
):
    """敏感性分析：不同增长率 × 折现率下的每股内在价值"""
    table = []
    for g in growth_rates:
        row = {"growth": f"{g*100:.0f}%"}
        for r in discount_rates:
            if r <= terminal_growth_rate:
                row[f"r_{r*100:.0f}%"] = "N/A"
                continue
            result = dcf_valuation(
                base_owner_earnings, g, r,
                terminal_growth_rate, projection_years,
                shares_outstanding, current_price
            )
            if "error" in result:
                row[f"r_{r*100:.0f}%"] = "N/A"
            else:
                val = result.get("intrinsic_value_per_share", 0)
                row[f"r_{r*100:.0f}%"] = f"${val:.0f}"
        table.append(row)
    return table


# ── 报告生成 ────────────────────────────────────────────

def generate_report(ticker, dcf_result, sensitivity, owner_earnings_data, quote, params, data_source="SEC", warnings=None):
    """生成 DCF 估值报告 Markdown"""
    lines = []

    symbol = ticker.split(".")[-1] if "." in ticker else ticker
    company_name = quote.get("name", symbol) if quote else symbol

    lines.append(f"# {company_name} ({ticker}) DCF 估值分析")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"估值方法: 巴菲特 Owner Earnings 折现模型")
    lines.append(f"数据来源: {data_source}")
    lines.append("")

    # ── 基本信息 ──
    lines.append("## 基本信息")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|:---|")
    if quote:
        lines.append(f"| 公司名称 | {company_name} |")
        lines.append(f"| 当前股价 | ${quote.get('price', 0):.2f} |")
        mc = quote.get("marketCap", 0)
        lines.append(f"| 市值 | ${mc/1e9:.1f}B |" if mc else "| 市值 | - |")
        so = quote.get("sharesOutstanding", 0) or 0
        if so:
            lines.append(f"| 流通股数 | {so/1e6:.1f}M |")
        elif quote.get("marketCap") and quote.get("price"):
            derived_so = quote["marketCap"] / quote["price"] / 1e6
            lines.append(f"| 流通股数 | {derived_so:.1f}M (推算) |")
    lines.append("")

    # ── Owner Earnings 计算 ──
    lines.append("## Owner Earnings（所有者收益）")
    lines.append("")
    lines.append("> 巴菲特定义：Owner Earnings = 净利润 + 折旧/摊销 - 维护性资本支出")
    lines.append("")

    if owner_earnings_data:
        lines.append("| 年度 | 净利润 | 折旧摊销 | 维护CapEx | Owner Earnings | 营收 | FCF |")
        lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
        for d in owner_earnings_data:
            ni = f"${d['net_income']:,.0f}M" if d.get("net_income") is not None else "-"
            dep = f"${d['depreciation']:,.0f}M" if d.get("depreciation") is not None else "-"
            mcapex = f"${d['maintenance_capex']:,.0f}M" if d.get("maintenance_capex") is not None else "-"
            oe = f"${d['owner_earnings']:,.0f}M" if d.get("owner_earnings") is not None else "-"
            rev = f"${d['revenue']:,.0f}M" if d.get("revenue") is not None else "-"
            fcf = f"${d['fcf']:,.0f}M" if d.get("fcf") is not None else "-"
            lines.append(f"| {d.get('year', '-')} | {ni} | {dep} | {mcapex} | {oe} | {rev} | {fcf} |")
        lines.append("")
    else:
        lines.append("*未能获取历史 Owner Earnings 数据*")
        lines.append("")

    # ── DCF 计算参数 ──
    lines.append("## DCF 计算参数")
    lines.append("")
    lines.append("| 参数 | 数值 | 说明 |")
    lines.append("|:---|:---:|:---|")
    lines.append(f"| 基期 Owner Earnings | ${dcf_result['base_earnings']:,.0f}M | 最近一年的 Owner Earnings |")
    lines.append(f"| 预测增长率 | {dcf_result['growth_rate']*100:.1f}% | 基于历史数据估算 |")
    lines.append(f"| 折现率 (WACC) | {dcf_result['discount_rate']*100:.1f}% | 巴菲特通常使用 10% |")
    lines.append(f"| 永续增长率 | {dcf_result['terminal_growth']*100:.1f}% | 长期 GDP 增速附近 |")
    lines.append(f"| 预测年数 | {dcf_result['projection_years']}年 | 通常 5-10 年 |")
    lines.append("")

    # ── 预测现金流 ──
    lines.append("## 预测现金流（百万美元）")
    lines.append("")
    if dcf_result.get("projections"):
        lines.append("| 年份 | 预测 Owner Earnings | 折现值 | 累计折现值 |")
        lines.append("|:---:|:---:|:---:|:---:|")
        for p in dcf_result["projections"]:
            lines.append(
                f"| 第{p['year']}年 "
                f"| ${p['projected_earnings']:,.0f} "
                f"| ${p['present_value']:,.0f} "
                f"| ${p['cumulative_pv']:,.0f} |"
            )
        lines.append("")

    # ── 估值结果 ──
    lines.append("## 估值结果")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|:---|")
    lines.append(f"| 预测期现金流现值合计 | ${dcf_result.get('sum_pv_projections', 0):,.0f}M |")
    lines.append(f"| 终值 | ${dcf_result.get('terminal_value', 0):,.0f}M |")
    lines.append(f"| 终值折现 | ${dcf_result.get('terminal_pv', 0):,.0f}M |")
    lines.append(f"| **企业内在价值** | **${dcf_result.get('intrinsic_value', 0):,.0f}M** |")
    lines.append("")

    if dcf_result.get("intrinsic_value_per_share"):
        lines.append("### 每股估值")
        lines.append("")
        lines.append("| 项目 | 数值 |")
        lines.append("|:---|:---|")
        lines.append(f"| **每股内在价值** | **${dcf_result['intrinsic_value_per_share']:.2f}** |")
        if dcf_result.get("current_price"):
            lines.append(f"| 当前股价 | ${dcf_result['current_price']:.2f} |")
            lines.append(f"| 上行空间 | {dcf_result.get('upside_pct', 0):.1f}% |")
            lines.append(f"| **估值判断** | **{dcf_result.get('verdict', '-')}** |")
        lines.append(f"| 25%安全边际价格 | ${dcf_result.get('margin_of_safety_25', 0):.2f} |")
        lines.append(f"| 50%安全边际价格 | ${dcf_result.get('margin_of_safety_50', 0):.2f} |")
        lines.append("")

    # ── 终值占比 ──
    if dcf_result.get("terminal_pct"):
        lines.append(f"> 终值占内在价值的 {dcf_result['terminal_pct']}%")
        if dcf_result["terminal_pct"] > 70:
            lines.append("> ⚠️ 终值占比过高（>70%），估值对永续增长率假设敏感")
        lines.append("")

    # ── 敏感性分析 ──
    lines.append("## 敏感性分析")
    lines.append("")
    lines.append("不同增长率 × 折现率下的每股内在价值：")
    lines.append("")

    if sensitivity:
        # 表头
        discount_cols = [k for k in sensitivity[0].keys() if k.startswith("r_")]
        header = "| 增长率 \\ 折现率 | " + " | ".join(discount_cols) + " |"
        sep = "|:---|" + "|".join([":---:" for _ in discount_cols]) + "|"
        lines.append(header)
        lines.append(sep)
        for row in sensitivity:
            cells = [row.get("growth", "-")]
            for col in discount_cols:
                cells.append(row.get(col, "-"))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    # ── 巴菲特投资检查清单 ──
    lines.append("## 巴菲特投资检查清单")
    lines.append("")

    checks = []
    if dcf_result.get("verdict") in ["严重低估", "低估"]:
        checks.append(("✅", "估值", "内在价值高于当前股价"))
    else:
        checks.append(("❌", "估值", f"当前股价可能偏高（{dcf_result.get('verdict', '-')}）"))

    if dcf_result.get("terminal_pct", 100) < 60:
        checks.append(("✅", "确定性", "终值占比合理，预测较可靠"))
    else:
        checks.append(("⚠️", "确定性", "终值占比偏高，需关注长期假设"))

    if owner_earnings_data and len(owner_earnings_data) >= 2:
        oe_values = [d["owner_earnings"] for d in owner_earnings_data if d.get("owner_earnings")]
        if len(oe_values) >= 2 and oe_values[-1] > oe_values[0]:
            checks.append(("✅", "成长性", "Owner Earnings 呈增长趋势"))
        else:
            checks.append(("⚠️", "成长性", "Owner Earnings 增长不稳定"))

    if owner_earnings_data:
        latest = owner_earnings_data[-1]
        if latest.get("owner_earnings") and latest.get("revenue") and latest["revenue"] > 0:
            oe_margin = latest["owner_earnings"] / latest["revenue"] * 100
            if oe_margin > 15:
                checks.append(("✅", "盈利质量", f"Owner Earnings 利润率 {oe_margin:.1f}%（优秀）"))
            elif oe_margin > 8:
                checks.append(("✅", "盈利质量", f"Owner Earnings 利润率 {oe_margin:.1f}%（良好）"))
            else:
                checks.append(("⚠️", "盈利质量", f"Owner Earnings 利润率 {oe_margin:.1f}%（一般）"))

    lines.append("| 结果 | 维度 | 说明 |")
    lines.append("|:---:|:---|:---|")
    for check in checks:
        lines.append(f"| {check[0]} | {check[1]} | {check[2]} |")
    lines.append("")

    # ── 数据质量警告 ──
    if warnings:
        lines.append("## 数据质量警告")
        lines.append("")
        lines.append("> 以下问题可能影响估值准确性，请仔细审查原始数据。")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    # ── 免责声明 ──
    lines.append("---")
    lines.append("")
    lines.append("> **免责声明**：DCF 模型的结果高度依赖输入假设（增长率、折现率、永续增长率）。")
    lines.append("> 本分析仅供参考，不构成投资建议。巴菲特本人也强调：宁要模糊的正确，不要精确的错误。")
    lines.append("")

    return "\n".join(lines)


# ── 主函数 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="巴菲特式 DCF 现金流折现估值")
    parser.add_argument("ticker", help="股票代码，如 US.NKE 或 AAPL")
    parser.add_argument("--growth", type=float, help="预测增长率（%%），如 8 表示 8%%")
    parser.add_argument("--discount", type=float, default=10.0, help="折现率（%%），默认 10%%")
    parser.add_argument("--terminal-growth", type=float, default=3.0, help="永续增长率（%%），默认 3%%")
    parser.add_argument("--years", type=int, default=10, help="预测年数，默认 10 年")
    parser.add_argument("--safety", type=float, default=0.25, help="安全边际（0-1），默认 0.25")

    args = parser.parse_args()
    ticker = args.ticker.upper()

    print(f"\n{'='*60}")
    print(f"💰 {ticker} 巴菲特式 DCF 估值")
    print(f"{'='*60}\n")

    # ── 1. 获取实时行情 ──
    print("📊 获取实时行情（Futu）...")
    quote = get_futu_snapshot(ticker)
    if quote:
        print(f"  股价: ${quote.get('price', 0):.2f}")
        print(f"  市值: ${quote.get('marketCap', 0)/1e9:.1f}B")
    else:
        print("  ⚠️  无法获取行情数据")

    # ── 2. 从 SEC 分析数据读取历史数据 ──
    print("\n📁 读取 SEC 分析数据...")
    sec_data = read_sec_analysis(ticker)
    if sec_data:
        print(f"  找到 {len(sec_data)} 期年报数据")
        for d in sec_data:
            print(f"    {d.get('period', '?')}: 营收=${d.get('revenue', 0):,}M, 净利润=${d.get('net_income', 0):,}M")
    else:
        print("  ⚠️  未找到 SEC 分析数据")

    # ── 2.5 数据验证 ──
    print("\n🔍 验证数据质量...")
    errors, warnings = validate_sec_data(sec_data)
    if warnings:
        for w in warnings:
            print(f"  ⚠️  {w}")
    if errors:
        for e in errors:
            print(f"  ❌ {e}")
        print("\n❌ 数据验证失败，存在严重问题，无法进行可靠的 DCF 估值")
        sys.exit(1)

    # ── 3. 计算 Owner Earnings ──
    print("\n🧮 计算 Owner Earnings...")
    owner_earnings_data = []

    for d in sec_data:
        period = d.get("period", "?")
        ni = d.get("net_income", 0)
        revenue = d.get("revenue", 0)
        depreciation = d.get("depreciation", 0)
        capex = d.get("capex", 0)
        fcf = d.get("fcf", 0)
        cfo = d.get("cfo", 0)

        # 计算 Owner Earnings
        if depreciation > 0 and capex > 0:
            maintenance_capex = capex * 0.6
            owner_earnings = calculate_owner_earnings(ni, depreciation, maintenance_capex)
        else:
            maintenance_capex = 0
            owner_earnings = fcf if fcf > 0 else 0

        owner_earnings_data.append({
            "year": period,
            "net_income": ni,
            "revenue": revenue,
            "fcf": fcf,
            "cfo": cfo,
            "depreciation": depreciation,
            "capex": capex,
            "maintenance_capex": maintenance_capex,
            "owner_earnings": owner_earnings,
        })

    if owner_earnings_data:
        print(f"  数据来源: SEC")
        print(f"  计算了 {len(owner_earnings_data)} 期 Owner Earnings")
        for d in owner_earnings_data:
            print(f"    {d.get('year', '?')}: Owner Earnings = ${d.get('owner_earnings', 0):,.0f}M")
    else:
        print("  ❌ 无法计算 Owner Earnings，数据不足")
        sys.exit(1)

    # ── 5. 估算增长率 ──
    fcf_history = [d["fcf"] for d in owner_earnings_data if d.get("fcf")]
    rev_history = [d["revenue"] for d in owner_earnings_data if d.get("revenue")]
    fcf_cagr, rev_cagr, recommended_growth = estimate_growth_rate(fcf_history, rev_history)

    if args.growth is not None:
        growth_rate = args.growth / 100
        print(f"\n📈 使用用户指定增长率: {args.growth:.1f}%")
    else:
        growth_rate = recommended_growth
        print(f"\n📈 估算增长率:")
        if fcf_cagr is not None:
            print(f"  FCF CAGR: {fcf_cagr*100:.1f}%")
        if rev_cagr is not None:
            print(f"  营收 CAGR: {rev_cagr*100:.1f}%")
        print(f"  推荐增长率: {growth_rate*100:.1f}%")

    discount_rate = args.discount / 100
    terminal_growth = args.terminal_growth / 100

    # ── 6. DCF 估值 ──
    # 取最新一年的 Owner Earnings 作为基期（反转后索引 0 为最早，-1 为最新）
    base_oe = owner_earnings_data[-1]["owner_earnings"]
    shares_outstanding = (quote.get("sharesOutstanding", 0) or 0) / 1e6 if quote else 0
    current_price = quote.get("price", 0) if quote else 0

    if shares_outstanding == 0 and quote and quote.get("marketCap") and current_price > 0:
        shares_outstanding = quote["marketCap"] / current_price / 1e6

    print(f"\n🔮 DCF 估值计算...")
    print(f"  基期 Owner Earnings: ${base_oe:,.0f}M")
    print(f"  增长率: {growth_rate*100:.1f}%")
    print(f"  折现率: {discount_rate*100:.1f}%")
    print(f"  永续增长率: {terminal_growth*100:.1f}%")
    print(f"  预测年数: {args.years}年")

    dcf_result = dcf_valuation(
        base_oe, growth_rate, discount_rate, terminal_growth,
        args.years, shares_outstanding, current_price
    )

    if "error" in dcf_result:
        print(f"\n❌ 估值失败: {dcf_result['error']}")
        sys.exit(1)

    print(f"\n📊 估值结果:")
    print(f"  企业内在价值: ${dcf_result['intrinsic_value']:,.0f}M")
    if dcf_result.get("intrinsic_value_per_share"):
        print(f"  每股内在价值: ${dcf_result['intrinsic_value_per_share']:.2f}")
        print(f"  当前股价: ${current_price:.2f}")
        print(f"  上行空间: {dcf_result.get('upside_pct', 0):.1f}%")
        print(f"  判断: {dcf_result.get('verdict', '-')}")

    # ── 7. 敏感性分析 ──
    print(f"\n📊 敏感性分析...")
    growth_rates = [max(growth_rate - 0.03, 0.00), growth_rate - 0.01, growth_rate,
                    growth_rate + 0.01, growth_rate + 0.03]
    growth_rates = sorted(set(round(g, 4) for g in growth_rates))
    discount_rates = [0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.11, 0.12]

    sensitivity = sensitivity_analysis(
        base_oe, shares_outstanding, current_price,
        growth_rates, discount_rates, terminal_growth, args.years
    )

    # ── 8. 生成报告 ──
    report = generate_report(ticker, dcf_result, sensitivity, owner_earnings_data, quote,
                             {"growth": growth_rate, "discount": discount_rate,
                              "terminal_growth": terminal_growth, "years": args.years},
                             data_source="SEC", warnings=warnings)

    # 保存报告
    output_dir = os.path.join(REPORTS_DIR, "dcf")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{ticker}_DCF_{datetime.now().strftime('%Y%m%d')}.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n✅ DCF 估值报告已保存: {output_path}")
    return output_path


if __name__ == "__main__":
    main()
