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

# 货币金额类指标（单位归一化时需要缩放的字段，比率类指标不受影响）
_MONETARY_KEYS = [
    "revenue", "net_income", "cost_of_revenue", "gross_profit", "operating_income",
    "total_assets", "total_liabilities", "stockholders_equity",
    "current_assets", "current_liabilities", "inventories", "cash_equivalents",
    "interest_expense", "goodwill",
    "primary_revenue", "primary_cost", "primary_profit",
]


def detect_reporting_unit(text: str):
    """检测报表货币与数量级，返回 (货币符号, 换算到百万的因子)

    依据财务报表表头声明，如 "(in millions, except per share data)"、
    "(RMB in thousands, except share and per share data)"。
    无法识别时默认美元/百万（美股 10-K 主流格式）。
    """
    m = re.search(
        r"\(\s*(RMB|US\$|\$)?\s*(?:amounts?\s*)?in\s+(thousands|millions)\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return "$", 1.0
    currency = "¥" if (m.group(1) or "").upper() == "RMB" else "$"
    factor = 0.001 if m.group(2).lower() == "thousands" else 1.0
    return currency, factor


# 收益表/资产负债表关键科目的 XBRL us-gaap 标签（按优先级排列，通用标签在前）
_XBRL_METRIC_TAGS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
}

# duration（区间）类科目：10-Q 中存在单季与 YTD 累计两种 context，需按时长筛选；
# 资产负债表科目为 instant（时点）context，不适用
_DURATION_METRIC_KEYS = {"revenue", "net_income", "operating_income", "cost_of_revenue"}


def _xbrl_attr(text: str, m, attr: str) -> str:
    """取 iXBRL 标签的属性值。contextRef/unitRef/scale 可能位于 name= 之前
    （不在匹配的 group(0) 内），需向前回溯窗口查找；窗口可能包含前一个标签
    的属性，故取最后一个匹配（最贴近当前标签）。"""
    ms = re.findall(rf'{attr}="([^"]*)"', m.group(0))
    if ms:
        return ms[-1]
    window = text[max(0, m.start() - 500):m.start()]
    ms = re.findall(rf'{attr}="([^"]*)"', window)
    return ms[-1] if ms else ""


def _build_context_spans(text: str) -> Dict[str, tuple]:
    """解析 <xbrli:context> 的期间定义，返回 {context id: (start, end)}

    start 为 startDate（duration context）；instant 时点 context 的 start
    为 None。end 取 endDate 或 instant。contextRef 与会计期的对应关系由
    XBRL 的 context 元素权威定义，不受 id 命名方式影响（MSFT 用 GUID 命名）。
    """
    spans = {}
    for m in re.finditer(
        r'<xbrli:context\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</xbrli:context>',
        text, re.DOTALL | re.IGNORECASE,
    ):
        ctx_id, body = m.group(1), m.group(2)
        sm = re.search(r"<xbrli:startDate>([^<]+)</xbrli:startDate>", body)
        em = (re.search(r"<xbrli:endDate>([^<]+)</xbrli:endDate>", body)
              or re.search(r"<xbrli:instant>([^<]+)</xbrli:instant>", body))
        if em:
            spans[ctx_id] = (sm.group(1) if sm else None, em.group(1))
    return spans


def _build_context_periods(text: str) -> Dict[str, str]:
    """解析 context 期间定义，返回 {context id: 会计期年份}（endDate 年份）"""
    periods = {}
    for ctx_id, (_start, end) in _build_context_spans(text).items():
        ym = re.search(r"(20\d{2})", end)
        if ym:
            periods[ctx_id] = ym.group(1)
    return periods


def _filter_quarter_spans(text: str, matches: List[Any], spans: Dict[str, tuple]) -> List[Any]:
    """从 iXBRL 实例中筛选单季 context（duration 80-100 天）且期末最新的。

    10-Q 收益表同时存在单季与 YTD 累计 context（期末相同），按"同年最大值"
    选取会拿到累计值冒充单季 —— 财务数据不可造假，必须按时长区分。"""
    from datetime import date

    def _end_day(m):
        span = spans.get(_xbrl_attr(text, m, "contextRef"))
        if not span or span[0] is None:
            return None
        try:
            start = date.fromisoformat(span[0][:10])
            end = date.fromisoformat(span[1][:10])
        except ValueError:
            return None
        if not 80 <= (end - start).days <= 100:
            return None
        return end

    quarter_insts = [(m, d) for m, d in ((m, _end_day(m)) for m in matches) if d]
    if not quarter_insts:
        return []
    max_end = max(d for _, d in quarter_insts)
    return [m for m, d in quarter_insts if d == max_end]


def _xbrl_ctx_year(text: str, m, periods: Dict[str, str] = None) -> str:
    """取事实的会计期年份：优先查 context period 定义，回退到 contextRef
    中的年份数字。列顺序因公司而异（MSFT 最新年在前、PDD 最新年在后），
    不能按出现顺序取，必须比较年份。"""
    ctx_id = _xbrl_attr(text, m, "contextRef")
    if periods and ctx_id in periods:
        return periods[ctx_id]
    years = re.findall(r"(20\d{2})", ctx_id)
    return max(years) if years else ""


def _xbrl_parse(text: str, m):
    """解析 iXBRL 标签数值：按 scale 换算到百万，sign="-" 表示负数，
    根据 unitRef 判断币种。返回 (百万值, 货币符号)"""
    value = int(m.group(1).replace(",", ""))
    scale_s = _xbrl_attr(text, m, "scale")
    if scale_s:
        value = round(value * (10 ** int(scale_s)) / 1e6)
    if _xbrl_attr(text, m, "sign") == "-":
        value = -value
    unit_upper = _xbrl_attr(text, m, "unitRef").upper()
    currency = "¥" if ("RMB" in unit_upper or "CNY" in unit_upper) else "$"
    return value, currency


def _xbrl_pick_latest(text: str, tag_names: List[str], periods: Dict[str, str] = None) -> List[Any]:
    """收集指定 XBRL 标签在最新会计期（context 年份最大）的全部实例"""
    matches = []
    for tag in tag_names:
        pattern = rf'name="[^"]*:{re.escape(tag)}"[^>]*>([\d,]+)<'
        matches.extend(re.finditer(pattern, text, re.IGNORECASE))
    if not matches:
        return []
    target_year = max(_xbrl_ctx_year(text, m, periods) for m in matches)
    return [m for m in matches if _xbrl_ctx_year(text, m, periods) == target_year]


def _compute_derived_metrics(metrics: Dict[str, Any], currency: str = "$") -> None:
    """基于 *_num 原始值计算派生指标（毛利润/利润率/财务健康比率）。

    在文本提取、XBRL 覆盖、单位归一化全部完成后调用一次，
    保证派生值基于最终合并后的数据，可整体替换中间重复计算。"""

    # 毛利润 = 营收 - 营业成本；毛利率
    if "revenue_num" in metrics and "cost_of_revenue_num" in metrics:
        gross_profit = metrics["revenue_num"] - metrics["cost_of_revenue_num"]
        metrics["gross_profit"] = f"{currency}{gross_profit:,}M"
        metrics["gross_profit_num"] = gross_profit
        if metrics["revenue_num"] > 0:
            metrics["gross_margin"] = f"{gross_profit / metrics['revenue_num'] * 100:.1f}%"

    # 营业利润率 / 净利率
    if "operating_income_num" in metrics and metrics.get("revenue_num", 0) > 0:
        metrics["operating_margin"] = f"{metrics['operating_income_num'] / metrics['revenue_num'] * 100:.1f}%"
    if "net_income_num" in metrics and metrics.get("revenue_num", 0) > 0:
        metrics["net_margin"] = f"{metrics['net_income_num'] / metrics['revenue_num'] * 100:.1f}%"

    # ====== 财务健康指标 ======

    # 资产负债率 = 总负债 / 总资产
    if metrics.get("total_assets_num", 0) > 0 and "total_liabilities_num" in metrics:
        debt_ratio = metrics["total_liabilities_num"] / metrics["total_assets_num"] * 100
        metrics["debt_to_asset_ratio"] = f"{debt_ratio:.1f}%"
        metrics["debt_to_asset_ratio_num"] = debt_ratio

    # 流动比率 = 流动资产 / 流动负债
    if "current_assets_num" in metrics and metrics.get("current_liabilities_num", 0) > 0:
        current_ratio = metrics["current_assets_num"] / metrics["current_liabilities_num"]
        metrics["current_ratio"] = f"{current_ratio:.2f}"
        metrics["current_ratio_num"] = current_ratio

    # 速动比率 = (流动资产 - 存货) / 流动负债
    if "current_assets_num" in metrics and "inventories_num" in metrics and metrics.get("current_liabilities_num", 0) > 0:
        quick_ratio = (metrics["current_assets_num"] - metrics["inventories_num"]) / metrics["current_liabilities_num"]
        metrics["quick_ratio"] = f"{quick_ratio:.2f}"
        metrics["quick_ratio_num"] = quick_ratio

    # 净现金不计算：无债务科目数据源，用"现金-总负债"近似会得出错误结论
    # （如现金充裕的 MSFT/AAPL 显示巨额负净现金）。宁缺勿错，不输出该字段。

    # 利息覆盖倍数 = 营业利润 / 利息费用
    if "operating_income_num" in metrics and metrics.get("interest_expense_num", 0) > 0:
        interest_coverage = metrics["operating_income_num"] / metrics["interest_expense_num"]
        metrics["interest_coverage"] = f"{interest_coverage:.1f}x"
        metrics["interest_coverage_num"] = interest_coverage

    # 商誉占比 = 商誉 / 总资产
    if "goodwill_num" in metrics and metrics.get("total_assets_num", 0) > 0:
        goodwill_ratio = metrics["goodwill_num"] / metrics["total_assets_num"] * 100
        metrics["goodwill_to_assets"] = f"{goodwill_ratio:.1f}%"
        metrics["goodwill_to_assets_num"] = goodwill_ratio

    # ROE = 净利润 / 股东权益
    if "net_income_num" in metrics and metrics.get("stockholders_equity_num", 0) > 0:
        roe = metrics["net_income_num"] / metrics["stockholders_equity_num"] * 100
        metrics["roe"] = f"{roe:.1f}%"
        metrics["roe_num"] = roe


# 比率类指标合理范围（超出视为提取/口径异常 → 置空并告警）。
# 下界为宽松经验值（真实企业极端亏损可能突破常规区间），主要拦截
# "抓错科目/抓错期间"产生的数量级错误
_RATIO_SANE_BOUNDS = {
    "metrics": {
        "gross_margin": (-200, 100, "毛利率"),
        "operating_margin": (-300, 300, "营业利润率"),
        "net_margin": (-300, 300, "净利率"),
        "debt_to_asset_ratio": (0, 150, "资产负债率"),
        "current_ratio": (0, 100, "流动比率"),
        "quick_ratio": (0, 100, "速动比率"),
        "goodwill_to_assets": (0, 100, "商誉占比"),
    },
    "cash_flows": {
        "fcf_margin": (-300, 300, "FCF利润率"),
        "capex_to_revenue": (0, 500, "CapEx/营收"),
    },
}


def _parse_ratio_value(v) -> Any:
    """从展示字符串解析比率数值（兼容 "67.9%" / "1.66" / "12.5x" 格式）"""
    if not isinstance(v, str):
        return None
    s = v.strip().rstrip("%").rstrip("x").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def validate_metrics(metrics: Dict[str, Any], cash_flows: Dict[str, Any] = None) -> List[str]:
    """数据质量校验：获取失败的字段保持缺失（报告的缺失警告机制已覆盖）；
    提取结果超出合理范围的异常值直接置空并返回告警列表——财务数据必须真实，
    不做默认值、不做模拟值。"""
    warnings = []
    for dict_name, data in (("metrics", metrics), ("cash_flows", cash_flows or {})):
        for key, (lo, hi, label) in _RATIO_SANE_BOUNDS[dict_name].items():
            if key not in data:
                continue
            value = _parse_ratio_value(data[key])
            if value is None or lo <= value <= hi:
                continue
            warnings.append(
                f"{label}({key})={data[key]} 超出合理范围 [{lo}, {hi}]，疑似提取错误，已置空")
            data.pop(key)
            data.pop(key + "_num", None)

    # 基础科目必须为正数
    for key, label in (("revenue", "营收"), ("total_assets", "总资产")):
        v = metrics.get(key + "_num")
        if isinstance(v, (int, float)) and v <= 0:
            warnings.append(f"{label}={metrics.get(key)} 非正数，疑似提取错误，已置空")
            metrics.pop(key)
            metrics.pop(key + "_num", None)

    return warnings


# 无 iXBRL 文件（如 PDD 的 6-K 盈利公告）叙述句模式：句中数字为本期值，
# 货币直接由句子给出（RMB/US$），单位支持 billion/million（早期公告用 million）
_NARRATIVE_METRIC_PATTERNS = [
    ("revenue",
     r"Total\s+revenues?(?:\s+in\s+the\s+quarter)?\s+were\s+(RMB|US\$)\s*([\d.,]+)\s+(billion|million)"),
    ("operating_income",
     r"Operating\s+profit(?:\s+in\s+the\s+quarter)?\s+was\s+(RMB|US\$)\s*([\d.,]+)\s+(billion|million)"),
    ("net_income",
     r"Net\s+income(?:\s+attributable\s+to\s+ordinary\s+shareholders)?(?:\s+in\s+the\s+quarter)?"
     r"\s+was\s+(RMB|US\$)\s*([\d.,]+)\s+(billion|million)"),
]


def _narrative_to_millions(num_str: str, unit: str) -> float:
    return float(num_str.replace(",", "")) * (1000 if unit.lower() == "billion" else 1)


def _extract_metrics_from_narrative(text: str) -> Dict[str, Any]:
    """无 iXBRL 文件从叙述句提取本期指标。

    这类文件（如 PDD 6-K 盈利公告）报表列为[前期, 本期, US$]，表格取第一个
    数会拿到前期值 —— 表格值宁缺勿错，只输出叙述句可确证的本期数据。"""
    clean_text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    clean_text = re.sub(r"\s+", " ", clean_text)

    metrics: Dict[str, Any] = {}
    currency = "$"
    for key, pattern in _NARRATIVE_METRIC_PATTERNS:
        m = re.search(pattern, clean_text, re.IGNORECASE)
        if not m:
            continue
        currency = "¥" if m.group(1).upper() == "RMB" else "$"
        value = _narrative_to_millions(m.group(2), m.group(3))
        metrics[f"{key}_num"] = int(round(value))
        metrics[key] = f"{currency}{value:,.0f}M"

    _compute_derived_metrics(metrics, currency if metrics else "$")
    return metrics


def extract_key_metrics(text: str, html: str = None, quarterly: bool = False) -> Dict[str, Any]:
    """从文本中提取关键财务指标

    html: 原始 HTML 内容（可选）。提供时优先用 iXBRL 标签提取收益表/资产负债表
    科目——文本正则在列序/分部表场景会抓错会计期（如 PDD 20-F 的 VIE 表列序），
    而 XBRL 带 contextRef 会计期上下文，更可靠；无 XBRL 匹配的科目回退文本结果。

    quarterly: 10-Q 场景为 True，收益表/每股收益取单季（≈3个月 duration）
    context —— 同期末还存在 YTD 累计 context，按数值最大选取会拿到累计值。
    资产负债表为时点 context，不受影响。
    """
    metrics = {}

    # 无 iXBRL 的文件（如 PDD 6-K 盈利公告）：报表列为[前期, 本期, US$]，
    # 表格取第一个数会拿到前期值 —— 只从叙述句提取本期数据
    if html is not None and not re.search(r"<ix:nonFraction", html):
        return _extract_metrics_from_narrative(text)

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

    # EPS 模式 - 数字紧跟在标签之后（可有 $ 前缀），禁止大跨度跳匹配，防止
    # "Diluted earnings per share ... Revenue increased $50.1 billion" 这类跨行误匹配
    # 约定：优先 Diluted（估值标准口径），再 Basic；EPS 必须带小数点，避免抓到 "increased 32%" 整数
    # (?<![\d,.]) 防止从 "7,452.9" 这类股本数中截取小数部分
    eps_num = r"(?<![\d,.])(\d+\.\d+)"
    eps_patterns = [
        rf"Diluted\s+(?:earnings\s*(?:\(loss\))?\s*)?per\s+share\s*[$]?\s*\(?{eps_num}\)?",
        rf"Earnings\s+per\s+share.{{0,40}}?Diluted\s*[$]?\s*\(?{eps_num}\)?",
        rf"Diluted[^0-9$.]{{0,30}}{eps_num}",
        rf"Basic\s+(?:earnings\s*(?:\(loss\))?\s*)?per\s+share\s*[$]?\s*\(?{eps_num}\)?",
        rf"per\s+(?:common\s+)?share\s*[:$]\s*\(?{eps_num}\)?",
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
    # 与营收相同的口径：优先 "Total cost of revenue/sales"（利润表行），
    # 否则取所有匹配中的最大值（分部数据之和等于总额，最大值即合并口径）
    cost_patterns = [
        r"Total\s+cost\s+of\s+(?:revenues?|sales)\s+[$]?\s*\(?(\d[\d,]*)",
        r"Cost\s+of\s+(?:revenues?|sales)\s+[$]?\s*\(?(\d[\d,]*)",
    ]

    def _parse_num(s):
        return int(s.replace(",", ""))

    total_cost = 0
    for match in re.finditer(cost_patterns[0], clean_text, re.IGNORECASE):
        try:
            total_cost = _parse_num(match.group(1))
            break
        except (ValueError, OverflowError):
            continue

    max_cost = total_cost
    if max_cost == 0:
        for pattern in cost_patterns[1:]:
            for match in re.finditer(pattern, clean_text, re.IGNORECASE):
                try:
                    value = _parse_num(match.group(1))
                    if value > max_cost:
                        max_cost = value
                except (ValueError, OverflowError) as e:
                    start = max(0, match.start() - 20)
                    end = min(len(clean_text), match.end() + 20)
                    context = clean_text[start:end]
                    print(f"   ⚠️  营业成本数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                    continue

    if max_cost > 0:
        metrics["cost_of_revenue"] = f"${max_cost:,}M"
        metrics["cost_of_revenue_num"] = max_cost

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
                break
            except (ValueError, OverflowError) as e:
                start = max(0, match.start() - 20)
                end = min(len(clean_text), match.end() + 20)
                context = clean_text[start:end]
                print(f"   ⚠️  营业利润数值解析失败: '{match.group(1)}' | 上下文: '{context}' | {e}")
                continue

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

    # ====== XBRL 结构化提取（优先于文本启发式）======
    # 收益表/资产负债表科目带 contextRef 会计期上下文，可避免文本正则抓错期间；
    # 同一会计期的多个实例（母公司/子公司/VIE/合并列）取最大值即合并口径
    xbrl_keys = set()  # XBRL 已换算到百万，末尾文本单位归一化时跳过，避免双重缩放
    if html:
        spans = _build_context_spans(html)
        periods = _build_context_periods(html)
        for key, tags in _XBRL_METRIC_TAGS.items():
            insts = _xbrl_pick_latest(html, tags, periods)
            if not insts:
                continue
            # 10-Q：收益表科目（duration context）筛选单季，避免取到 YTD 累计值
            if quarterly and key in _DURATION_METRIC_KEYS:
                quarter_insts = _filter_quarter_spans(html, insts, spans)
                if not quarter_insts:
                    # iXBRL 中存在该科目实例，但识别不出单季 context（80-100 天，
                    # 如仅披露 YTD 累计 context）。文本回退值来自多列报表按行取数，
                    # 大概率是累计列或上年同期列——财务数据不可造假：置空并告警，
                    # 宁缺勿错，绝不用无法确证单季口径的值冒充单季
                    if key in metrics:
                        metrics.pop(key)
                        metrics.pop(key + "_num", None)
                        print(f"   ⚠️  10-Q 未识别出 {key} 的单季 context（仅有 YTD 累计），"
                              f"文本回退值已置空")
                    continue
                insts = quarter_insts
            chosen = max(insts, key=lambda m: int(m.group(1).replace(",", "")))
            try:
                value, currency = _xbrl_parse(html, chosen)
            except (ValueError, OverflowError) as e:
                print(f"   ⚠️  XBRL {key} 数值解析失败: '{chosen.group(1)}' | {e}")
                continue
            metrics[key] = f"{currency}{value:,}M" if value >= 0 else f"({currency}{abs(value):,}M)"
            metrics[key + "_num"] = value
            xbrl_keys.add(key)

        # EPS：XBRL 优先 Diluted，其次 Basic（每股数值无 scale 换算）
        for tag in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
            eps_matches = list(re.finditer(
                rf'name="[^"]*:{tag}"[^>]*>([\d.]+)<', html, re.IGNORECASE))
            if not eps_matches:
                continue
            target_year = max(_xbrl_ctx_year(html, m, periods) for m in eps_matches)
            eps_insts = [m for m in eps_matches if _xbrl_ctx_year(html, m, periods) == target_year]
            # 10-Q：单季 EPS 优先于 YTD 累计 EPS
            if quarterly:
                quarter_insts = _filter_quarter_spans(html, eps_insts, spans)
                if quarter_insts:
                    eps_insts = quarter_insts
                else:
                    # 同收益表科目：识别不出单季 context（仅 YTD 累计）时置空告警，
                    # 不用累计 EPS 冒充单季
                    if "eps" in metrics:
                        metrics.pop("eps")
                        print("   ⚠️  10-Q 未识别出 EPS 的单季 context（仅有 YTD 累计），"
                              "文本回退值已置空")
                    break
            chosen = eps_insts[0]
            currency = "¥" if ("RMB" in _xbrl_attr(html, chosen, "unitRef").upper()
                               or "CNY" in _xbrl_attr(html, chosen, "unitRef").upper()) else "$"
            metrics["eps"] = f"{currency}{chosen.group(1)}"
            xbrl_keys.add("eps")
            break

    # ====== 单位/币种归一化（如 "(RMB in thousands)" 外国发行人报表） ======
    # 仅作用于文本回退路径的科目；XBRL 科目已按 scale 属性换算
    currency, factor = detect_reporting_unit(clean_text)
    if factor != 1.0 or currency != "$":
        for key in _MONETARY_KEYS:
            num_key = key + "_num"
            if num_key in metrics and key not in xbrl_keys:
                metrics[num_key] = round(metrics[num_key] * factor)
                metrics[key] = f"{currency}{metrics[num_key]:,}M"
        if "eps" in metrics and currency != "$" and "eps" not in xbrl_keys:
            metrics["eps"] = currency + metrics["eps"].lstrip("$")

    # 封面流通股数（dei 结构化标签，DCF 每股估值的兜底数据源）。
    # 10-K/10-Q/20-F 封面必有；6-K 无 dei 标签则缺失，不伪造。
    # 多类股（如 GOOGL A/B/C、NKE A/B）为多个同名标签，需求和；
    # scale 语义: 显示值 × 10^scale = 实际股数（GOOGL scale=6 值 5,941 = 5.941B 股）
    if html is not None:
        total_shares = 0
        for m_sh in re.finditer(
                r'name="dei:EntityCommonStockSharesOutstanding"([^>]*)>([\d,]+)<', html):
            scale_m = re.search(r'scale="(\d+)"', m_sh.group(1))
            scale = int(scale_m.group(1)) if scale_m else 0
            total_shares += int(m_sh.group(2).replace(",", "")) * (10 ** scale)
        if total_shares > 0:
            metrics["shares_outstanding_num"] = total_shares

    # 派生指标（毛利润/利润率/财务健康比率）在数据源合并与归一化完成后统一计算
    _compute_derived_metrics(metrics, currency)

    return metrics


def extract_cash_flows(text: str) -> Dict[str, Any]:
    """从文本中提取现金流数据（经营/投资/筹资）"""
    cash_flows = {}

    # 无 iXBRL 的文件（如 PDD 6-K 盈利公告）：现金流量表列为[前期, 本期, US$]，
    # 表格取第一个数会拿到前期值 —— 只从叙述句提取本期经营现金流
    # （仅对 HTML 文档生效；纯文本输入走原有文本回退路径）
    if re.match(r"\s*<", text) and not re.search(r"<ix:nonFraction", text):
        # 去除 HTML 标签（叙述句中可能夹杂标签，如 RMB</FONT>25.7 billion）
        clean_text = re.sub(r"<[^>]+>", " ", text)
        clean_text = re.sub(r"\s+", " ", clean_text.replace("&#160;", " ").replace("&nbsp;", " "))
        m = re.search(
            r"Net\s+cash\s+(generated\s+from|used\s+in)\s+operating\s+activities"
            r"\s+was\s+(RMB|US\$)\s*([\d.,]+)\s+(billion|million)",
            clean_text, re.IGNORECASE)
        if m:
            currency = "¥" if m.group(2).upper() == "RMB" else "$"
            value = _narrative_to_millions(m.group(3), m.group(4))
            if m.group(1).lower().startswith("used"):
                value = -value
            cash_flows["operating"] = f"{currency}{abs(value):,.0f}M" if value >= 0 else f"({currency}{abs(value):,.0f}M)"
            cash_flows["operating_num"] = value
        return cash_flows

    # 清理 HTML 实体和标签
    clean_text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    clean_text = re.sub(r"<[^>]+>", " ", clean_text)  # 去除 HTML 标签
    clean_text = re.sub(r"\s+", " ", clean_text)
    # 文本回退路径的单位换算（XBRL 路径用 scale 属性，不走这里）
    text_currency, unit_factor = detect_reporting_unit(clean_text)
    op_currency = text_currency  # FCF 币种随经营现金流（XBRL 提取时更新为 unitRef 币种）

    # 方法1: 从 XBRL 标签提取（优先）
    xbrl_patterns = {
        "operating": r'name="us-gaap:NetCashProvidedByUsedInOperatingActivities"[^>]*>([\d,]+)<',
        "investing": r'name="us-gaap:NetCashProvidedByUsedInInvestingActivities"[^>]*>([\d,]+)<',
        "financing": r'name="us-gaap:NetCashProvidedByUsedInFinancingActivities"[^>]*>([\d,]+)<',
        # capex：任意命名空间的 PaymentsToAcquire*，按语义（Property/Equipment/
        # Productive）筛选，排除证券投资类（ShortTerm/Longterm Investments），
        # 兼容自定义标签（如 pdd:PaymentsToAcquirePropertyEquipmentAndSoftwareAndIntangibleAssets）
        "capex": r'name="[^"]*:PaymentsToAcquire(?=[A-Za-z]*(?:Property|Equipment|Productive))[A-Za-z]*"[^>]*>([\d,]+)<',
        # 折旧摊销：兼容任意命名空间（us-gaap / 公司自定义如 msft:DepreciationAmortizationAndOther），
        # 排除 Accumulated/Deferred 开头的资产负债表科目；现金流表先于附注出现，取首个匹配即当期值
        "depreciation_amortization": r'name="[^"]*:(?!Accumulated|Deferred)[Dd]epreciation[^"]*"[^>]*>([\d,]+)<',
    }

    xbrl_keys = set()  # 已按 scale 属性换算的 key，末尾文本归一化时跳过，避免双重缩放
    periods = _build_context_periods(text)
    for key, pattern in xbrl_patterns.items():
        # 收集所有实例，按 context 的会计期年份选最新
        # （列顺序因公司而异：MSFT 最新年在前，PDD 最新年在后）
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if not matches:
            continue
        xbrl_keys.add(key)

        target_year = max((_xbrl_ctx_year(text, m, periods) for m in matches), default="")
        match = next(
            (m for m in matches if _xbrl_ctx_year(text, m, periods) == target_year),
            matches[0],
        )
        try:
            value_str = match.group(1)
            value = int(value_str.replace(",", ""))
            # scale 属性：XBRL 数值 = 表格显示值 × 10^scale，统一换算到百万
            scale_s = _xbrl_attr(text, match, "scale")
            if scale_s:
                value = round(value * (10 ** int(scale_s)) / 1e6)
            # 币种（unitRef 含 RMB/CNY 时用 ¥ 显示）
            unit_upper = _xbrl_attr(text, match, "unitRef").upper()
            currency = "¥" if ("RMB" in unit_upper or "CNY" in unit_upper) else "$"
            if key == "operating":
                op_currency = currency  # FCF 币种随经营现金流
            # 检查原文中该数值是否在括号内（括号表示负数）
            is_negative = False
            for m in re.finditer(re.escape(value_str), text):
                start = max(0, m.start() - 5)
                end = min(len(text), m.end() + 5)
                context = text[start:end]
                if context.startswith("(") or "(" in text[max(0, m.start()-3):m.start()+1]:
                    is_negative = True
                    break
            if key == "capex":
                cash_flows["capex"] = f"({currency}{value:,}M)"
                cash_flows["capex_num"] = -value  # 负数表示现金流出
            elif key == "depreciation_amortization":
                cash_flows[key] = f"{currency}{value:,}M"
                cash_flows[f"{key}_num"] = value
            else:
                if is_negative:
                    cash_flows[key] = f"({currency}{value:,}M)"
                    cash_flows[f"{key}_num"] = -value
                else:
                    cash_flows[key] = f"{currency}{value:,}M"
                    cash_flows[f"{key}_num"] = value
        except (ValueError, OverflowError) as e:
            print(f"   ⚠️  XBRL {key} 数值解析失败: '{match.group(1)}' | {e}")
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
                    value = int(match.group(1).replace(",", ""))
                    if is_negative:
                        cash_flows["operating"] = f"(${value:,}M)"
                        cash_flows["operating_num"] = -value
                    else:
                        cash_flows["operating"] = f"${value:,}M"
                        cash_flows["operating_num"] = value
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
                    value = int(match.group(1).replace(",", ""))
                    if is_negative:
                        cash_flows["investing"] = f"(${value:,}M)"
                        cash_flows["investing_num"] = -value
                    else:
                        cash_flows["investing"] = f"${value:,}M"
                        cash_flows["investing_num"] = value
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
                    value = int(match.group(1).replace(",", ""))
                    if is_negative:
                        cash_flows["financing"] = f"(${value:,}M)"
                        cash_flows["financing_num"] = -value
                    else:
                        cash_flows["financing"] = f"${value:,}M"
                        cash_flows["financing_num"] = value
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
            r"Depreciation,?\s+amortization,?\s+and\s+(?:other|impairment)\s+[$]?\s*\(?(\d[\d,]*)",
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

    # 单位归一化（文本回退路径：如 "(RMB in thousands)" 报表；XBRL 路径已按 scale 换算）
    if unit_factor != 1.0:
        for key in ("operating", "investing", "financing", "capex",
                    "depreciation_amortization"):
            num_key = key + "_num"
            if num_key not in cash_flows or key in xbrl_keys:
                continue
            cash_flows[num_key] = round(cash_flows[num_key] * unit_factor)
            num = cash_flows[num_key]
            if key == "capex":
                cash_flows[key] = f"(${abs(num):,}M)"
            elif key == "depreciation_amortization":
                cash_flows[key] = f"${num:,}M"
            elif num >= 0:
                cash_flows[key] = f"${num:,}M"
            else:
                cash_flows[key] = f"(${abs(num):,}M)"

    # 自由现金流 = 经营现金流 - 资本支出（*_num 已统一为百万口径，放在归一化之后计算
    # 避免被文本单位声明二次缩放）
    if "operating_num" in cash_flows and "capex_num" in cash_flows:
        fcf = cash_flows["operating_num"] + cash_flows["capex_num"]  # capex_num 已是负数
        if fcf >= 0:
            cash_flows["free_cash_flow"] = f"{op_currency}{fcf:,}M"
        else:
            cash_flows["free_cash_flow"] = f"({op_currency}{abs(fcf):,}M)"
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
