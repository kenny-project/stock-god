"""
SEC 财报数据提取模块
从纯文本中提取财务指标、现金流等数据
这些逻辑在 10-K 和 10-Q 之间是共用的
"""

import re
from typing import Dict, Any, List

# 必填字段定义（在报告中标记缺失项）
REQUIRED_METRIC_FIELDS = [
    ("revenue", "营收"),
    ("net_income", "净利润"),
    ("total_assets", "总资产"),
    ("total_liabilities", "总负债"),
]

REQUIRED_CASHFLOW_FIELDS = [
    ("operating", "经营现金流"),
    ("capex", "资本支出"),
    ("free_cash_flow", "自由现金流"),
]


def extract_key_metrics(text: str) -> Dict[str, Any]:
    """从文本中提取关键财务指标"""
    metrics = {}

    # 清理 HTML 实体
    clean_text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    clean_text = re.sub(r"\s+", " ", clean_text)

    # 营收模式 - 合并利润表中的 Revenues（取所有匹配中的最大值，避免分部数据干扰）
    # 注意：正则必须确保第一个字符是数字，避免匹配到 "Total revenues ," 这种文本
    revenue_patterns = [
        r"Total\s+revenues?\s+[$]?\s*(\d[\d,]*)",
        r"(?:total\s+)?(?:net\s+)?revenues?\s*[:\$]\s*(\d[\d,]*)",
        r"Revenues?\s+[$]\s*(\d[\d,]*)",
    ]

    # 净利润模式 - Net income $ 3,004
    income_patterns = [
        r"Net\s+income\s+[$]?\s*(\d[\d,]*)",
        r"net\s+(?:income|loss)\s*[:\$]\s*(\d[\d,]*)",
    ]

    # EPS 模式 - Basic earnings per share $ 2.81
    eps_patterns = [
        r"(?:Basic|Diluted)\s+earnings\s*(?:\(loss\))?\s*per\s+share.*?[$]\s*(\d[\d.]*)",
        r"Earnings\s+per\s+common\s+share.*?[$]\s*(\d[\d.]*)",
        r"earnings\s+per\s+share\s*[:][$]\s*(\d[\d.]*)",
    ]

    # 营收（取所有匹配中的最大值，避免分部数据干扰）
    max_revenue = 0
    for pattern in revenue_patterns:
        for match in re.finditer(pattern, clean_text, re.IGNORECASE):
            try:
                value = int(match.group(1).replace(",", ""))
                if value > max_revenue:
                    max_revenue = value
            except (ValueError, OverflowError) as e:
                # 显示匹配的完整上下文以便调试
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  营收数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue
    if max_revenue > 0:
        metrics["revenue"] = f"${max_revenue:,}M"
        metrics["revenue_num"] = max_revenue

    # 净利润
    for pattern in income_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["net_income"] = f"${int(value):,}M"
                metrics["net_income_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  净利润数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # EPS
    for pattern in eps_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            metrics["eps"] = f"${match.group(1)}"
            break

    # Cost of revenues (用于计算毛利润)
    cost_patterns = [
        r"Cost\s+of\s+revenues?\s+[$]?\s*(\d[\d,]*)",
        r"Cost\s+of\s+sales\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in cost_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["cost_of_revenue"] = f"${int(value):,}M"
                metrics["cost_of_revenue_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  营业成本数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 毛利润 = 营收 - Cost of revenues
    if "revenue_num" in metrics and "cost_of_revenue_num" in metrics:
        gross_profit = metrics["revenue_num"] - metrics["cost_of_revenue_num"]
        metrics["gross_profit"] = f"${gross_profit:,}M"
        metrics["gross_profit_num"] = gross_profit
        # 毛利率
        if metrics["revenue_num"] > 0:
            metrics["gross_margin"] = f"{gross_profit / metrics['revenue_num'] * 100:.1f}%"

    # 营业利润 (Operating income)
    operating_patterns = [
        r"Operating\s+income\s+[$]?\s*(\d[\d,]*)",
        r"Operating\s+income\s+(\d[\d,]*)",
        r"Income\s+before\s+income\s+taxes\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in operating_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["operating_income"] = f"${int(value):,}M"
                metrics["operating_income_num"] = int(value)
                # 营业利润率
                if "revenue_num" in metrics and metrics["revenue_num"] > 0:
                    metrics["operating_margin"] = f"{int(value) / metrics['revenue_num'] * 100:.1f}%"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  营业利润数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 净利率
    if "net_income_num" in metrics and "revenue_num" in metrics and metrics["revenue_num"] > 0:
        metrics["net_margin"] = f"{metrics['net_income_num'] / metrics['revenue_num'] * 100:.1f}%"

    # 总资产 (Total assets)
    total_assets_patterns = [
        r"Total\s+assets\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in total_assets_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["total_assets"] = f"${int(value):,}M"
                metrics["total_assets_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  总资产数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 总负债 (Total liabilities)
    total_liabilities_patterns = [
        r"Total\s+liabilities\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in total_liabilities_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["total_liabilities"] = f"${int(value):,}M"
                metrics["total_liabilities_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  总负债数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 股东权益 - 优先从文本提取，fallback 用 总资产 - 总负债
    stockholders_equity_patterns = [
        r"Total\s+stockholders(?:'|')?s?\s+equity\s+[$]?\s*(\d[\d,]*)",
        r"Total\s+shareholders(?:'|')?s?\s+equity\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in stockholders_equity_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["stockholders_equity"] = f"${int(value):,}M"
                metrics["stockholders_equity_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                continue

    # Fallback: 股东权益 = 总资产 - 总负债
    if "stockholders_equity_num" not in metrics and "total_assets_num" in metrics and "total_liabilities_num" in metrics:
        equity = metrics["total_assets_num"] - metrics["total_liabilities_num"]
        metrics["stockholders_equity"] = f"${equity:,}M"
        metrics["stockholders_equity_num"] = equity

    # Fallback: 总负债 = 总资产 - 股东权益（部分公司没有单独 Total liabilities 行）
    if "total_liabilities_num" not in metrics and "total_assets_num" in metrics and "stockholders_equity_num" in metrics:
        liabilities = metrics["total_assets_num"] - metrics["stockholders_equity_num"]
        metrics["total_liabilities"] = f"${liabilities:,}M"
        metrics["total_liabilities_num"] = liabilities

    # ====== 新增字段：用于计算财务健康指标 ======

    # 流动资产 (Current assets)
    current_assets_patterns = [
        r"Total\s+current\s+assets\s+[$]?\s*(\d[\d,]*)",
        r"Current\s+assets\s*[:\$]\s*(\d[\d,]*)",
    ]
    for pattern in current_assets_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["current_assets"] = f"${int(value):,}M"
                metrics["current_assets_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  流动资产数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 流动负债 (Current liabilities)
    current_liabilities_patterns = [
        r"Total\s+current\s+liabilities\s+[$]?\s*(\d[\d,]*)",
        r"Current\s+liabilities\s*[:\$]\s*(\d[\d,]*)",
    ]
    for pattern in current_liabilities_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["current_liabilities"] = f"${int(value):,}M"
                metrics["current_liabilities_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  流动负债数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 存货 (Inventories)
    inventories_patterns = [
        r"Inventories\s+[$]?\s*(\d[\d,]*)",
        r"Inventory\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in inventories_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["inventories"] = f"${int(value):,}M"
                metrics["inventories_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  存货数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 现金及等价物 (Cash and cash equivalents)
    cash_patterns = [
        r"Cash\s+and\s+cash\s+equivalents\s+[$]?\s*(\d[\d,]*)",
        r"Cash\s+and\s+equivalents\s+[$]?\s*(\d[\d,]*)",
        r"Cash\s+equivalents\s+[$]?\s*(\d[\d,]*)",
    ]
    for pattern in cash_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["cash_equivalents"] = f"${int(value):,}M"
                metrics["cash_equivalents_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  现金及等价物数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 利息费用 (Interest expense)
    interest_patterns = [
        r"Interest\s+expense\s+[$]?\s*(\d[\d,]*)",
        r"Interest\s+\(income\)\s+expense[^($]*\(\s*(\d[\d,]*)\s*\)",
        r"Interest\s+(?:expense|cost)\s*[:\$]\s*(\d[\d,]*)",
    ]
    for pattern in interest_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["interest_expense"] = f"${int(value):,}M"
                metrics["interest_expense_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  利息费用数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 商誉 (Goodwill)
    goodwill_patterns = [
        r"Goodwill\s+[$]?\s*(\d[\d,]*)",
        r"Goodwill\s*[:\$]\s*(\d[\d,]*)",
    ]
    for pattern in goodwill_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                value = match.group(1).replace(",", "")
                metrics["goodwill"] = f"${int(value):,}M"
                metrics["goodwill_num"] = int(value)
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  商誉数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # ====== 计算财务健康指标 ======

    # 资产负债率 = 总负债 / 总资产 × 100%
    if "total_liabilities_num" in metrics and "total_assets_num" in metrics and metrics["total_assets_num"] > 0:
        debt_ratio = metrics["total_liabilities_num"] / metrics["total_assets_num"] * 100
        metrics["debt_to_asset_ratio"] = f"{debt_ratio:.1f}%"
        metrics["debt_to_asset_ratio_num"] = debt_ratio

    # 流动比率 = 流动资产 / 流动负债
    if "current_assets_num" in metrics and "current_liabilities_num" in metrics and metrics["current_liabilities_num"] > 0:
        current_ratio = metrics["current_assets_num"] / metrics["current_liabilities_num"]
        metrics["current_ratio"] = f"{current_ratio:.2f}"
        metrics["current_ratio_num"] = current_ratio

    # 速动比率 = (流动资产 - 存货) / 流动负债
    if "current_assets_num" in metrics and "inventories_num" in metrics and "current_liabilities_num" in metrics and metrics["current_liabilities_num"] > 0:
        quick_ratio = (metrics["current_assets_num"] - metrics["inventories_num"]) / metrics["current_liabilities_num"]
        metrics["quick_ratio"] = f"{quick_ratio:.2f}"
        metrics["quick_ratio_num"] = quick_ratio

    # 净现金 = 现金及等价物 - 总负债
    if "cash_equivalents_num" in metrics and "total_liabilities_num" in metrics:
        net_cash = metrics["cash_equivalents_num"] - metrics["total_liabilities_num"]
        if net_cash >= 0:
            metrics["net_cash"] = f"${net_cash:,}M"
        else:
            metrics["net_cash"] = f"(${abs(net_cash):,}M)"
        metrics["net_cash_num"] = net_cash

    # 利息覆盖倍数 = 营业利润 / 利息费用
    if "operating_income_num" in metrics and "interest_expense_num" in metrics and metrics["interest_expense_num"] > 0:
        interest_coverage = metrics["operating_income_num"] / metrics["interest_expense_num"]
        metrics["interest_coverage"] = f"{interest_coverage:.1f}x"
        metrics["interest_coverage_num"] = interest_coverage

    # 商誉占比 = 商誉 / 总资产 × 100%
    if "goodwill_num" in metrics and "total_assets_num" in metrics and metrics["total_assets_num"] > 0:
        goodwill_ratio = metrics["goodwill_num"] / metrics["total_assets_num"] * 100
        metrics["goodwill_to_assets"] = f"{goodwill_ratio:.1f}%"
        metrics["goodwill_to_assets_num"] = goodwill_ratio

    # ROE = 净利润 / 股东权益 × 100%
    if "net_income_num" in metrics and "stockholders_equity_num" in metrics and metrics["stockholders_equity_num"] > 0:
        roe = metrics["net_income_num"] / metrics["stockholders_equity_num"] * 100
        metrics["roe"] = f"{roe:.1f}%"
        metrics["roe_num"] = roe

    # 主营利润相关字段（需要从业务分部信息提取，暂时使用总数据近似）
    # TODO: 需要大模型从业务分部信息中提取主营收入和主营成本
    # 目前使用营收和成本作为近似
    if "revenue_num" in metrics:
        metrics["primary_revenue"] = metrics.get("revenue")
        metrics["primary_revenue_num"] = metrics.get("revenue_num")
    if "cost_of_revenue_num" in metrics:
        metrics["primary_cost"] = metrics.get("cost_of_revenue")
        metrics["primary_cost_num"] = metrics.get("cost_of_revenue_num")
    if "primary_revenue_num" in metrics and "primary_cost_num" in metrics:
        primary_profit = metrics["primary_revenue_num"] - metrics["primary_cost_num"]
        metrics["primary_profit"] = f"${primary_profit:,}M"
        metrics["primary_profit_num"] = primary_profit
        if metrics["primary_revenue_num"] > 0:
            metrics["primary_margin"] = f"{primary_profit / metrics['primary_revenue_num'] * 100:.1f}%"

    return metrics


def extract_cash_flows(text: str) -> Dict[str, Any]:
    """从文本中提取现金流数据（经营/投资/筹资）"""
    cash_flows = {}

    # 清理 HTML 实体和标签
    clean_text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    clean_text = re.sub(r"<[^>]+>", " ", clean_text)  # 去除 HTML 标签
    clean_text = re.sub(r"\s+", " ", clean_text)

    # 方法1: 从 XBRL 标签提取（优先）
    xbrl_patterns = {
        "operating": r'name="us-gaap:NetCashProvidedByUsedInOperatingActivities"[^>]*>([\d,]+)<',
        "investing": r'name="us-gaap:NetCashProvidedByUsedInInvestingActivities"[^>]*>([\d,]+)<',
        "financing": r'name="us-gaap:NetCashProvidedByUsedInFinancingActivities"[^>]*>([\d,]+)<',
        "capex": r'name="us-gaap:PaymentsToAcquire(?:PropertyPlantAndEquipment|ProductiveAssets)"[^>]*>([\d,]+)<',
        "depreciation_amortization": r'name="us-gaap:Depreciation(?:AndAmortizationAndAccretionNet|Net|)"[^>]*>([\d,]+)<',
    }

    for key, pattern in xbrl_patterns.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            try:
                # 取第一个匹配（最新一期）
                value = int(matches[0].replace(",", ""))
                # 检查原文中该数值是否在括号内（括号表示负数）
                is_negative = False
                for m in re.finditer(re.escape(matches[0]), text):
                    start = max(0, m.start() - 5)
                    end = min(len(text), m.end() + 5)
                    context = text[start:end]
                    if context.startswith("(") or "(" in text[max(0, m.start()-3):m.start()+1]:
                        is_negative = True
                        break
                if key == "capex":
                    cash_flows["capex"] = f"(${value:,}M)"
                    cash_flows["capex_num"] = -value  # 负数表示现金流出
                elif key == "depreciation_amortization":
                    cash_flows[key] = f"${value:,}M"
                    cash_flows[f"{key}_num"] = value
                else:
                    if is_negative:
                        cash_flows[key] = f"(${value:,}M)"
                        cash_flows[f"{key}_num"] = -value
                    else:
                        cash_flows[key] = f"${value:,}M"
                        cash_flows[f"{key}_num"] = value
            except (ValueError, OverflowError) as e:
                print(f"   ⚠️  XBRL {key} 数值解析失败: '{matches[0]}' | {e}")
                continue

    # 方法2: 从纯文本提取（如果 XBRL 没有匹配到）
    if "operating" not in cash_flows:
        op_patterns = [
            (r"Net\s+cash\s+provided\s+by\s+operating\s+activities\s*[\$]?\s*(\d[\d,]*)", False),
            (r"Net\s+cash\s+used\s+(?:by|in)\s+operating\s+activities\s*[\$]?\s*\((\d[\d,]*)\)", True),
        ]
        for pattern, is_negative in op_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                try:
                    value = match.group(1).replace(",", "")
                    if is_negative:
                        cash_flows["operating"] = f"(${int(value):,}M)"
                    else:
                        cash_flows["operating"] = f"${int(value):,}M"
                    break
                except (ValueError, OverflowError) as e:
                    start = max(0, match.start() - 20)
                    end = min(len(clean_text), match.end() + 20)
                    context = clean_text[start:end]
                    print(f"   ⚠️  经营现金流数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                    continue

    if "investing" not in cash_flows:
        inv_patterns = [
            (r"Net\s+cash\s+(?:used|provided)\s+(?:by|in)\s+investing\s+activities\s*[\$]?\s*\((\d[\d,]*)\)", True),
            (r"Net\s+cash\s+(?:used|provided)\s+(?:by|in)\s+investing\s+activities\s*[\$]?\s*(\d[\d,]*)", False),
        ]
        for pattern, is_negative in inv_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                try:
                    value = match.group(1).replace(",", "")
                    if is_negative:
                        cash_flows["investing"] = f"(${int(value):,}M)"
                    else:
                        cash_flows["investing"] = f"${int(value):,}M"
                    break
                except (ValueError, OverflowError) as e:
                    start = max(0, match.start() - 20)
                    end = min(len(clean_text), match.end() + 20)
                    context = clean_text[start:end]
                    print(f"   ⚠️  投资现金流数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                    continue

    if "financing" not in cash_flows:
        fin_patterns = [
            (r"Net\s+cash\s+used\s+(?:by|in)\s+financing\s+activities\s*[\$]?\s*\((\d[\d,]*)\)", True),
            (r"Net\s+cash\s+provided\s+by\s+financing\s+activities\s*[\$]?\s*(\d[\d,]*)", False),
        ]
        for pattern, is_negative in fin_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                try:
                    value = match.group(1).replace(",", "")
                    if is_negative:
                        cash_flows["financing"] = f"(${int(value):,}M)"
                    else:
                        cash_flows["financing"] = f"${int(value):,}M"
                    break
                except (ValueError, OverflowError) as e:
                    start = max(0, match.start() - 20)
                    end = min(len(clean_text), match.end() + 20)
                    context = clean_text[start:end]
                    print(f"   ⚠️  筹资现金流数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                    continue

    if "capex" not in cash_flows:
        capex_patterns = [
            r"Capital\s+expenditures?\s+[$]?\s*\(\s*(\d[\d,]*)\s*\)",
            r"Additions\s+to\s+property,?\s*plant\s+and\s+equipment\s+[$]?\s*\(\s*(\d[\d,]*)\s*\)",
            r"Purchases\s+of\s+property,?\s*plant\s+and\s+equipment\s+[$]?\s*\(\s*(\d[\d,]*)\s*\)",
            r"Capital\s+expenditures?\s+[$]?\s*(\d[\d,]*)",
        ]
        max_capex = 0
        for pattern in capex_patterns:
            for match in re.finditer(pattern, clean_text, re.IGNORECASE):
                try:
                    value = int(match.group(1).replace(",", ""))
                    if value > max_capex:
                        max_capex = value
                except (ValueError, OverflowError) as e:
                    start = max(0, match.start() - 20)
                    end = min(len(clean_text), match.end() + 20)
                    context = clean_text[start:end]
                    print(f"   ⚠️  资本支出数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                    continue
        if max_capex > 0:
            cash_flows["capex"] = f"(${max_capex:,}M)"
            cash_flows["capex_num"] = -max_capex

    if "depreciation_amortization" not in cash_flows:
        depreciation_patterns = [
            r"Depreciation\s+and\s+amortization\s+(?:expense\s+)?[$]?\s*(\d[\d,]*)",
            r"Depreciation,\s*amortization,?\s+(?:and\s+impairment)\s+[$]?\s*(\d[\d,]*)",
            r"Depreciation(?:\s+expense)?\s+[$]?\s*(\d[\d,]*)",
        ]
        for pattern in depreciation_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                try:
                    value = match.group(1).replace(",", "")
                    cash_flows["depreciation_amortization"] = f"${int(value):,}M"
                    cash_flows["depreciation_amortization_num"] = int(value)
                    break
                except (ValueError, OverflowError) as e:
                    start = max(0, match.start() - 20)
                    end = min(len(clean_text), match.end() + 20)
                    context = clean_text[start:end]
                    print(f"   ⚠️  折旧摊销数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                    continue

    # 自由现金流 = 经营现金流 - 资本支出（取绝对值）
    if "operating" in cash_flows and "capex_num" in cash_flows:
        op_match = re.search(r'[\$]?([\d,]+)M', cash_flows["operating"])
        if op_match:
            op_value = int(op_match.group(1).replace(",", ""))
            if cash_flows["operating"].startswith("("):
                op_value = -op_value
            fcf = op_value + cash_flows["capex_num"]  # capex_num 已经是负数
            if fcf >= 0:
                cash_flows["free_cash_flow"] = f"${fcf:,}M"
            else:
                cash_flows["free_cash_flow"] = f"(${abs(fcf):,}M)"
            cash_flows["free_cash_flow_num"] = fcf

    return cash_flows


def extract_detailed_profit_items(text: str) -> Dict[str, Any]:
    """提取详细的利润项目（非经常性损益）"""
    items = {}

    # 清理 HTML 实体
    clean_text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    clean_text = re.sub(r"\s+", " ", clean_text)

    # 投资收益
    investment_income_patterns = [
        r"(?:Investment|Interest)\s+income\s*[:\$]\s*[$]?\s*([\d,]+)",
        r"Net\s+(?:investment|interest)\s+income\s*[:\$]\s*[$]?\s*([\d,]+)",
    ]
    for pattern in investment_income_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                items["investment_income"] = f"${int(match.group(1).replace(',', '')):,}M"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  投资收益数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 资产处置收益
    asset_sale_patterns = [
        r"(?:Gain|Loss)\s+on\s+(?:sale|disposition)\s+of\s+(?:assets|business)\s*[:\$]\s*[$]?\s*([\d,]+)",
    ]
    for pattern in asset_sale_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                items["asset_sale_gain"] = f"${int(match.group(1).replace(',', '')):,}M"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  资产处置收益数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 商誉减值
    impairment_patterns = [
        r"(?:Impairment|Goodwill\s+impairment)\s*[:\$]\s*[$]?\s*([\d,]+)",
    ]
    for pattern in impairment_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                items["impairment"] = f"${int(match.group(1).replace(',', '')):,}M"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  商誉减值数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 汇兑损益
    fx_patterns = [
        r"(?:Foreign\s+currency|Exchange)\s+(?:gain|loss|gains|losses)\s*[:\$]\s*[$]?\s*([\d,]+)",
    ]
    for pattern in fx_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                items["fx_gain_loss"] = f"${int(match.group(1).replace(',', '')):,}M"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  汇兑损益数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 重组费用
    restructuring_patterns = [
        r"Restructuring\s+(?:charges|expenses|costs)\s*[:\$]\s*[$]?\s*([\d,]+)",
    ]
    for pattern in restructuring_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                items["restructuring"] = f"${int(match.group(1).replace(',', '')):,}M"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  重组费用数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    # 诉讼和解
    litigation_patterns = [
        r"(?:Litigation|Legal)\s+(?:settlement|charge|expense)\s*[:\$]\s*[$]?\s*([\d,]+)",
    ]
    for pattern in litigation_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                items["litigation_settlement"] = f"${int(match.group(1).replace(',', '')):,}M"
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  诉讼和解数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

    return items
