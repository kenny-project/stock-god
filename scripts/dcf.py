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
import os
import re
import sys
from datetime import datetime

from common import QuoteSource
from dataversion import ANALYSIS_VERSION, DCF_VERSION

# 目录配置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYSIS_DIR = os.path.join(PROJECT_ROOT, "reports", "sec_analysis")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")


# ── 数据获取 ────────────────────────────────────────────
# 行情统一走 common.QuoteSource（Futu → 腾讯财经 fallback），契约见 common.py


# ── SEC 分析数据读取 ────────────────────────────────────

def _parse_gen_version(content):
    """解析分析 md 头部 `生成器版本: analysis-v2` 行 → "v2"；无该行 → None（legacy 旧数据）"""
    for line in content.splitlines()[:20]:
        m = re.match(r"生成器版本:\s*analysis-(\S+)$", line.strip())
        if m:
            return m.group(1)
    return None


def _read_sec_records(ticker):
    """从 sec_analysis 提取的 .md 文件中读取全部年报/季报记录"""
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

        record = {"source": md_file, "type": "10-Q",
                  "gen_version": _parse_gen_version(content)}
        # 表单类型：20-F（外国发行人年报）等同 10-K 年报口径；6-K 为中期申报
        if "20-F" in md_file:
            record["form"], record["type"] = "20-F", "10-K"
        elif "6-K" in md_file:
            record["form"], record["type"] = "6-K", "10-Q"
        elif "10-K" in md_file:
            record["form"], record["type"] = "10-K", "10-K"
        else:
            record["form"], record["type"] = "10-Q", "10-Q"

        # 提取期间（优先从文件名提取，如 10-K_FY2025.md -> FY2025）
        period_match = re.search(r"_(\w+)\.md$", md_file)
        if period_match:
            record["period"] = period_match.group(1)
        else:
            # 备用：从标题行提取（如 # AAPL 10-K FY2025）
            title_match = re.search(r"^#\s+\w+\s+10-[KQ]\s+(\S+)", content, re.MULTILINE)
            if title_match:
                record["period"] = title_match.group(1)

        # 提取财务数据。金额兼容 $/¥ 前缀（RMB 报告如 PDD 用 ¥），括号表示负数
        def _money(label, da=False):
            pattern = rf"\|\s*{label}\s*\|\s*(\()?[$¥]?([\d,]+)M?(?:\))?\s*\|"
            m = re.search(pattern, content)
            if not m:
                return None
            val = int(m.group(2).replace(",", ""))
            return -val if m.group(1) else val

        # 营收
        record["revenue"] = _money(r"营收") or 0
        # 净利润
        record["net_income"] = _money(r"净利润")
        # 营业利润
        record["operating_income"] = _money(r"营业利润(?!率)")
        # 自由现金流 / 经营现金流 / 折旧摊销 / 资本支出
        record["fcf"] = _money(r"自由现金流")
        record["cfo"] = _money(r"经营现金流")
        record["depreciation"] = _money(r"折旧/?摊销")
        record["capex"] = _money(r"资本支出")
        # 总资产 / 股东权益
        record["total_assets"] = _money(r"总资产")
        record["equity"] = _money(r"股东权益")
        # 封面流通股数（百万股，非货币金额不能用 _money）
        sh_match = re.search(r"\|\s*流通股数\s*\|\s*([\d,]+(?:\.\d+)?)M\s*\|", content)
        if sh_match:
            record["shares_outstanding"] = float(sh_match.group(1).replace(",", ""))
        # EPS
        eps_match = re.search(r"\|\s*每股收益.*?\|\s*[$¥]?([\d.]+)\s*\|", content)
        if eps_match:
            record["eps"] = float(eps_match.group(1))

        # 币种（RMB 报告如 PDD 用 ¥，报告输出沿用原币种）
        if "¥" in content:
            record["currency"] = "¥"

        if record.get("revenue") or record.get("net_income"):
            history.append(record)

    return history


def read_sec_analysis(ticker):
    """年报记录（10-K/20-F）：DCF 历史数据与 CAGR 估算"""
    return [h for h in _read_sec_records(ticker) if h.get("type") == "10-K"]


def read_sec_quarterly(ticker):
    """季报记录（10-Q/6-K）：TTM 基期构造，消除年报滞后"""
    return [h for h in _read_sec_records(ticker) if h.get("type") == "10-Q"]


# 现金流量表科目（10-Q 披露的是 YTD 累计，TTM 公式与利润表科目不同）
_CF_METRICS = ("cfo", "capex", "depreciation", "fcf")
_TTM_FLOW_METRICS = ("revenue", "net_income", "operating_income") + _CF_METRICS
# YTD 累计披露的季度 → 人读标签（Q1/H1/9M/FY），如 2026Q2 → "H1'26 累计"
_YTD_LABEL = {1: "Q1", 2: "H1", 3: "9M", 4: "FY"}


def _parse_fyq(period):
    """"2026Q2" -> (2026, 2)；其余返回 None"""
    m = re.match(r"^(\d{4})Q([1-4])$", str(period))
    return (int(m.group(1)), int(m.group(2))) if m else None


def compute_ttm(annual, quarterly):
    """由最新年报 + 年后季报构造 TTM（未来十二个月已实现的滚动值）

    TTM = 最新年报 + 年后季度 − 上年同期季度，消除"只用年报"造成的滞后：
      - 利润表科目（10-Q/6-K 均为单季值）: 逐年滚动求和
      - 现金流科目在 10-Q 中为 YTD 累计: TTM = 年报 + 最新YTD − 上年同期YTD
      - 现金流科目在 6-K 中为单季值: 与利润表同公式
    任一成分缺失则该指标不输出 TTM（财务数据不可造假），由调用方回退年报基期。

    返回 {metric: ttm_value, "base_period": "TTM 截至 2026Q3",
          "detail": {metric: 推导明细}}；无年后季报返回 {}。
    detail 每指标含 formula（rolling=单季滚动 / ytd=YTD 累计）、年报值、
    各成分值与缺失原因，供报告"基期 Owner Earnings 明细"逐项展示。
    """
    if not annual:
        return {}
    latest = max(annual, key=lambda r: str(r.get("period", "")))
    m = re.search(r"(\d{4})", str(latest.get("period", "")))
    if not m:
        return {}
    fy = int(m.group(1))

    after, prior = {}, {}  # 年后季度 / 年报自身财年的同期季度
    for r in quarterly:
        fyq = _parse_fyq(r.get("period", ""))
        if not fyq:
            continue
        y, q = fyq
        if y == fy + 1:
            after[q] = r
        elif y == fy:
            prior[q] = r
    if not after:
        return {}

    ttm = {}
    detail = {}  # 每指标推导明细（供报告展示推导过程）
    for metric in _TTM_FLOW_METRICS:
        a_val = latest.get(metric)
        if a_val is None:
            continue
        if metric in _CF_METRICS and all(r.get("form") == "10-Q" for r in after.values()):
            # 10-Q 现金流为 YTD 累计：取期末最新一份与上年同期一份
            q_latest = max(after)
            after_label = f"{_YTD_LABEL[q_latest]}'{(fy + 1) % 100} 累计"
            prior_label = f"{_YTD_LABEL[q_latest]}'{fy % 100} 累计"
            prior_rec = prior.get(q_latest)
            if prior_rec is None or prior_rec.get(metric) is None:
                detail[metric] = {"formula": "ytd", "annual": a_val,
                                  "after_label": after_label,
                                  "after": after[q_latest][metric],
                                  "missing_prior_label": prior_label}
                continue
            ttm[metric] = a_val + after[q_latest][metric] - prior_rec[metric]
            detail[metric] = {"formula": "ytd", "annual": a_val,
                              "after_label": after_label,
                              "after": after[q_latest][metric],
                              "prior_label": prior_label,
                              "prior": prior_rec[metric]}
        else:
            # 单季值滚动求和（现金流科目仅当全部为 6-K 单季披露时适用）
            if metric in _CF_METRICS and not all(r.get("form") == "6-K" for r in after.values()):
                detail[metric] = {"formula": "rolling", "annual": a_val,
                                  "missing_reason":
                                      "现金流科目为 10-Q/6-K 混合披露，单季/YTD 口径不一致，无法滚动"}
                continue
            comps = []
            total = a_val
            missing_q_label = None
            for q, r in sorted(after.items()):
                prior_rec = prior.get(q)
                if prior_rec is None or prior_rec.get(metric) is None:
                    total = None
                    missing_q_label = f"{(fy + 1) % 100}Q{q}"
                    break
                comps.append((f"{(fy + 1) % 100}Q{q}", r[metric], prior_rec[metric]))
                total += r[metric] - prior_rec[metric]
            if total is not None:
                ttm[metric] = total
            detail[metric] = {"formula": "rolling", "annual": a_val,
                              "components": comps,
                              "missing_q_label": missing_q_label}

    if ttm:
        ttm["base_period"] = f"TTM 截至 {fy + 1}Q{max(after)}"
        ttm["detail"] = detail
    return ttm


# ── 数据验证 ────────────────────────────────────────────

def find_stale_analysis(sec_data, quarterly_data):
    """找出旧版生成器产物：头部无版本号（legacy）或版本 ≠ 当前 ANALYSIS_VERSION 的记录。

    供 main() 打印告警与在 DCF 报告头部标记；按用户确认的行为：警告后继续计算，不阻断。
    """
    return [r for r in list(sec_data) + list(quarterly_data)
            if r.get("gen_version") != ANALYSIS_VERSION]


def validate_sec_data(sec_data, quarterly_data=None):
    """
    验证 SEC 提取数据的质量和合理性。

    检查项：
    1. 必填字段是否存在（营收、净利润）
    2. 关键字段是否为 0（折旧、CapEx、现金流）
    3. 数值是否在合理范围内（相对营收的比例）
    4. 交叉验证（FCF ≈ CFO - CapEx）
    5. 季报单季列口径（净利率异常 + 同年 NI 环比异常跳变）

    参数：
      sec_data: 年报记录列表
      quarterly_data: 季报记录列表（可选，用于单季列口径检查）

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

    # ── 6. 季报单季列口径检查 ──
    # 10-Q 收益表同页披露 Three/Six/Nine Months 多组列，取错列会把累计值当单季，
    # TTM/DCF 被显著抬高。以下均为 warning（不阻断）：高净利率也可能来自真实的
    # 大额一次性损益，标记后需人工核对原文单季列。
    for record in list(sec_data) + list(quarterly_data or []):
        revenue = record.get("revenue", 0)
        net_income = record.get("net_income")
        period = record.get("period", "未知")
        if revenue and revenue > 0 and net_income is not None and net_income / revenue > 0.60:
            warnings.append(
                f"[{period}] 净利率 {net_income / revenue * 100:.1f}% — "
                f"疑似利润表取到累计列（或存在大额一次性损益），建议核对 10-Q 单季数据"
            )

    # 同年季报 NI 环比异常跳变：Q(n) < Q(n-1) 但 Q(n+1) > Q(n-1)×1.5，
    # 典型的单季/累计列混取特征
    ni_by_year = {}
    for record in quarterly_data or []:
        fyq = _parse_fyq(record.get("period", ""))
        if fyq and record.get("net_income") is not None:
            ni_by_year.setdefault(fyq[0], []).append((fyq[1], record["net_income"]))
    for year, nis in sorted(ni_by_year.items()):
        nis.sort()
        for (q0, ni0), (q1, ni1), (q2, ni2) in zip(nis, nis[1:], nis[2:]):
            if ni1 < ni0 and ni2 > ni0 * 1.5:
                warnings.append(
                    f"[{year} Q{q0}→Q{q2}] 季报净利润环比异常跳变"
                    f"（Q{q1} {ni1:,} < Q{q0} {ni0:,}，但 Q{q2} {ni2:,} > "
                    f"Q{q0}×1.5）— 疑似利润表取到累计列，建议核对单季数据"
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


def owner_earnings_for_record(net_income, depreciation, capex, fcf):
    """单期 Owner Earnings。

    capex 在提取文件中为负数（现金流出，如 "资本支出 | ($1,192M)"），
    统一取绝对值，避免符号约定导致公式被跳过而静默回退 FCF。

    返回 (owner_earnings, maintenance_capex)；折旧或 capex 缺失时回退 FCF（宁缺勿错）。
    """
    dep = depreciation or 0
    capex_abs = abs(capex or 0)
    if dep > 0 and capex_abs > 0:
        maintenance = capex_abs * 0.6
        return calculate_owner_earnings(net_income, dep, maintenance), maintenance
    return (fcf if fcf and fcf > 0 else 0), 0


def latest_cover_shares(quarterly_data, sec_data):
    """封面流通股数兜底：最新季报封面 > 最新年报封面（百万股）。

    返回 (shares, period)；全部缺失返回 (None, None)。
    """
    for rec in list(reversed(quarterly_data)) + list(reversed(sec_data)):
        if rec.get("shares_outstanding"):
            return rec["shares_outstanding"], rec.get("period")
    return None, None


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
    growth_rates, discount_rates, terminal_growth_rate, projection_years,
    currency="$",
):
    """敏感性分析：不同增长率 × 折现率下的内在价值

    有流通股数时输出每股内在价值；无股数（如无行情）时输出企业内在价值（M），
    不除股数——不得输出 0（财务数据不可造假）。
    """
    has_shares = shares_outstanding and shares_outstanding > 0
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
            elif has_shares:
                val = result.get("intrinsic_value_per_share", 0)
                row[f"r_{r*100:.0f}%"] = f"{currency}{val:.0f}"
            else:
                val = result.get("intrinsic_value", 0)
                row[f"r_{r*100:.0f}%"] = f"{currency}{val:,.0f}M"
        table.append(row)
    return table


# ── 报告生成 ────────────────────────────────────────────

# 行情源标识 → 报告展示用名称
_QUOTE_SOURCE_LABEL = {"futu": "Futu 行情", "tencent": "腾讯行情"}


def _fmt_m(v):
    """千分位整数（百万单位，负数保留符号）"""
    return f"{v:,.0f}"


def _ttm_derive_text(metric, info):
    """compute_ttm 的单指标推导明细 → 人读推导文本（与实际使用的公式一致）。

    capex 在提取文件中为负数（现金流出），展示时取绝对值。
    """
    if info is None:
        return "最新年报缺该科目，无法构造 TTM"
    if "missing_reason" in info:
        return info["missing_reason"]
    show = abs if metric == "capex" else (lambda x: x)
    if info["formula"] == "ytd":
        head = (f"YTD 公式：年报 {_fmt_m(show(info['annual']))} "
                f"+ {info['after_label']} {_fmt_m(show(info['after']))}")
        if "missing_prior_label" in info:
            return f"{head}，上年同期（{info['missing_prior_label']}）缺失，无法计算"
        return (f"{head} − {info['prior_label']} {_fmt_m(show(info['prior']))}")
    # rolling：年报 + Σ(年后单季 − 上年同期单季)
    parts = [f"年报 {_fmt_m(show(info['annual']))}"]
    parts += [f"{label} ({_fmt_m(a)}−{_fmt_m(p)})"
              for label, a, p in info.get("components", [])]
    text = " + ".join(parts)
    if info.get("missing_q_label"):
        text += f"，{info['missing_q_label']} 上年同期单季缺失，无法滚动"
    return text


def generate_report(ticker, dcf_result, sensitivity, owner_earnings_data, quote, params, data_source="SEC", warnings=None, stale_analysis=False, base_ttm=None):
    """生成 DCF 估值报告 Markdown"""
    lines = []

    symbol = ticker.split(".")[-1] if "." in ticker else ticker
    company_name = quote.get("name", symbol) if quote else symbol
    cur = params.get("currency", "$")
    base_note = params.get("base_period", "最近一年的 Owner Earnings")

    lines.append(f"# {company_name} ({ticker}) DCF 估值分析")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    # 生成器版本号：入库时解析该行写 generator_version 列，无该行视为 legacy 旧数据
    lines.append(f"生成器版本: dcf-{DCF_VERSION}")
    lines.append(f"估值方法: 巴菲特 Owner Earnings 折现模型")
    lines.append(f"数据来源: {data_source}")
    lines.append("")
    if stale_analysis:
        lines.append("> ⚠️ 本报告基于旧版分析数据（legacy），结果可能不可靠，建议先重新生成财报分析")
        lines.append("")

    # ── 基本信息（4 列：项目|数值 两两并排，减少纵向长度）──
    # 展示参与计算的全部输入：股价/行情来源、市值、流通股数+来源、数据截止期，
    # 便于发现"结果对不上"背后的数据错误
    lines.append("## 基本信息")
    lines.append("")
    lines.append("| 项目 | 数值 | 项目 | 数值 |")
    lines.append("|:---|:---|:---|:---|")

    def _cell(v):
        return v if v not in (None, "") else "-"

    if quote:
        price_cell = f"${quote.get('price', 0):.2f}"
        mc = quote.get("marketCap", 0)
        mc_cell = f"${mc/1e9:.1f}B" if mc else "-"
        src = quote.get("source", "")
        src_cell = _QUOTE_SOURCE_LABEL.get(src, src)
    else:
        price_cell, mc_cell, src_cell = "-", "-", "-"

    shares_cell = ""
    shares_m = params.get("shares_millions")
    if shares_m:
        shares_note = params.get("shares_note", "")
        # 来源标注用 " · " 分隔，避免与 "SEC 封面（2026Q2）" 中的括号嵌套
        shares_cell = f"{shares_m:,.1f}M" + (f" · {shares_note}" if shares_note else "")
    elif quote:
        # params 未携带时按行情兜底展示（兼容外部直接调用）
        so = quote.get("sharesOutstanding", 0) or 0
        if so:
            src = quote.get("source", "")
            shares_cell = f"{so/1e6:,.1f}M" + (f" · {_QUOTE_SOURCE_LABEL.get(src, src)}" if src else "")
        elif quote.get("marketCap") and quote.get("price"):
            derived_so = quote["marketCap"] / quote["price"] / 1e6
            shares_cell = f"{derived_so:,.1f}M（推算）"

    # 数据截止期：TTM 基期取 TTM 期末，否则最新年报期
    basis = params.get("base_oe_basis", "annual")
    if basis in ("ttm", "ttm_fcf") and base_ttm and base_ttm.get("base_period"):
        data_cutoff = base_ttm["base_period"]
    elif owner_earnings_data:
        data_cutoff = f"最新年报 {owner_earnings_data[-1].get('year', '')}".strip()
    else:
        data_cutoff = base_note

    lines.append(f"| 公司名称 | {_cell(company_name)} | 当前股价 | {_cell(price_cell)} |")
    lines.append(f"| 行情来源 | {_cell(src_cell)} | 市值 | {_cell(mc_cell)} |")
    lines.append(f"| 流通股数 | {_cell(shares_cell)} | 数据截止期 | {_cell(data_cutoff)} |")
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
            ni = f"{cur}{d['net_income']:,.0f}M" if d.get("net_income") is not None else "-"
            dep = f"{cur}{d['depreciation']:,.0f}M" if d.get("depreciation") is not None else "-"
            mcapex = f"{cur}{d['maintenance_capex']:,.0f}M" if d.get("maintenance_capex") is not None else "-"
            oe = f"{cur}{d['owner_earnings']:,.0f}M" if d.get("owner_earnings") is not None else "-"
            rev = f"{cur}{d['revenue']:,.0f}M" if d.get("revenue") is not None else "-"
            fcf = f"{cur}{d['fcf']:,.0f}M" if d.get("fcf") is not None else "-"
            lines.append(f"| {d.get('year', '-')} | {ni} | {dep} | {mcapex} | {oe} | {rev} | {fcf} |")
        lines.append("")
    else:
        lines.append("*未能获取历史 Owner Earnings 数据*")
        lines.append("")

    # ── 基期 Owner Earnings 明细（逐项推导，便于发现数据错误）──
    if owner_earnings_data:
        lines.append("## 基期 Owner Earnings 明细")
        lines.append("")
        ttm_detail = base_ttm.get("detail") if base_ttm else None
        if basis in ("ttm", "ttm_fcf") and ttm_detail is not None:
            lines.append(f"> 单位：百万{cur}；{base_ttm.get('base_period', 'TTM')}。"
                         "利润表科目按单季逐年滚动，现金流科目按 YTD 累计公式推导。")
        else:
            lines.append(f"> 单位：百万{cur}。未启用 TTM（成分缺失），回退最新年报。")
        lines.append("")
        lines.append("| 成分 | 数值 | 推导 |")
        lines.append("|:---|:---:|:---|")
        rows = []
        if basis in ("ttm", "ttm_fcf") and ttm_detail is not None:
            for label, metric, is_outflow in (("净利润（TTM）", "net_income", False),
                                              ("折旧摊销（TTM）", "depreciation", False),
                                              ("CapEx（TTM）", "capex", True)):
                val = base_ttm.get(metric)
                deriv = _ttm_derive_text(metric, ttm_detail.get(metric))
                if val is None:
                    rows.append((label, "-", deriv))
                else:
                    shown = abs(val) if is_outflow else val
                    rows.append((label, _fmt_m(shown), deriv))
            if basis == "ttm":
                maint = abs(base_ttm.get("capex") or 0) * 0.6
                rows.append(("维护性 CapEx（×0.6）", _fmt_m(maint), "CapEx × 0.6"))
                rows.append(("**基期 Owner Earnings**", f"**{_fmt_m(dcf_result['base_earnings'])}**",
                             "净利润 + 折旧摊销 − 维护性 CapEx"))
            else:
                rows.append(("维护性 CapEx（×0.6）", "-", "折旧摊销或 CapEx 的 TTM 缺失"))
                rows.append(("**基期 Owner Earnings**", f"**{_fmt_m(dcf_result['base_earnings'])}**",
                             "TTM 自由现金流（FCF 口径）"))
        else:
            latest_annual = owner_earnings_data[-1]
            lp = latest_annual.get("year", "?")
            for label, key in (("净利润", "net_income"),
                               ("折旧摊销", "depreciation"),
                               ("CapEx", "capex")):
                val = latest_annual.get(key)
                if val:
                    rows.append((f"{label}（{lp}）", _fmt_m(abs(val)), f"最新年报 {lp}"))
                else:
                    rows.append((f"{label}（{lp}）", "-", "最新年报缺该科目"))
            mcapex = latest_annual.get("maintenance_capex")
            if mcapex:
                rows.append(("维护性 CapEx（×0.6）", _fmt_m(mcapex), "CapEx × 0.6"))
                rows.append(("**基期 Owner Earnings**", f"**{_fmt_m(dcf_result['base_earnings'])}**",
                             "净利润 + 折旧摊销 − 维护性 CapEx"))
            else:
                rows.append(("维护性 CapEx（×0.6）", "-", "折旧摊销或 CapEx 缺失"))
                rows.append(("**基期 Owner Earnings**", f"**{_fmt_m(dcf_result['base_earnings'])}**",
                             "最新年报自由现金流（FCF 口径）"))
        for label, val, deriv in rows:
            lines.append(f"| {label} | {val} | {deriv} |")
        lines.append("")

    # ── DCF 计算参数（4 列：两两并排，减少纵向长度）──
    lines.append("## DCF 计算参数")
    lines.append("")
    lines.append("| 项目 | 数值 | 项目 | 数值 |")
    lines.append("|:---|:---:|:---|:---:|")
    lines.append(f"| 基期 Owner Earnings | {cur}{dcf_result['base_earnings']:,.0f}M "
                 f"| 预测增长率 | {dcf_result['growth_rate']*100:.1f}% |")
    lines.append(f"| 折现率 (WACC) | {dcf_result['discount_rate']*100:.1f}% "
                 f"| 永续增长率 | {dcf_result['terminal_growth']*100:.1f}% |")
    lines.append(f"| 预测年数 | {dcf_result['projection_years']}年 | 基期口径 | {base_note} |")
    lines.append("")

    # ── 预测现金流 ──
    lines.append(f"## 预测现金流（百万{cur}）")
    lines.append("")
    if dcf_result.get("projections"):
        lines.append("| 年份 | 预测 Owner Earnings | 折现值 | 累计折现值 |")
        lines.append("|:---:|:---:|:---:|:---:|")
        for p in dcf_result["projections"]:
            lines.append(
                f"| 第{p['year']}年 "
                f"| {cur}{p['projected_earnings']:,.0f} "
                f"| {cur}{p['present_value']:,.0f} "
                f"| {cur}{p['cumulative_pv']:,.0f} |"
            )
        lines.append("")

    # ── 估值结果 ──
    lines.append("## 估值结果")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|:---|")
    lines.append(f"| 预测期现金流现值合计 | {cur}{dcf_result.get('sum_pv_projections', 0):,.0f}M |")
    lines.append(f"| 终值 | {cur}{dcf_result.get('terminal_value', 0):,.0f}M |")
    lines.append(f"| 终值折现 | {cur}{dcf_result.get('terminal_pv', 0):,.0f}M |")
    lines.append(f"| **股权内在价值** | **{cur}{dcf_result.get('intrinsic_value', 0):,.0f}M** |")
    lines.append("")
    lines.append("> Owner Earnings 基于净利润（已扣除息费用）折现，结果为股权价值近似口径")
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
    if dcf_result.get("shares_outstanding", 0) and dcf_result["shares_outstanding"] > 0:
        lines.append("不同增长率 × 折现率下的每股内在价值：")
    else:
        lines.append("不同增长率 × 折现率下的股权内在价值（无流通股数，不除股数）：")
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
    if not dcf_result.get("current_price"):
        checks.append(("⚠️", "估值", "未获取市价，无法与每股内在价值对比"))
    elif dcf_result.get("verdict") in ["严重低估", "低估"]:
        checks.append(("✅", "估值", "内在价值高于当前股价"))
    elif dcf_result.get("verdict") == "合理估值":
        checks.append(("✅", "估值", "内在价值与当前股价基本匹配"))
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

    # ── 1. 获取实时行情（统一接口：Futu → 腾讯财经 fallback）──
    print("📊 获取实时行情...")
    quote = QuoteSource().get_snapshot(ticker)
    if quote:
        print(f"  数据源: {quote.get('source', '?')}")
        print(f"  股价: ${quote.get('price', 0):.2f}")
        if quote.get("marketCap"):
            print(f"  市值: ${quote['marketCap']/1e9:.1f}B")
    else:
        print("  ⚠️  无法获取行情数据（无现价，仅输出内在价值）")

    # ── 2. 从 SEC 分析数据读取历史数据 ──
    print("\n📁 读取 SEC 分析数据...")
    sec_data = read_sec_analysis(ticker)
    # 货币符号：任一期报存款货币为 ¥（外国发行人 20-F）则全程用 ¥ 展示
    cur = "¥" if any(d.get("currency") == "¥" for d in sec_data) else "$"
    if sec_data:
        print(f"  找到 {len(sec_data)} 期年报数据")
        for d in sec_data:
            print(f"    {d.get('period', '?')}: 营收={cur}{d.get('revenue', 0):,}M, 净利润={cur}{d.get('net_income', 0):,}M")
    else:
        print("  ⚠️  未找到 SEC 分析数据")

    # ── 2.5 数据验证 ──
    print("\n🔍 验证数据质量...")
    quarterly_data = read_sec_quarterly(ticker)
    # 生成器版本校验：旧版/legacy 分析数据参与计算前先告警（警告后继续，不阻断）
    stale = find_stale_analysis(sec_data, quarterly_data)
    if stale:
        print("\n⚠️  部分分析数据为旧版生成器产物（legacy/旧版本号），建议重新生成财报分析后再估值")
        for r in stale:
            v = r.get("gen_version")
            why = "legacy 无版本号" if not v else f"版本 {v} ≠ 当前 {ANALYSIS_VERSION}"
            print(f"    - {r.get('source', '?')}（{why}）")
    errors, warnings = validate_sec_data(sec_data, quarterly_data)
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
        cfo = d.get("cfo", 0)

        # 计算 Owner Earnings（capex 为负数流出表示，函数内统一取绝对值）
        owner_earnings, maintenance_capex = owner_earnings_for_record(
            ni, d.get("depreciation"), d.get("capex"), d.get("fcf"))
        depreciation = d.get("depreciation") or 0
        capex = d.get("capex") or 0
        fcf = d.get("fcf") or 0

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
            print(f"    {d.get('year', '?')}: Owner Earnings = {cur}{d.get('owner_earnings', 0):,.0f}M")
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
    # 基期优先用 TTM（最新年报 + 年后季报滚动），年报滞后最多 3 个季度；
    # TTM 成分缺失时回退最新年报（base_oe_basis 供报告标注基期口径）
    if quarterly_data:
        print(f"\n📅 读取 {len(quarterly_data)} 期季报数据（用于 TTM 基期）")
    base_oe = owner_earnings_data[-1]["owner_earnings"]
    base_oe_note = "最新年报的 Owner Earnings"
    base_oe_basis = "annual"
    ttm = compute_ttm(sec_data, quarterly_data)
    if ttm.get("net_income") is not None:
        # capex TTM 为负数流出表示，取绝对值
        t_dep = ttm.get("depreciation") or 0
        t_capex = abs(ttm.get("capex") or 0)
        if t_dep > 0 and t_capex > 0:
            base_oe = calculate_owner_earnings(ttm["net_income"], t_dep, t_capex * 0.6)
            base_oe_note = f"{ttm['base_period']}（年报+季报滚动）"
            base_oe_basis = "ttm"
        elif (ttm.get("fcf") or 0) > 0:
            base_oe = ttm["fcf"]
            base_oe_note = f"{ttm['base_period']}（FCF 口径）"
            base_oe_basis = "ttm_fcf"

    # 流通股数：Futu/腾讯行情优先，无行情时用 SEC 财报封面股数兜底
    # （最新季报封面 > 最新年报封面）；shares_note 为报告"基本信息"的来源标注
    shares_outstanding = 0
    shares_note = ""
    current_price = 0
    if quote:
        current_price = quote.get("price", 0)
        shares_outstanding = (quote.get("sharesOutstanding", 0) or 0) / 1e6
        if shares_outstanding:
            src = quote.get("source", "")
            shares_note = _QUOTE_SOURCE_LABEL.get(src, src)

    if shares_outstanding == 0 and quote and quote.get("marketCap") and current_price > 0:
        shares_outstanding = quote["marketCap"] / current_price / 1e6
        shares_note = "市值/股价推算"

    if shares_outstanding == 0:
        cover_shares, cover_period = latest_cover_shares(quarterly_data, sec_data)
        if cover_shares:
            shares_outstanding = cover_shares
            shares_note = f"SEC 封面（{cover_period}）"
            print(f"  ℹ️  流通股数来自 SEC 封面（{cover_period}）: {cover_shares:,.1f}M")

    # PDD 等 ¥ 报表公司：美股 ADS 报价为美元，每股价值未做汇率换算
    if cur == "¥" and shares_outstanding > 0:
        fx_warn = ("财报为人民币（¥）口径，美股 ADS 报价为美元（$）；"
                   "每股内在价值为 ¥/ADS 口径，未做汇率换算，与市价直接比较前需自行换算")
        print(f"  ⚠️  {fx_warn}")
        warnings.append(fx_warn)

    print(f"\n🔮 DCF 估值计算...")
    print(f"  基期 Owner Earnings: {cur}{base_oe:,.0f}M")
    print(f"  基期口径: {base_oe_note}")
    print(f"  增长率: {growth_rate*100:.1f}%")
    print(f"  折现率: {discount_rate*100:.1f}%")
    print(f"  永续增长率: {terminal_growth*100:.1f}%")
    print(f"  预测年数: {args.years}年")
    if shares_outstanding > 0:
        print(f"  流通股数: {shares_outstanding:,.1f}M")

    dcf_result = dcf_valuation(
        base_oe, growth_rate, discount_rate, terminal_growth,
        args.years, shares_outstanding, current_price
    )

    if "error" in dcf_result:
        print(f"\n❌ 估值失败: {dcf_result['error']}")
        sys.exit(1)

    print(f"\n📊 估值结果:")
    print(f"  股权内在价值: {cur}{dcf_result['intrinsic_value']:,.0f}M")
    if dcf_result.get("intrinsic_value_per_share"):
        print(f"  每股内在价值: {cur}{dcf_result['intrinsic_value_per_share']:.2f}")
        if current_price > 0:
            print(f"  当前股价: ${current_price:.2f}")
            print(f"  上行空间: {dcf_result.get('upside_pct', 0):.1f}%")
            print(f"  判断: {dcf_result.get('verdict', '-')}")
        else:
            print(f"  （无现价：合理股价已算出，但无法与市价比较给出判断）")

    # ── 7. 敏感性分析 ──
    print(f"\n📊 敏感性分析...")
    growth_rates = [max(growth_rate - 0.03, 0.00), growth_rate - 0.01, growth_rate,
                    growth_rate + 0.01, growth_rate + 0.03]
    growth_rates = sorted(set(round(g, 4) for g in growth_rates))
    discount_rates = [0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.11, 0.12]

    sensitivity = sensitivity_analysis(
        base_oe, shares_outstanding, current_price,
        growth_rates, discount_rates, terminal_growth, args.years,
        currency=cur
    )

    # ── 8. 生成报告 ──
    report = generate_report(ticker, dcf_result, sensitivity, owner_earnings_data, quote,
                             {"growth": growth_rate, "discount": discount_rate,
                              "terminal_growth": terminal_growth, "years": args.years,
                              "currency": cur, "base_period": base_oe_note,
                              "base_oe_basis": base_oe_basis,
                              "shares_millions": shares_outstanding,
                              "shares_note": shares_note},
                             data_source="SEC", warnings=warnings,
                             stale_analysis=bool(stale), base_ttm=ttm)

    # 保存报告（文件名带秒级时间戳：每次估值落一份独立文件，重跑不覆盖历史；
    # webapp 的 register_dcf 按 local_path 去重，时间戳保证每次估值在库里新建一条记录）
    output_dir = os.path.join(REPORTS_DIR, "dcf")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(
        output_dir, f"{ticker}_DCF_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n✅ DCF 估值报告已保存: {output_path}")
    return output_path


if __name__ == "__main__":
    main()
