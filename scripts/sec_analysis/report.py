"""
SEC 财报报告生成模块
生成提取文件和最终分析报告
"""

import os
import re
import sys
import json
import importlib.util
import urllib.request
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# 导入必填字段定义
from .extraction import REQUIRED_METRIC_FIELDS, REQUIRED_CASHFLOW_FIELDS

# 生成器版本号（单一定义点 scripts/dataversion.py；本包由 scripts/ 下的 CLI
# 入口加载，scripts/ 均在 sys.path 上）
from dataversion import ANALYSIS_VERSION

# 翻译功能
try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False
    print("⚠️  deep-translator 未安装，翻译功能不可用")

# 目录配置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "sec_filings")
ANALYSIS_DIR = os.path.join(PROJECT_ROOT, "reports", "sec_analysis")

# API Keys
FMP_KEY = "OeyYwkTOzQUfkywmNu8p0NFIP1pTSv6x"
FMP_URL = "https://financialmodelingprep.com/stable"


def get_stock_quote(ticker: str) -> Optional[Dict]:
    """从 FMP API 获取股票实时行情数据"""
    try:
        # 移除市场前缀（如 US.QCOM -> QCOM）
        symbol = ticker.split(".")[-1] if "." in ticker else ticker
        url = f"{FMP_URL}/quote?symbol={symbol}&apikey={FMP_KEY}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
            if data and len(data) > 0:
                return data[0]
    except Exception as e:
        print(f"⚠️  获取行情数据失败: {e}")
    return None


def calculate_valuation_metrics(ticker: str, metrics: Dict, cash_flows: Dict) -> Dict:
    """计算估值指标"""
    valuation = {}

    # 获取实时行情数据
    quote = get_stock_quote(ticker)
    if not quote:
        print("⚠️  无法获取行情数据，跳过估值指标计算")
        return valuation

    # 从行情数据中提取
    stock_price = quote.get("price", 0)
    market_cap = quote.get("marketCap", 0)
    shares_outstanding = quote.get("sharesOutstanding", 0)

    if stock_price:
        valuation["stock_price"] = f"${stock_price:.2f}"
    if market_cap:
        valuation["market_cap"] = f"${market_cap / 1e9:.2f}B"
    if shares_outstanding:
        valuation["shares_outstanding"] = f"{shares_outstanding / 1e6:.1f}M"

    # 市盈率 (PE) = 股价 / EPS(TTM)
    if stock_price and metrics.get("eps"):
        try:
            eps_value = float(metrics["eps"].replace("$", ""))
            if eps_value > 0:
                pe_ratio = stock_price / eps_value
                valuation["pe_ratio"] = f"{pe_ratio:.1f}"
                valuation["pe_ratio_num"] = pe_ratio
        except ValueError:
            pass

    # 市净率 (PB) = 股价 / 每股净资产
    if stock_price and metrics.get("stockholders_equity_num") and shares_outstanding:
        bvps = metrics["stockholders_equity_num"] * 1e6 / shares_outstanding
        if bvps > 0:
            pb_ratio = stock_price / bvps
            valuation["pb_ratio"] = f"{pb_ratio:.1f}"
            valuation["pb_ratio_num"] = pb_ratio
            valuation["book_value_per_share"] = f"${bvps:.2f}"

    # 市销率 (PS) = 市值 / 营收
    if market_cap and metrics.get("revenue_num"):
        ps_ratio = market_cap / (metrics["revenue_num"] * 1e6)
        valuation["ps_ratio"] = f"{ps_ratio:.1f}"
        valuation["ps_ratio_num"] = ps_ratio

    # EV/EBITDA = 企业价值 / EBITDA
    if market_cap and metrics.get("total_liabilities_num") and metrics.get("cash_equivalents_num"):
        ev = market_cap + metrics["total_liabilities_num"] * 1e6 - metrics["cash_equivalents_num"] * 1e6
        valuation["enterprise_value"] = f"${ev / 1e9:.2f}B"

        if metrics.get("operating_income_num"):
            ebitda_approx = metrics["operating_income_num"] * 1e6 * 1.2
            ev_ebitda = ev / ebitda_approx if ebitda_approx > 0 else 0
            valuation["ev_ebitda"] = f"{ev_ebitda:.1f}"
            valuation["ev_ebitda_num"] = ev_ebitda

    # 自由现金流收益率 = FCF / 市值 × 100%
    if cash_flows.get("free_cash_flow_num") and market_cap:
        fcf_yield = cash_flows["free_cash_flow_num"] * 1e6 / market_cap * 100
        valuation["fcf_yield"] = f"{fcf_yield:.1f}%"
        valuation["fcf_yield_num"] = fcf_yield

    # PEG = PE / 净利润增长率
    if valuation.get("pe_ratio_num") and metrics.get("net_income_growth"):
        try:
            growth_rate = float(metrics["net_income_growth"].replace("%", ""))
            if growth_rate > 0:
                peg = valuation["pe_ratio_num"] / growth_rate
                valuation["peg_ratio"] = f"{peg:.1f}"
                valuation["peg_ratio_num"] = peg
        except ValueError:
            pass

    return valuation


def parse_extraction_md(filepath: str) -> Optional[Dict]:
    """解析单个提取 .md 文件，返回结构化 dict"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    record = {"source": os.path.basename(filepath)}

    # 检测财报类型
    filename = os.path.basename(filepath)
    if "10-K" in filename or "10k" in filename.lower():
        record["filing_type"] = "10-K"
    elif "20-F" in filename or "20f" in filename.lower():
        record["filing_type"] = "20-F"
    elif "6-K" in filename or "6k" in filename.lower():
        record["filing_type"] = "6-K"
    else:
        record["filing_type"] = "10-Q"

    # 提取期间（如 10-K_FY2025.md → FY2025）
    period_match = re.search(r"_(\w+)\.md$", filename)
    if period_match:
        record["fiscal_period"] = period_match.group(1)

    # 货币无关的金额解析：兼容 $/¥ 前缀与括号负数（RMB 报告如 PDD 用 ¥）
    def _money(x):
        x = x.strip()
        digits = re.sub(r"[^\d]", "", x)
        v = int(digits) if digits else 0
        return -v if x.startswith("(") else v

    # 解析财务指标表格
    metrics_map = {
        "营收": ("revenue", _money),
        "毛利润": ("gross_profit", _money),
        "毛利率": ("gross_margin", lambda x: float(x.replace("%", ""))),
        "营业利润": ("operating_income", _money),
        "营业利润率": ("operating_margin", lambda x: float(x.replace("%", ""))),
        "净利润": ("net_income", _money),
        "净利率": ("net_margin", lambda x: float(x.replace("%", ""))),
        "每股收益": ("eps", lambda x: float(x.replace("$", "").replace("¥", ""))),
        "总资产": ("total_assets", _money),
        "总负债": ("total_liabilities", _money),
        "股东权益": ("stockholders_equity", _money),
        "流动资产": ("current_assets", _money),
        "流动负债": ("current_liabilities", _money),
        "现金及等价物": ("cash_equivalents", _money),
        "资产负债率": ("debt_to_asset_ratio", lambda x: float(x.replace("%", ""))),
        "流动比率": ("current_ratio", lambda x: float(x)),
        "速动比率": ("quick_ratio", lambda x: float(x)),
        "净资产收益率": ("roe", lambda x: float(x.replace("%", ""))),
        "利息覆盖倍数": ("interest_coverage", lambda x: float(x.replace("x", ""))),
        "商誉占比": ("goodwill_to_assets", lambda x: float(x.replace("%", ""))),
        "存货": ("inventories", _money),
        "利息费用": ("interest_expense", _money),
        "商誉": ("goodwill", _money),
        "主营营收": ("primary_revenue", _money),
        "主营成本": ("primary_cost", _money),
        "主营利润": ("primary_profit", _money),
        "主营利润率": ("primary_margin", lambda x: float(x.replace("%", ""))),
        # 封面流通股数（百万股），非货币金额，不能用 _money（会丢小数位）
        "流通股数": ("shares_outstanding", lambda x: float(x.replace(",", "").rstrip("M"))),
    }

    # 解析现金流表格（括号表示负数）
    def _parse_cf_value(x):
        is_neg = x.strip().startswith("(")
        val = int(x.replace(",", "").replace("$", "").replace("M", "").replace("(", "").replace(")", ""))
        return -val if is_neg else val

    cf_map = {
        "经营现金流": ("operating_cf", _parse_cf_value),
        "投资现金流": ("investing_cf", _parse_cf_value),
        "筹资现金流": ("financing_cf", _parse_cf_value),
        "资本支出": ("capex", _parse_cf_value),
        "自由现金流": ("free_cash_flow", _parse_cf_value),
        "折旧摊销": ("depreciation_amortization", lambda x: int(x.replace(",", "").replace("$", "").replace("M", ""))),
    }

    # 解析现金流质量指标
    quality_map = {
        "CFO/净利润": ("cfo_to_net_income", lambda x: float(x)),
        "FCF利润率": ("fcf_margin", lambda x: float(x.replace("%", ""))),
        "CapEx/营收": ("capex_to_revenue", lambda x: float(x.replace("%", ""))),
    }

    # 逐行解析
    for line in content.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 2:
            continue

        label, value = cells[0], cells[1]

        # 检查负数（括号表示）
        is_negative = value.startswith("(") and value.endswith(")")

        # 财务指标
        if label in metrics_map:
            key, parser = metrics_map[label]
            try:
                record[key] = parser(value)
                if "¥" in value:
                    record["currency"] = "¥"  # RMB 报告（如 PDD），聚合表格沿用原币种
            except (ValueError, TypeError):
                pass

        # 现金流
        if label in cf_map:
            key, parser = cf_map[label]
            try:
                v = parser(value)
                if is_negative and label in ["经营现金流", "投资现金流", "筹资现金流", "资本支出", "自由现金流"]:
                    v = -abs(v)
                record[key] = v
            except (ValueError, TypeError):
                pass

        # 质量指标
        if label in quality_map:
            key, parser = quality_map[label]
            try:
                record[key] = parser(value)
            except (ValueError, TypeError):
                pass

    return record if record.get("revenue") or record.get("net_income") else None


def calculate_cagr(first: float, last: float, years: int) -> Optional[float]:
    """计算复合年增长率 (CAGR)"""
    if first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def calculate_growth_rates(data_list: List[Dict]) -> List[Dict]:
    """计算相邻期间的增长率"""
    result = []
    for i, curr in enumerate(data_list):
        entry = {"period": curr.get("fiscal_period", "")}
        if i == 0:
            entry["revenue_growth"] = None
            entry["net_income_growth"] = None
            entry["fcf_growth"] = None
        else:
            prev = data_list[i - 1]
            # 营收增长率
            if prev.get("revenue") and curr.get("revenue") and prev["revenue"] > 0:
                entry["revenue_growth"] = (curr["revenue"] - prev["revenue"]) / prev["revenue"] * 100
            else:
                entry["revenue_growth"] = None
            # 净利润增长率
            if prev.get("net_income") and curr.get("net_income") and prev["net_income"] > 0:
                entry["net_income_growth"] = (curr["net_income"] - prev["net_income"]) / prev["net_income"] * 100
            else:
                entry["net_income_growth"] = None
            # FCF 增长率
            if prev.get("free_cash_flow") and curr.get("free_cash_flow") and prev["free_cash_flow"] > 0:
                entry["fcf_growth"] = (curr["free_cash_flow"] - prev["free_cash_flow"]) / prev["free_cash_flow"] * 100
            else:
                entry["fcf_growth"] = None
        result.append(entry)
    return result


def get_futu_snapshot(ticker: str) -> Optional[Dict]:
    """从 Futu OpenD 获取实时行情快照"""
    try:
        # 动态加载 futuapi common 模块
        futuapi_path = os.path.expanduser("~/.openclaw/skills/futuapi/scripts/common.py")
        spec = importlib.util.spec_from_file_location("_fc", futuapi_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_fc"] = mod
        spec.loader.exec_module(mod)

        ctx = mod.create_quote_context()
        try:
            symbol = ticker.split(".")[-1] if "." in ticker else ticker
            # 加市场前缀
            if not symbol.startswith("US.") and not symbol.startswith("HK."):
                symbol = f"US.{symbol}"

            ret, data = ctx.get_market_snapshot([symbol])
            if ret != 0 or data is None or data.empty:
                return None

            row = data.iloc[0]
            return {
                "code": str(row.get("code", "")),
                "name": str(row.get("name", "")),
                "last_price": float(row.get("last_price", 0) or 0),
                "pe_ttm": float(row.get("pe_ttm_ratio", 0) or 0),
                "pb_ratio": float(row.get("pb_ratio", 0) or 0),
                "eps": float(row.get("earning_per_share", 0) or 0),
                "net_asset_pershare": float(row.get("net_asset_per_share", 0) or 0),
                "total_mkt_val": float(row.get("total_market_val", 0) or 0),
                "dividend_ttm": float(row.get("dividend_ttm", 0) or 0),
                "dividend_ratio_ttm": float(row.get("dividend_ratio_ttm", 0) or 0),
                "issued_shares": float(row.get("issued_shares", 0) or 0),
                "outstanding_shares": float(row.get("outstanding_shares", 0) or 0),
            }
        finally:
            mod.safe_close(ctx)
    except Exception as e:
        print(f"⚠️  Futu 获取行情失败: {e}")
        return None


def calculate_valuation_metrics_futu(
    ticker: str, metrics: Dict, cash_flows: Dict, growth_rates: Optional[Dict] = None
) -> Dict:
    """使用 Futu + SEC 数据计算估值指标"""
    valuation = {}

    snap = get_futu_snapshot(ticker)
    if not snap:
        print("⚠️  无法获取 Futu 行情数据，跳过估值指标")
        return valuation

    stock_price = snap.get("last_price", 0)
    market_cap = snap.get("total_mkt_val", 0)
    shares_outstanding = snap.get("outstanding_shares", 0)

    if stock_price:
        valuation["stock_price"] = f"${stock_price:.2f}"
    if market_cap:
        valuation["market_cap"] = f"${market_cap / 1e9:.2f}B"
    if shares_outstanding:
        valuation["shares_outstanding"] = f"{shares_outstanding / 1e6:.1f}M"

    # PE (TTM) — 直接从 Futu 获取
    pe_ttm = snap.get("pe_ttm", 0)
    if pe_ttm and pe_ttm > 0:
        valuation["pe_ratio"] = f"{pe_ttm:.1f}"
        valuation["pe_ratio_num"] = pe_ttm

    # PB — 直接从 Futu 获取
    pb_ratio = snap.get("pb_ratio", 0)
    if pb_ratio and pb_ratio > 0:
        valuation["pb_ratio"] = f"{pb_ratio:.1f}"
        valuation["pb_ratio_num"] = pb_ratio

    # 每股净资产 — 直接从 Futu 获取
    bvps = snap.get("net_asset_pershare", 0)
    if bvps and bvps > 0:
        valuation["book_value_per_share"] = f"${bvps:.2f}"

    # EPS — 直接从 Futu 获取
    eps_ttm = snap.get("eps", 0)
    if eps_ttm and eps_ttm > 0:
        valuation["eps_ttm"] = f"${eps_ttm:.2f}"

    # 股息率 — 直接从 Futu 获取
    div_yield = snap.get("dividend_ratio_ttm", 0)
    if div_yield and div_yield > 0:
        valuation["dividend_yield"] = f"{div_yield:.2f}%"

    # 每股分红 — 直接从 Futu 获取
    div_per_share = snap.get("dividend_ttm", 0)
    if div_per_share and div_per_share > 0:
        valuation["dividend_per_share"] = f"${div_per_share:.2f}"

    # PS (市销率) = 市值 / 营收
    if market_cap and metrics.get("revenue") and metrics["revenue"] > 0:
        ps_ratio = market_cap / (metrics["revenue"] * 1e6)
        valuation["ps_ratio"] = f"{ps_ratio:.1f}"
        valuation["ps_ratio_num"] = ps_ratio

    # EV/EBITDA
    if market_cap and metrics.get("total_liabilities") and metrics.get("cash_equivalents"):
        ev = market_cap + metrics["total_liabilities"] * 1e6 - metrics["cash_equivalents"] * 1e6
        valuation["enterprise_value"] = f"${ev / 1e9:.2f}B"
        valuation["enterprise_value_num"] = ev

        if metrics.get("operating_income") and metrics["operating_income"] > 0:
            da = metrics.get("depreciation_amortization", 0) or 0
            ebitda = (metrics["operating_income"] + da) * 1e6
            if ebitda > 0:
                ev_ebitda = ev / ebitda
                valuation["ev_ebitda"] = f"{ev_ebitda:.1f}"
                valuation["ev_ebitda_num"] = ev_ebitda

    # FCF Yield = FCF / 市值 × 100%
    if cash_flows.get("free_cash_flow") and market_cap and market_cap > 0:
        fcf_yield = cash_flows["free_cash_flow"] * 1e6 / market_cap * 100
        valuation["fcf_yield"] = f"{fcf_yield:.1f}%"
        valuation["fcf_yield_num"] = fcf_yield

    # PEG = PE / 净利润增长率
    if pe_ttm and pe_ttm > 0 and growth_rates and growth_rates.get("net_income_growth"):
        ni_growth = growth_rates["net_income_growth"]
        if ni_growth and ni_growth > 0:
            peg = pe_ttm / ni_growth
            valuation["peg_ratio"] = f"{peg:.1f}"
            valuation["peg_ratio_num"] = peg

    return valuation


def _translate_once(text: str) -> str:
    """单次翻译：优先 Google，失败时回退 MyMemory。

    Google 的 /m 免费端点已改为 JS 渲染页面，deep-translator 的
    GoogleTranslator 解析不到结果会抛 TranslationNotFound（2026-09 实测），
    MyMemoryTranslator 仍可用。"""
    try:
        return GoogleTranslator(source='en', target='zh-CN').translate(text)
    except Exception:
        return MyMemoryTranslator(source='en-US', target='zh-CN').translate(text)


def translate_to_chinese(text: str, max_chunk: int = 4500) -> str:
    """翻译英文文本到中文"""
    if not TRANSLATOR_AVAILABLE:
        return text

    if not text or len(text.strip()) == 0:
        return text

    try:
        if len(text) <= max_chunk:
            return _translate_once(text)

        chunks = []
        sentences = re.split(r'(?<=[.!?])\s+', text)
        current_chunk = ""

        for sentence in sentences:
            if len(current_chunk) + len(sentence) + 1 <= max_chunk:
                current_chunk += sentence + " "
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence + " "

        if current_chunk:
            chunks.append(current_chunk.strip())

        translated_chunks = []
        for chunk in chunks:
            translated_chunks.append(_translate_once(chunk))

        return " ".join(translated_chunks)

    except Exception as e:
        print(f"⚠️  翻译失败: {e}")
        return text


def detect_filing_type(filename: str, text: str = None) -> str:
    """检测财报类型（10-K / 10-Q / 20-F）"""
    filename_lower = filename.lower()

    # 从文件名判断（最可靠）
    if "10k" in filename_lower or "10-k" in filename_lower:
        return "10-K"
    if "10q" in filename_lower or "10-q" in filename_lower:
        return "10-Q"
    # 特殊分支：外国私人发行人（PDD/BABA 等）申报 20-F
    if "20f" in filename_lower or "20-f" in filename_lower:
        return "20-F"
    # 6-K：外国私人发行人中期申报（盈利公告等），无 dei XBRL 标签
    if "6k" in filename_lower or "6-k" in filename_lower:
        return "6-K"

    # 从 XBRL 标签判断（最准确）
    if text:
        # 检查 DocumentFiscalPeriodFocus
        fiscal_match = re.search(r'dei:DocumentFiscalPeriodFocus[^>]*>(.*?)<', text, re.IGNORECASE)
        if fiscal_match:
            period = fiscal_match.group(1).strip()
            if period == "FY":
                return "10-K"
            if period.startswith("Q"):
                return "10-Q"

        # 检查 DocumentType（更准确）
        doc_type_match = re.search(r'dei:DocumentType[^>]*>(.*?)<', text, re.IGNORECASE)
        if doc_type_match:
            doc_type = doc_type_match.group(1).strip().upper()
            if "20-F" in doc_type:
                return "20-F"
            if "10-K" in doc_type:
                return "10-K"
            if "10-Q" in doc_type:
                return "10-Q"

        # 只检查文件开头的 FORM 声明（避免引用其他文件）
        first_5000 = text[:5000]
        if re.search(r"FORM\s+20-F", first_5000, re.IGNORECASE):
            return "20-F"
        if re.search(r"FORM\s+6-K", first_5000, re.IGNORECASE):
            return "6-K"
        if re.search(r"FORM\s+10-K", first_5000, re.IGNORECASE):
            return "10-K"
        if re.search(r"FORM\s+10-Q", first_5000, re.IGNORECASE):
            return "10-Q"

    # 文件名只有日期（如 msft-20230630.htm），根据日期推断
    # 微软财年是 7 月结束，6 月底的通常是 10-K
    date_match = re.search(r"(\d{4})(\d{2})\d{2}\.htm", filename)
    if date_match:
        month = int(date_match.group(2))
        if month == 6:  # 6月底通常是年报
            return "10-K"

    return "10-Q"  # 默认为 10-Q（大多数是季报）


# 季度词 → 季度序号（6-K 正文标题回退用）
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def extract_fiscal_period(
    filename: str,
    filing_type: str,
    fy_end_month: int = 12,
    text: str = None,
) -> str:
    """提取财年/季度标签。

    10-Q 季度按公司财年截止月（fy_end_month）推算，而非硬编码 6 月财年：
      fy = year if month <= fy_end_month else year + 1
    无 8 位日期的文件（如 PDD 的 6-K 盈利公告 PDD_6K_2026-08-25.htm）
    回退到正文标题（"Second Quarter 2026 Unaudited Financial Results"）。
    """
    # 6-K：文件名无可靠期间信息，正文标题优先
    if filing_type == "6-K" and text:
        m = re.search(
            r"(First|Second|Third|Fourth)\s+Quarter\s+(?:of\s+)?(\d{4})",
            text[:20000], re.IGNORECASE)
        if m:
            q = _QUARTER_WORDS[m.group(1).lower()]
            return f"{m.group(2)}Q{q}"

    # 匹配日期模式：msft-20230630.htm / msft-10k_20220630.htm / pdd-20251231x20f.htm
    date_match = re.search(r"(\d{8})[^.]*\.htm", filename)
    if date_match:
        date_str = date_match.group(1)
        year = int(date_str[:4])
        month = int(date_str[4:6])

        if filing_type in ("10-K", "20-F"):
            return f"FY{year}"
        else:
            # 10-Q: 报告期末所属财年 + 相对财年末的偏移定季度
            # 4-4-5 财历（如 PFE 季末 4/3、7/3、10/2）偏移非整月，按最近季度归类
            fy = year if month <= fy_end_month else year + 1
            quarter_idx = round(((month - fy_end_month) % 12) / 3) % 4
            q = {0: 4, 1: 1, 2: 2, 3: 3}[quarter_idx]
            return f"{fy}Q{q}"

    return "Unknown"


def generate_extraction_md(
    ticker: str,
    filing_type: str,
    fiscal_period: str,
    sections: Dict,
    metrics: Dict,
    cash_flows: Dict,
    detailed_items: Dict,
    force: bool = False,
    quality_warnings: Optional[List[str]] = None,
) -> str:
    """生成提取文件的 Markdown 内容"""
    output_dir = os.path.join(ANALYSIS_DIR, ticker.upper())
    os.makedirs(output_dir, exist_ok=True)

    output_filename = f"{filing_type}_{fiscal_period}.md"
    output_path = os.path.join(output_dir, output_filename)

    # 检查是否已存在（--force 时强制重新生成）
    if os.path.exists(output_path) and not force:
        print(f"   ⏭️  已存在，跳过: {output_filename}")
        return output_path

    # 生成 Markdown 内容
    lines = []
    lines.append(f"# {ticker.upper()} {filing_type} {fiscal_period}")
    lines.append(f"\n提取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    # 生成器版本号：入库时解析该行写 generator_version 列，无该行视为 legacy 旧数据
    lines.append(f"生成器版本: analysis-{ANALYSIS_VERSION}\n")

    # 财务指标表格
    lines.append("## 财务指标\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|:---|:---|")

    key_metrics = [
        ("revenue", "营收"),
        ("gross_profit", "毛利润"),
        ("gross_margin", "毛利率"),
        ("operating_income", "营业利润"),
        ("operating_margin", "营业利润率"),
        ("net_income", "净利润"),
        ("net_margin", "净利率"),
        ("eps", "每股收益"),
        ("total_assets", "总资产"),
        ("total_liabilities", "总负债"),
        ("stockholders_equity", "股东权益"),
        ("current_assets", "流动资产"),
        ("current_liabilities", "流动负债"),
        ("cash_equivalents", "现金及等价物"),
        ("inventories", "存货"),
        ("interest_expense", "利息费用"),
        ("goodwill", "商誉"),
        ("debt_to_asset_ratio", "资产负债率"),
        ("current_ratio", "流动比率"),
        ("quick_ratio", "速动比率"),
        ("interest_coverage", "利息覆盖倍数"),
        ("goodwill_to_assets", "商誉占比"),
        ("roe", "净资产收益率"),
        ("primary_revenue", "主营营收"),
        ("primary_cost", "主营成本"),
        ("primary_profit", "主营利润"),
        ("primary_margin", "主营利润率"),
    ]

    for key, label in key_metrics:
        if key in metrics:
            lines.append(f"| {label} | {metrics[key]} |")

    # 封面流通股数（原始股数 → 百万股展示，DCF 每股估值兜底数据源）
    if "shares_outstanding_num" in metrics:
        lines.append(f"| 流通股数 | {metrics['shares_outstanding_num'] / 1e6:,.1f}M |")

    # 现金流表格（10-Q 现金流量表仅披露 YTD 累计值，需明示口径避免误读为单季）
    cf_title = "现金流（YTD 累计）" if filing_type == "10-Q" else "现金流"
    lines.append(f"\n## {cf_title}\n")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|:---|")

    cf_items = [
        ("operating", "经营现金流"),
        ("investing", "投资现金流"),
        ("financing", "筹资现金流"),
        ("capex", "资本支出"),
        ("free_cash_flow", "自由现金流"),
        ("depreciation_amortization", "折旧摊销"),
    ]

    for key, label in cf_items:
        if key in cash_flows:
            lines.append(f"| {label} | {cash_flows[key]} |")

    # 现金流质量指标
    lines.append("\n## 现金流质量指标\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|:---|:---|")

    if "cfo_to_net_income" in cash_flows:
        lines.append(f"| CFO/净利润 | {cash_flows['cfo_to_net_income']} |")
    if "fcf_margin" in cash_flows:
        lines.append(f"| FCF利润率 | {cash_flows['fcf_margin']} |")
    if "capex_to_revenue" in cash_flows:
        lines.append(f"| CapEx/营收 | {cash_flows['capex_to_revenue']} |")

    # 详细利润项目
    if detailed_items:
        lines.append("\n## 详细利润项目\n")
        lines.append("| 项目 | 数值 |")
        lines.append("|:---|:---|")
        for key, value in detailed_items.items():
            lines.append(f"| {key} | {value} |")

    # 章节内容
    lines.append("\n## 章节内容\n")
    for section_type, section_data in sections.items():
        if section_data.get("found"):
            content = section_data.get("content", "")
            if content:
                # 截断过长的内容
                if len(content) > 5000:
                    content = content[:5000] + "..."
                lines.append(f"### {section_type}\n")
                lines.append(content)
                lines.append("")

    # ====== 必填字段缺失检查 ======
    missing_metrics = []
    for field_key, field_label in REQUIRED_METRIC_FIELDS:
        if field_key not in metrics:
            missing_metrics.append(field_label)

    missing_cashflows = []
    for field_key, field_label in REQUIRED_CASHFLOW_FIELDS:
        if field_key not in cash_flows:
            missing_cashflows.append(field_label)

    if missing_metrics or missing_cashflows or quality_warnings:
        lines.append("\n## ⚠️ 数据质量警告\n")
        lines.append("> 以下字段缺失或数据异常，可能影响分析准确性（异常字段已置空，不做默认值/模拟值）：\n")

        if quality_warnings:
            for w in quality_warnings:
                lines.append(f"**数据异常**: {w}")
                lines.append("")

        if missing_metrics:
            lines.append(f"**缺失财务指标**: {', '.join(missing_metrics)}")
            lines.append("")

        if missing_cashflows:
            lines.append(f"**缺失现金流字段**: {', '.join(missing_cashflows)}")
            lines.append("")

        lines.append("---")

    # 写入文件
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path


def _fmt(v, suffix="", prefix="$"):
    """格式化数值：None → '-'"""
    if v is None:
        return "-"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{prefix}{v:,.0f}{suffix}"
        return f"{prefix}{v:.1f}{suffix}"
    if isinstance(v, int):
        return f"{prefix}{v:,}{suffix}"
    return str(v)


def generate_analysis_report(ticker: str, force: bool = False) -> str:
    """生成聚合分析报告: {TICKER}_analysis_{YYYYMMDD}.md"""
    ticker = ticker.upper()
    analysis_dir = os.path.join(ANALYSIS_DIR, ticker)
    if not os.path.exists(analysis_dir):
        print(f"❌ 未找到 {ticker} 的分析数据")
        return ""

    # 读取所有提取文件
    md_files = sorted([f for f in os.listdir(analysis_dir) if f.endswith(".md")])
    if not md_files:
        print(f"❌ {ticker} 没有提取文件")
        return ""

    all_data = []
    for md_file in md_files:
        record = parse_extraction_md(os.path.join(analysis_dir, md_file))
        if record:
            # Fallback: 总负债 = 总资产 - 股东权益（部分公司没有单独 Total liabilities 行）
            if "total_liabilities" not in record and "total_assets" in record and "stockholders_equity" in record:
                record["total_liabilities"] = record["total_assets"] - record["stockholders_equity"]
            all_data.append(record)

    if not all_data:
        print(f"❌ 无法解析 {ticker} 的提取文件")
        return ""

    # 分离年报和季报
    # 年报/季报：外国私人发行人用 20-F（年报）与 6-K（中期）替代 10-K/10-Q
    annual = [d for d in all_data if d.get("filing_type") in ("10-K", "20-F")]
    quarterly = [d for d in all_data if d.get("filing_type") in ("10-Q", "6-K")]

    # 取最新一期的数据用于估值
    latest = all_data[-1]

    lines = []
    lines.append(f"# {ticker} 企业财报分析\n")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ── 年度趋势 ──
    if annual:
        lines.append("## 年度趋势\n")
        lines.append("| 期间 | 营收 | 毛利润 | 毛利率 | 营业利润 | 营业利润率 | 净利润 | 净利率 | EPS | 总资产 | 总负债 | 股东权益 |")
        lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
        for d in annual:
            period = d.get("fiscal_period", "?")
            cur = d.get("currency", "$")
            lines.append(
                f"| {d.get('filing_type', '')}_{period} "
                f"| {_fmt(d.get('revenue'), 'M', cur)} "
                f"| {_fmt(d.get('gross_profit'), 'M', cur)} "
                f"| {_fmt(d.get('gross_margin'), '%', '')} "
                f"| {_fmt(d.get('operating_income'), 'M', cur)} "
                f"| {_fmt(d.get('operating_margin'), '%', '')} "
                f"| {_fmt(d.get('net_income'), 'M', cur)} "
                f"| {_fmt(d.get('net_margin'), '%', '')} "
                f"| {_fmt(d.get('eps'), '', cur)} "
                f"| {_fmt(d.get('total_assets'), 'M', cur)} "
                f"| {_fmt(d.get('total_liabilities'), 'M', cur)} "
                f"| {_fmt(d.get('stockholders_equity'), 'M', cur)} |"
            )

        # 3年 CAGR
        if len(annual) >= 2:
            lines.append("\n### 3年复合增长率 (CAGR)\n")
            years = min(3, len(annual) - 1)
            first, last = annual[-(years + 1)], annual[-1]

            rev_cagr = calculate_cagr(first.get("revenue", 0), last.get("revenue", 0), years)
            ni_cagr = calculate_cagr(first.get("net_income", 0), last.get("net_income", 0), years)
            gp_cagr = calculate_cagr(first.get("gross_profit", 0), last.get("gross_profit", 0), years)

            parts = []
            if rev_cagr is not None:
                parts.append(f"营收: {rev_cagr * 100:.1f}%")
            if ni_cagr is not None:
                parts.append(f"净利润: {ni_cagr * 100:.1f}%")
            if gp_cagr is not None:
                parts.append(f"毛利润: {gp_cagr * 100:.1f}%")
            lines.append(" | ".join(parts) + "\n")

        # 成长性指标
        growth_data = calculate_growth_rates(annual)
        has_growth = any(
            g.get("revenue_growth") is not None or g.get("net_income_growth") is not None
            for g in growth_data
        )
        if has_growth:
            lines.append("### 成长性指标\n")
            lines.append("| 期间 | 营收增长率 | 净利润增长率 | FCF增长率 |")
            lines.append("|:---|:---:|:---:|:---:|")
            for g in growth_data:
                period = g.get("period", "")
                rev_g = f"{g['revenue_growth']:.1f}%" if g.get("revenue_growth") is not None else "-"
                ni_g = f"{g['net_income_growth']:.1f}%" if g.get("net_income_growth") is not None else "-"
                fcf_g = f"{g['fcf_growth']:.1f}%" if g.get("fcf_growth") is not None else "-"
                lines.append(f"| {d.get('filing_type', '')}_{period} | {rev_g} | {ni_g} | {fcf_g} |")
            lines.append("")

    # ── 季度趋势 ──
    if quarterly:
        lines.append("## 季度趋势\n")
        lines.append("| 期间 | 营收 | 毛利润 | 毛利率 | 营业利润 | 营业利润率 | 净利润 | 净利率 | EPS |")
        lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
        for d in quarterly:
            period = d.get("fiscal_period", "?")
            cur = d.get("currency", "$")
            lines.append(
                f"| {d.get('filing_type', '')}_{period} "
                f"| {_fmt(d.get('revenue'), 'M', cur)} "
                f"| {_fmt(d.get('gross_profit'), 'M', cur)} "
                f"| {_fmt(d.get('gross_margin'), '%', '')} "
                f"| {_fmt(d.get('operating_income'), 'M', cur)} "
                f"| {_fmt(d.get('operating_margin'), '%', '')} "
                f"| {_fmt(d.get('net_income'), 'M', cur)} "
                f"| {_fmt(d.get('net_margin'), '%', '')} "
                f"| {_fmt(d.get('eps'), '', cur)} |"
            )
        lines.append("")

    # ── 财务健康指标（取最新一期）──
    lines.append("## 财务健康指标\n")
    lines.append("| 指标 | 数值 | 警戒线 | 说明 |")
    lines.append("|:---|:---|:---:|:---|")

    health_items = [
        ("debt_to_asset_ratio", "资产负债率", "%", ">70%", "负债占总资产比例"),
        ("current_ratio", "流动比率", "", "<1.0", "短期偿债能力"),
        ("quick_ratio", "速动比率", "", "<0.5", "更严格的短期偿债能力"),
        ("interest_coverage", "利息覆盖倍数", "x", "<3x", "偿债压力指标"),
        ("goodwill_to_assets", "商誉占比", "%", ">20%", "高商誉 = 减值风险"),
    ]
    for key, label, suffix, warn, desc in health_items:
        v = latest.get(key)
        if v is not None:
            lines.append(f"| {label} | {_fmt(v, suffix, '')} | {warn} | {desc} |")
    lines.append("")

    # ── 现金流质量指标 ──
    lines.append("## 现金流质量指标\n")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("|:---|:---|:---|")

    quality_items = [
        ("cfo_to_net_income", "CFO/净利润", "长期看应 ≥ 1.0"),
        ("fcf_margin", "FCF利润率", "自由现金流占营收比例"),
        ("capex_to_revenue", "CapEx/营收", "资本支出强度"),
    ]
    for key, label, desc in quality_items:
        v = latest.get(key)
        if v is not None:
            suffix = "%" if "margin" in key or "revenue" in key else ""
            lines.append(f"| {label} | {_fmt(v, suffix, '')} | {desc} |")
    lines.append("")

    # ── 现金流趋势 ──
    if annual:
        lines.append("## 现金流趋势\n")
        lines.append("| 期间 | 经营现金流 | 投资现金流 | 筹资现金流 | 资本支出 | 折旧摊销 | 自由现金流 |")
        lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
        for d in annual:
            period = d.get("fiscal_period", "?")
            cur = d.get("currency", "$")
            lines.append(
                f"| {d.get('filing_type', '')}_{period} "
                f"| {_fmt(d.get('operating_cf'), 'M', cur)} "
                f"| {_fmt(d.get('investing_cf'), 'M', cur)} "
                f"| {_fmt(d.get('financing_cf'), 'M', cur)} "
                f"| {_fmt(d.get('capex'), 'M', cur)} "
                f"| {_fmt(d.get('depreciation_amortization'), 'M', cur)} "
                f"| {_fmt(d.get('free_cash_flow'), 'M', cur)} |"
            )
        lines.append("")

    # ── 公司概况/风险因素/管理层展望 ──
    # 从最新的 10-K 提取文件读取章节内容
    latest_annual = annual[-1] if annual else latest
    latest_source = latest_annual.get("source", "")
    if latest_source:
        source_path = os.path.join(analysis_dir, latest_source)
        if os.path.exists(source_path):
            with open(source_path, "r", encoding="utf-8") as f:
                source_content = f.read()

            for section_name, section_label in [
                ("### financial_statements", "公司概况"),
                ("### risk_factors", "风险因素"),
                ("### mda", "管理层展望"),
            ]:
                # 查找章节内容
                start = source_content.find(section_name)
                if start >= 0:
                    # 找到章节标题后的第一个换行
                    content_start = source_content.find("\n", start) + 1
                    # 找到下一个 ### 或文件结尾
                    next_section = source_content.find("\n### ", content_start)
                    if next_section < 0:
                        next_section = len(source_content)
                    section_text = source_content[content_start:next_section].strip()
                    if section_text and len(section_text) > 50:
                        # 截断过长内容
                        if len(section_text) > 3000:
                            section_text = section_text[:3000] + "..."
                        lines.append(f"## {section_label}\n")
                        lines.append(section_text)
                        lines.append("")

    # ── 估值指标 ──
    lines.append("## 估值指标\n")

    # 计算增长率用于 PEG
    latest_growth = {}
    if annual and len(annual) >= 2:
        prev = annual[-2]
        curr = annual[-1]
        if prev.get("net_income") and curr.get("net_income") and prev["net_income"] > 0:
            latest_growth["net_income_growth"] = (curr["net_income"] - prev["net_income"]) / prev["net_income"] * 100

    valuation = calculate_valuation_metrics_futu(ticker, latest, latest, latest_growth)

    if valuation:
        lines.append("| 指标 | 数值 |")
        lines.append("|:---|:---|")
        val_items = [
            ("stock_price", "股价"),
            ("market_cap", "市值"),
            ("pe_ratio", "市盈率 (PE)"),
            ("pb_ratio", "市净率 (PB)"),
            ("ps_ratio", "市销率 (PS)"),
            ("peg_ratio", "PEG"),
            ("ev_ebitda", "EV/EBITDA"),
            ("enterprise_value", "企业价值 (EV)"),
            ("fcf_yield", "FCF收益率"),
            ("dividend_yield", "股息率"),
            ("book_value_per_share", "每股净资产"),
            ("eps_ttm", "EPS (TTM)"),
            ("dividend_per_share", "每股分红"),
            ("shares_outstanding", "流通股数"),
        ]
        for key, label in val_items:
            if key in valuation:
                lines.append(f"| {label} | {valuation[key]} |")
    else:
        lines.append("*无法获取估值数据*\n")

    # 写入文件
    output_filename = f"{ticker}_analysis_{datetime.now().strftime('%Y%m%d')}.md"
    output_path = os.path.join(ANALYSIS_DIR, output_filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n📊 聚合分析报告已保存: {output_path}")
    return output_path
