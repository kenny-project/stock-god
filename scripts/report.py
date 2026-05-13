#!/usr/bin/env python3
"""
价值投资分析报告生成器 v0.9
v0.8 改进：
  - Alpha Vantage 配额耗尽时，明确标注"数据不可用"，不再静默跳过
  - 新增 Finviz 数据源：分析师评级 + 机构持股 + 做空数据
  - 缓存 key 强制包含完整 symbol（含市场前缀），避免跨股票污染

数据源优先级（2026-04-25）：
  VIX              → FMP API              → 缓存 5 分钟
  实时行情（主）   → Futu OpenD           → 不缓存
  实时行情（备）   → 腾讯财经             → 不缓存
  K线              → Futu OpenD           → 不缓存
  财务年/季报      → FMP API（主）       → 缓存 1 小时
  分红历史         → FMP API             → 缓存 1 小时
  Key Metrics      → FMP API             → 缓存 1 小时
  分析师评级/机构持仓/Beta/毛利率/负债率 → Finviz → 缓存 1 小时
  EPS surprise/F-Score/Balance Sheet    → Alpha Vantage（辅）→ 缓存 1 小时

用法：
    python3 stock_analysis_report.py US.NKE
    python3 stock_analysis_report.py HK.00700 --no-cache
"""

import argparse, sys, os, time, urllib.request, json, re, io, subprocess
import importlib.util, hashlib
from datetime import datetime

# ── futuapi common ─────────────────────────────────────
_SPEC = importlib.util.spec_from_file_location(
    "_fc", "/Users/wmh/.openclaw/skills/futuapi/scripts/common.py")
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["_fc"] = _MOD; _SPEC.loader.exec_module(_MOD)
create_quote_context = _MOD.create_quote_context
safe_close = _MOD.safe_close
from futu import RET_OK, SubType, KLType, AuType

# ── API Keys ───────────────────────────────────────────
AV_KEY  = "UQ3XI876M9S3PKND"
AV_URL  = "https://www.alphavantage.co/query"
FMP_KEY = "OeyYwkTOzQUfkywmNu8p0NFIP1pTSv6x"
FMP_URL = "https://financialmodelingprep.com/stable"

# ── 缓存目录 ───────────────────────────────────────────
CACHE_DIR = os.path.expanduser("~/.openclaw/cache/stock-analysis/")
os.makedirs(CACHE_DIR, exist_ok=True)
SEC_FILINGS_DIR = os.path.expanduser("~/.openclaw/reports/sec_filings/")


def _cache_path(namespace, key):
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"{namespace}_{h}.json")

def cache_get(namespace, key):
    p = _cache_path(namespace, key)
    if not os.path.exists(p):
        return None, False
    try:
        with open(p) as f:
            entry = json.load(f)
        if time.time() - entry.get("_ts", 0) > entry.get("_ttl", 0):
            return None, False
        return entry.get("data"), True
    except Exception:
        return None, False

def cache_set(namespace, key, data, ttl):
    p = _cache_path(namespace, key)
    try:
        with open(p, "w") as f:
            json.dump({"data": data, "_ts": time.time(), "_ttl": ttl, "_key": key}, f)
    except Exception as e:
        print(f"[WARN] cache write error: {e}")


# ── 数据获取函数 ───────────────────────────────────────

def av_get(function, symbol, use_cache=True, ttl=3600):
    """Alpha Vantage 数据获取（历史财务专用）
    返回 (data, rate_limited)
    - rate_limited=True 表示配额耗尽，数据不可用
    """
    cache_key = f"{function}_{symbol}"          # symbol 本身已含市场前缀，如 US.NKE
    if use_cache:
        data, hit = cache_get("av", cache_key)
        if hit:
            print(f"  [CACHE HIT] {function}({symbol})")
            # 缓存命中时假设未限流（缓存中不会存限流标记）
            return data, False

    url = f"{AV_URL}?function={function}&symbol={symbol}&apikey={AV_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read())
            if "Note" in raw or "Information" in raw:
                print(f"  [AV RATE LIMIT] {function}({symbol}) — 今日配额已耗尽")
                return None, True           # ← 明确标记限流
            if use_cache:
                cache_set("av", cache_key, raw, ttl)
            return raw, False
    except Exception as e:
        print(f"[WARN] AV {function}: {e}"); return None, False


def common_get_vix(use_cache=True, ttl=300):
    """VIX 恐慌指数（FMP），缓存 5 分钟"""
    cache_key = "VIX_^VIX"
    if use_cache:
        data, hit = cache_get("fmp", cache_key)
        if hit:
            print(f"  [CACHE HIT] VIX")
            return data
    try:
        url = f"{FMP_URL}/quote?symbol=%5EVIX&apikey={FMP_KEY}"
        with urllib.request.urlopen(url, timeout=10) as r:
            raw = json.loads(r.read())
            if isinstance(raw, list) and raw:
                v = raw[0]
                data = dict(
                    price=float(v.get('price',0)),
                    chg=float(v.get('change',0)),
                    chg_pct=float(v.get('changePercentage',0)),
                    day_h=float(v.get('dayHigh',0)),
                    day_l=float(v.get('dayLow',0)),
                    yr_h=float(v.get('yearHigh',0)),
                    yr_l=float(v.get('yearLow',0)),
                    avg50=float(v.get('priceAvg50',0)),
                    avg200=float(v.get('priceAvg200',0)))
                if use_cache:
                    cache_set("fmp", cache_key, data, ttl)
                return data
    except Exception as e:
        print(f"[WARN] VIX: {e}")
    return None


# ── FMP API（主力财务数据）───────────────────────────────
def fmp_get(endpoint, symbol, use_cache=True, ttl=3600):
    """FMP API 数据获取（income-statement / balance-sheet / key-metrics / dividend-history）
    返回 (data, error)
    """
    cache_key = f"{endpoint}_{symbol}"
    if use_cache:
        data, hit = cache_get("fmp", cache_key)
        if hit:
            print(f"  [CACHE HIT] FMP {endpoint}({symbol})")
            return data, None

    # symbol 处理：FMP 美股直接用 ticker，港股用 HK:00700 格式
    fmp_sym = symbol
    url = f"{FMP_URL}/{endpoint}?symbol={fmp_sym}&apikey={FMP_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read())
            # FMP free tier 会返回 list 或 {"financials": [...]}
            if isinstance(raw, dict) and raw.get("error"):
                print(f"  [FMP WARN] {endpoint}({symbol}): {raw.get('error')}")
                return None, raw.get("error")
            if use_cache:
                cache_set("fmp", cache_key, raw, ttl)
            return raw, None
    except Exception as e:
        print(f"[WARN] FMP {endpoint}({symbol}): {e}")
        return None, str(e)


def fmp_get_income(symbol, use_cache=True, ttl=3600):
    """FMP 财报（收入/利润），返回 annual + quarterly 列表
    FMP 格式：date, revenue, netIncome, grossProfit, operatingIncome
    AV 格式：fiscalDateEnding, totalRevenue, netIncome, grossProfit
    """
    raw, err = fmp_get("income-statement", symbol, use_cache, ttl)
    if err or not raw:
        return {}, {}
    if isinstance(raw, list):
        # 转换为 AV 格式
        def convert(r):
            return {
                "fiscalDateEnding": r.get("date",""),
                "totalRevenue":     r.get("revenue",0),
                "netIncome":        r.get("netIncome",0),
                "grossProfit":      r.get("grossProfit",0),
            }
        ann = [convert(r) for r in raw if r.get("date")]
        return {"annualReports": ann}, {"quarterlyReports": []}
    annual = raw.get("annual") or raw.get("data", {}).get("annual") or []
    quarterly = raw.get("quarterly") or raw.get("data", {}).get("quarterly") or []
    return {"annualReports": annual}, {"quarterlyReports": quarterly}


def fmp_get_balance(symbol, use_cache=True, ttl=3600):
    """FMP 资产负债表，返回 annual + quarterly 列表
    FMP 格式：date, totalAssets, totalLiabilities, totalEquity
    AV 格式：fiscalDateEnding, totalAssets, totalLiabilities, totalShareholderEquity
    """
    raw, err = fmp_get("balance-sheet-statement", symbol, use_cache, ttl)
    if err or not raw:
        return {}, {}
    if isinstance(raw, list):
        def convert(r):
            return {
                "fiscalDateEnding":           r.get("date",""),
                "totalAssets":                r.get("totalAssets",0),
                "totalLiabilities":           r.get("totalLiabilities",0),
                "totalShareholderEquity":     r.get("totalEquity", r.get("totalStockholdersEquity",0)),
            }
        ann = [convert(r) for r in raw if r.get("date")]
        return {"annualReports": ann}, {"quarterlyReports": []}
    annual = raw.get("annual") or raw.get("data", {}).get("annual") or []
    quarterly = raw.get("quarterly") or raw.get("data", {}).get("quarterly") or []
    return {"annualReports": annual}, {"quarterlyReports": quarterly}


def fmp_get_key_metrics(symbol, use_cache=True, ttl=3600):
    """FMP Key Metrics（PE、分红、ROE、ROA、毛利率等），返回列表"""
    raw, err = fmp_get("key-metrics", symbol, use_cache, ttl)
    if err or not raw:
        return []
    if isinstance(raw, list):
        return raw
    return raw.get("quarterly") or []


def fmp_get_dividends(symbol, use_cache=True, ttl=3600):
    """FMP 分红历史，返回列表"""
    raw, err = fmp_get("dividends", symbol, use_cache, ttl)
    if err or not raw:
        return []
    if isinstance(raw, list):
        return raw
    return []


# ── 腾讯财经备用行情 ────────────────────────────────────
def common_get_tencent_quote(symbol):
    """从腾讯财经获取实时行情（备用）
    支持 US.NKE、HK.00700 格式
    返回 dict 或 None
    """
    try:
        mkt, code = (symbol.split(".", 1) + [None])[:2]
        if mkt == "US":
            qq_url = f"https://qt.gtimg.cn/q=us{code.upper()}"
        elif mkt == "HK":
            qq_url = f"https://qt.gtimg.cn/q=hk{code}"
        else:
            return None
        req = urllib.request.Request(qq_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            text = r.read().decode("gbk", errors="replace")
        # 腾讯格式：v_usNKE="100,285.12,281.23,282.00,281.50,281.80,..."
        m = re.search(r'="([^"]+)"', text)
        if not m:
            return None
        fields = m.group(1).split(",")
        if len(fields) < 10:
            return None
        if mkt == "US":
            # 字段：0=名称 1=现价 2=昨收 3=开盘 4=最高 5=最低 ...
            return {
                "name":  fields[1],
                "price": float(fields[1]) if fields[1] else 0,
                "prev":  float(fields[2]) if fields[2] else 0,
                "open":  float(fields[3]) if fields[3] else 0,
                "high":  float(fields[4]) if fields[4] else 0,
                "low":   float(fields[5]) if fields[5] else 0,
                "vol":   int(fields[6]) if fields[6] else 0,
            }
        else:  # HK
            # 字段：0=名称 1=现价 2=昨收 3=开盘 4=最高 5=最低 ...
            return {
                "name":  fields[1],
                "price": float(fields[2]) if fields[2] else 0,
                "prev":  float(fields[3]) if fields[3] else 0,
                "open":  float(fields[4]) if fields[4] else 0,
                "high":  float(fields[5]) if fields[5] else 0,
                "low":   float(fields[6]) if fields[6] else 0,
                "vol":   int(fields[7]) if fields[7] else 0,
            }
    except Exception as e:
        print(f"[WARN] Tencent quote({symbol}): {e}")
        return None


def common_futu_snapshot(ctx, symbol, use_cache=True, ttl=300):
    """Futu get_market_snapshot 获取基本信息，缓存 5 分钟"""
    cache_key = f"snapshot_{symbol}"
    if use_cache:
        data, hit = cache_get("futu", cache_key)
        if hit:
            print(f"  [CACHE HIT] Futu Snapshot({symbol})")
            return data
    ret, df = ctx.get_market_snapshot([symbol])
    if ret != RET_OK or df is None or df.empty:
        return None
    r = df.iloc[0]
    data = {
        'code':            str(r.get('code', symbol)),
        'name':            str(r.get('name', '')),
        'last_price':      float(r.get('last_price') or 0),
        'prev_close':      float(r.get('prev_close_price') or 0),
        'open_price':      float(r.get('open_price') or 0),
        'high_price':      float(r.get('high_price') or 0),
        'low_price':       float(r.get('low_price') or 0),
        'volume':          int(r.get('volume') or 0),
        'pe_ttm':          float(r.get('pe_ttm_ratio') or 0) or None,
        'pe_ratio':        float(r.get('pe_ratio') or 0) or None,
        'pb_ratio':        float(r.get('pb_ratio') or 0) or None,
        'eps':             float(r.get('earning_per_share') or 0) or None,
        'net_asset_pershare': float(r.get('net_asset_per_share') or 0) or None,
        'net_asset':        float(r.get('net_asset') or 0) or None,
        'net_profit':      float(r.get('net_profit') or 0) or None,
        'total_mkt_val':   float(r.get('total_market_val') or 0) or None,
        'circular_mkt_val': float(r.get('circular_market_val') or 0) or None,
        'dividend_ttm':    float(r.get('dividend_ttm') or 0) or None,
        'dividend_ratio_ttm': float(r.get('dividend_ratio_ttm') or 0) or None,
        'dividend_lfy':    float(r.get('dividend_lfy') or 0) or None,
        'highest52wk':     float(r.get('highest52weeks_price') or 0) or None,
        'lowest52wk':      float(r.get('lowest52weeks_price') or 0) or None,
        'issued_shares':   float(r.get('issued_shares') or 0) or None,
        'outstanding_shares': float(r.get('outstanding_shares') or 0) or None,
    }
    if use_cache:
        cache_set("futu", cache_key, data, ttl)
    return data


def finviz_get(symbol, use_cache=True, ttl=3600):
    """从 Finviz 获取分析师评级 + 机构持股 + 做空数据
    返回 dict，失败返回 {}
    """
    mkt, code = (symbol.split(".", 1) + [None])[:2]
    ticker = code or symbol
    finviz_ticker = ticker.upper()

    cache_key = f"finviz_{symbol}"
    if use_cache:
        data, hit = cache_get("finviz", cache_key)
        if hit:
            print(f"  [CACHE HIT] Finviz({symbol})")
            return data

    try:
        url = f"https://finviz.com/quote.ashx?t={finviz_ticker}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")

        # 解析 snapshot table：支持两种格式
        # 格式A（非链接标签）: <div class="snapshot-td-label">LABEL</div></td><td>...<b>VALUE</b>
        # 格式B（链接标签）: <a>LABEL</a></div></td><td>...<a>...<b>VALUE</b>
        def extract(label):
            p1 = r'<div class="snapshot-td-label">' + re.escape(label) + r'</div></td><td[^>]*>[^<]*<div class="snapshot-td-content"><b>([^<]+)</b>'
            p2 = re.escape(label) + r'(?:</a>)?</div></td><td[^>]*>[^<]*<div[^>]*><(?:a[^>]*>)?<b>([^<]+)</b>'
            for p in [p1, p2]:
                m = re.search(p, html)
                if m:
                    return m.group(1).strip()
            return None

        result = {}

        # 机构持仓
        v = extract("Inst Own")
        if v: result["inst_own"] = v
        v = extract("Insider Own")
        if v: result["ins_own"] = v

        # 做空数据
        v = extract("Short Float")
        if v: result["short_float"] = v
        v = extract("Short Ratio")
        if v: result["short_ratio"] = v

        # 分析师综合评级
        v = extract("Recom")
        if v: result["recom"] = v

        # 市值 / 估值
        v = extract("Market Cap")
        if v: result["market_cap"] = v
        v = extract("P/E")
        if v: result["pe"] = v
        v = extract("EPS (ttm)")
        if v: result["eps"] = v
        v = extract("Sales")
        if v: result["sales"] = v
        v = extract("P/S")
        if v: result["ps"] = v
        v = extract("P/B")
        if v: result["pb"] = v
        v = extract("Dividend Est.")
        if v: result["dividend_est"] = v
        v = extract("Beta")
        if v: result["beta"] = v
        v = extract("Avg Volume")
        if v: result["avg_volume"] = v
        v = extract("Employees")
        if v: result["employees"] = v

        # 板块/行业
        v = extract("Sector")
        if v: result["sector"] = v
        v = extract("Industry")
        if v: result["industry"] = v

        # 估值 / 增长
        v = extract("EPS (ttm)")
        if v: result["eps"] = v
        v = extract("Sales")
        if v: result["sales"] = v
        v = extract("EPS Growth")
        if v: result["eps_growth"] = v
        v = extract("Revenue Growth")
        if v: result["rev_growth"] = v

        # 盈利能力（Finviz 有 GP、A直接算）
        v = extract("Gross Margin")
        if v: result["gross_margin"] = v
        v = extract("Operating Margin")
        if v: result["op_margin"] = v
        v = extract("Profit Margin")
        if v: result["net_margin"] = v

        # 财务健康
        v = extract("Debt")
        if v: result["debt"] = v
        v = extract("Debt/Equity")
        if v: result["de_ ratio"] = v
        v = extract("Current Ratio")
        if v: result["current_ratio"] = v

        # 解析分析师评级变化表格（日期 / 行动 / 机构 / 评级 / 目标价）
        ratings = []
        rating_pattern = re.findall(
            r'<tr[^>]*><td[^>]*>(\w{3}-\d{2}-\d{2})</td>'
            r'<td[^>]*>(?:<span[^>]*>)?([^<]+?)(?:</span>)?</td>\s*'
            r'<td[^>]*>([^<]+)</td>\s*'
            r'<td[^>]*>([^<]+)</td>\s*'
            r'<td[^>]*(?:class="[^"]*\s)?tabular-nums[^>]*>([^<]+)</td>',
            html)
        for row in rating_pattern[:10]:
            date_str, action, firm, rating_ch, target_ch = row
            ratings.append({
                "date": date_str.strip(),
                "action": action.strip(),
                "firm": firm.strip(),
                "rating": rating_ch.strip().replace('&rarr;', '→'),
                "target": target_ch.strip().replace('&rarr;', '→')
            })
        result["ratings"] = ratings

        if ratings:
            print(f"  [Finviz OK] {symbol} — 分析师评级 {len(ratings)} 条")
        else:
            print(f"  [Finviz OK] {symbol} — 抓取完成（无评级表格数据）")
        if use_cache:
            cache_set("finviz", cache_key, result, ttl)
        return result

    except Exception as e:
        print(f"[WARN] Finviz({symbol}): {e}")
        return {}


# ── SEC 财报分析 ─────────────────────────────────────
def ensure_sec_filings(symbol, fy=None):
    """检查本地是否已有 SEC 财报，没有则调用 sec_filings.py download 下载最新 10-K"""
    if "." not in symbol:
        symbol = f"US.{symbol}"
    mkt, code = symbol.split(".", 1)
    ticker = code.upper()

    ticker_dir = os.path.join(SEC_FILINGS_DIR, ticker)

    # 检查本地是否已有 .htm 文件
    if os.path.isdir(ticker_dir):
        for root, dirs, files in os.walk(ticker_dir):
            for f in files:
                if f.endswith('.htm') or f.endswith('.html'):
                    print(f"  [SEC] 已有本地财报: {ticker}")
                    return True

    # 需要下载
    print(f"  [SEC] 下载 {ticker} 的 SEC 财报...")
    script_path = os.path.join(os.path.dirname(__file__), "sec_filings.py")
    cmd = [sys.executable, script_path, "download", symbol]
    if fy:
        cmd.extend(["--fy", str(fy)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print(f"  [SEC] 下载完成: {ticker}")
            return True
        else:
            err = result.stderr[:200] if result.stderr else 'unknown error'
            print(f"  [SEC] 下载失败: {err}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  [SEC] 下载超时（120s）")
        return False
    except Exception as e:
        print(f"  [SEC] 下载异常: {e}")
        return False


def parse_sec_financials(html_path):
    """从 SEC 10-K/10-Q HTML 中提取关键财务数据（用 regex 匹配，不引入新依赖）"""
    try:
        with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
    except Exception as e:
        print(f"  [SEC] 读取文件失败: {e}")
        return {}

    # 去 HTML 标签
    text = re.sub(r'<[^>]+>', ' ', html)
    # 解码常见 HTML 实体
    for entity, char in [('&#160;', ' '), ('&#8212;', '—'), ('&#8211;', '–'),
                         ('&#8226;', '•'), ('&#167;', '§'), ('&amp;', '&'),
                         ('&lt;', '<'), ('&gt;', '>'), ('&nbsp;', ' ')]:
        text = text.replace(entity, char)
    # 通用数字实体解码
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    text = re.sub(r'&(#x[0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
    # 合并空白
    text = re.sub(r'\s+', ' ', text)

    data = {}

    def extract_num(pattern, txt, negate=False):
        """从 text 中提取第一个匹配 pattern 的数字"""
        m = re.search(pattern, txt, re.IGNORECASE)
        if m:
            val_str = m.group(1).replace(',', '').replace(' ', '')
            try:
                val = float(val_str)
                return -val if negate else val
            except ValueError:
                pass
        return None

    # ── 营收 ──
    for pat in [
        r'Total\s+revenues?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Net\s+revenues?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Net\s+sales?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Total\s+net\s+sales?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Revenues?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
    ]:
        v = extract_num(pat, text)
        if v is not None:
            data['revenue'] = v
            break

    # ── 净利润（正） ──
    for pat in [
        r'Net\s+income\s+attributable\s+to\s+[\w\s]+\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Net\s+income\s+before\s+[\w\s]+\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Net\s+income\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Net\s+earnings?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
    ]:
        v = extract_num(pat, text)
        if v is not None:
            data['net_income'] = v
            break

    # ── 净利润（负/亏损） ──
    if 'net_income' not in data:
        for pat in [
            r'Net\s+loss\s+attributable\s+to\s+[\w\s]+\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
            r'Net\s+loss\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        ]:
            v = extract_num(pat, text, negate=True)
            if v is not None:
                data['net_income'] = v
                break
    # 括号表示负数
    if 'net_income' not in data:
        for pat in [
            r'Net\s+income\s+attributable\s+to\s+[\w\s]+\s*\$\s*\((\d[\d,]*(?:\.\d+)?)\)',
            r'Net\s+income\s*\$\s*\((\d[\d,]*(?:\.\d+)?)\)',
        ]:
            v = extract_num(pat, text, negate=True)
            if v is not None:
                data['net_income'] = v
                break

    # ── 总资产 ──
    v = extract_num(r'Total\s+assets\s*\$?\s*(\d[\d,]*(?:\.\d+)?)', text)
    if v is not None:
        data['total_assets'] = v

    # ── 总负债 ──
    v = extract_num(r'Total\s+liabilities\s*\$?\s*(\d[\d,]*(?:\.\d+)?)', text)
    if v is not None:
        data['total_liabilities'] = v

    # ── 股东权益 ──
    for pat in [
        r"Total\s+[\w\s]+shareholders['\u2019]?\s+equity\s*\$?\s*(\d[\d,]*(?:\.\d+)?)",
        r"Shareholders['\u2019]?\s+equity\s*\$?\s*(\d[\d,]*(?:\.\d+)?)",
        r'Total\s+equity\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
    ]:
        v = extract_num(pat, text)
        if v is not None:
            data['stockholders_equity'] = v
            break

    # ── 毛利润 ──
    for pat in [
        r'Gross\s+profit\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Gross\s+profit\s+\(loss\)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
    ]:
        v = extract_num(pat, text)
        if v is not None:
            data['gross_profit'] = v
            break

    # ── 营业利润 ──
    for pat in [
        r'Operating\s+income\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Income\s+from\s+operations?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
        r'Operating\s+earnings?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)',
    ]:
        v = extract_num(pat, text)
        if v is not None:
            data['operating_income'] = v
            break

    return data


def extract_sec_data(symbol):
    """组合 ensure_sec_filings 和 parse_sec_financials，返回可用的财务数据 dict"""
    if "." not in symbol:
        symbol = f"US.{symbol}"
    mkt, code = symbol.split(".", 1)
    ticker = code.upper()

    # SEC 财报仅用于美股
    if mkt != "US":
        return None

    # 确保本地有财报
    if not ensure_sec_filings(symbol):
        return None

    # 找到 ticker 目录
    ticker_dir = os.path.join(SEC_FILINGS_DIR, ticker)
    if not os.path.isdir(ticker_dir):
        return None

    # 收集所有 .htm/.html 文件（含子目录）
    htm_files = []
    for root, dirs, files in os.walk(ticker_dir):
        for f in files:
            if f.endswith('.htm') or f.endswith('.html'):
                htm_files.append(os.path.join(root, f))

    if not htm_files:
        return None

    # 优先 10-K（年报）> 10-Q（季报）> 其他
    def filing_priority(path):
        basename = os.path.basename(path).lower()
        if '10-k' in basename or '10k' in basename:
            return 0
        elif '10-q' in basename or '10q' in basename:
            return 1
        else:
            return 2

    htm_files.sort(key=lambda f: (filing_priority(f), -os.path.getmtime(f)))

    # 逐个尝试解析，直到提取到数据
    for htm_path in htm_files:
        parsed = parse_sec_financials(htm_path)
        if parsed and len(parsed) >= 2:
            parsed['_source'] = os.path.basename(htm_path)
            parsed['_path'] = htm_path
            return parsed

    return None


def safe_int(v, default=0):
    if v is None or v == 'None' or v == '': return default
    try: return int(float(v))
    except: return default


# ── 主函数 ───────────────────────────────────────────────
def report_analyze(symbol, use_cache=True):
    if "." in symbol:
        mkt, code = symbol.split(".", 1)
    else:
        mkt, code = "US", symbol; symbol = f"US.{symbol}"

    print(f"\n{'='*60}")
    print(f"  📊 生成报告: {symbol}  [cache={'ON' if use_cache else 'OFF'}]")
    print(f"{'='*60}")

    # ── 0. VIX ─────────────────────────────────────────
    vix = common_get_vix(use_cache=use_cache)

    # ── 1. 富途：行情 + K线 + 基本信息（snapshot）────
    ctx = create_quote_context()

    snap = common_futu_snapshot(ctx, symbol, use_cache=use_cache)

    quote = {}
    ret, q = ctx.get_stock_quote([symbol])
    if ret == RET_OK and len(q):
        r = q.iloc[0]
        quote = dict(
            price=float(r.get("last_price") or 0),
            prev=float(r.get("prev_close_price") or 0),
            open_=float(r.get("open_price") or 0),
            high=float(r.get("high_price") or 0),
            low=float(r.get("low_price") or 0),
            vol=int(r.get("volume") or 0),
            chg=float(r.get("change_rate") or 0))

    ctx.subscribe([symbol], [SubType.K_DAY])
    ret_k, kl = ctx.get_cur_kline(symbol, 30, KLType.K_DAY, AuType.QFQ)
    klines = []
    if ret_k == RET_OK:
        for _, row in kl.iterrows():
            t = str(row.get("time_key",""))[:10]
            o,h,l,c,v = float(row['open']),float(row['high']),float(row['low']),float(row['close']),int(row['volume'])
            kchg = ((c-o)/o*100) if o else 0
            klines.append(dict(d=t,o=o,h=h,l=l,c=c,v=v,chg=kchg))

    safe_close(ctx)

    # ── 2. FMP 主力财务数据（主）─────────────────────────
    #    降级时回退到 Alpha Vantage（仅用于 AV 独有数据）
    fmp_income_ann, fmp_income_qtr = fmp_get_income(code, use_cache=use_cache)
    time.sleep(0.2)
    fmp_balance_ann, fmp_balance_qtr = fmp_get_balance(code, use_cache=use_cache)
    fmp_km = fmp_get_key_metrics(code, use_cache=use_cache)
    time.sleep(0.2)
    fmp_divs = fmp_get_dividends(code, use_cache=use_cache)

    # 判断 FMP 是否可用（数据非空）
    fmp_limited = not fmp_income_ann and not fmp_income_qtr

    # ── 3. Alpha Vantage 辅助（仅用于 AV 独有数据）───────
    #    AV 独有：EPS surprise、EARNINGS（含 reportTime）
    av_earnings_raw, av_earn_rl   = av_get("EARNINGS",          code, use_cache=use_cache)
    av_balance_raw, av_bal_rl     = av_get("BALANCE_SHEET",     code, use_cache=use_cache)
    av_income_raw, av_inc_rl      = av_get("INCOME_STATEMENT",  code, use_cache=use_cache)

    income   = fmp_income_ann   if not fmp_limited else (av_income_raw   or {})
    balance  = fmp_balance_ann  if not fmp_limited else (av_balance_raw   or {})
    earnings = av_earnings_raw  or {}
    av_limited = (av_earn_rl or av_bal_rl or av_inc_rl) and fmp_limited

    # ── 4. Finviz：分析师评级 + 机构持仓 + Beta/毛利率/负债率 ──
    finviz = finviz_get(symbol, use_cache=use_cache) if mkt == "US" else {}

    # ── 4. 构建财务数据 ─────────────────────────────────
    def bal_map(reports):
        m = {}
        for r in (reports or []):
            k = r.get("fiscalDateEnding","")
            m[k] = dict(
                assets=safe_int(r.get("totalAssets")),
                liab=safe_int(r.get("totalLiabilities")),
                equity=safe_int(r.get("totalShareholderEquity")) or 1)
        return m

    bal_ann = bal_map(balance.get("annualReports") or [])
    bal_qtr = bal_map(balance.get("quarterlyReports") or [])

    annual = []
    for r in (income.get("annualReports") or [])[:4]:
        dt = r.get("fiscalDateEnding","")
        b_r = bal_ann.get(dt, {})
        rev   = safe_int(r.get("totalRevenue",0))
        ni    = safe_int(r.get("netIncome",0))
        gross = safe_int(r.get("grossProfit",0))
        assets = max(b_r["assets"] or rev, 1)
        equity = max(b_r["equity"], 1)
        annual.append(dict(yr=dt[:4],dt=dt,rev=rev,ni=ni,gross=gross,
                           assets=assets,equity=equity,liab=b_r["liab"]))

    quarterly = []
    for r in (income.get("quarterlyReports") or [])[:8]:
        dt = r.get("fiscalDateEnding","")
        b_r = bal_qtr.get(dt, {})
        rev   = safe_int(r.get("totalRevenue",0))
        ni    = safe_int(r.get("netIncome",0))
        gross = safe_int(r.get("grossProfit",0))
        assets = max(b_r["assets"] or rev, 1)
        equity = max(b_r["equity"], 1)
        quarterly.append(dict(dt=dt,rev=rev,ni=ni,gross=gross,
                              assets=assets,equity=equity,liab=b_r["liab"]))

    eps_hist = []
    for r in (earnings.get("quarterlyEarnings") or [])[:8]:
        eps_hist.append(dict(
            period=r.get("fiscalDateEnding",""),
            rpt_date=r.get("reportedDate",""),
            rep_eps=r.get("reportedEPS","N/A"),
            est_eps=r.get("estimatedEPS","N/A"),
            surprise=r.get("surprise",""),
            surprise_pct=r.get("surprisePercentage",""),
            rpt_time=r.get("reportTime","")))

    # ── 5. 指标计算 ──────────────────────────────────────
    name    = snap.get('name', code) if snap else code
    price   = snap.get('last_price', 0) if snap else quote.get('price', 0)
    chg     = quote.get('chg', 0)

    pe      = snap.get('pe_ttm') if snap else None
    pe_r    = snap.get('pe_ratio') if snap else None
    pb      = snap.get('pb_ratio') if snap else None
    eps_v   = snap.get('eps') if snap else None
    div_v   = snap.get('dividend_ttm') if snap else None
    div_r   = snap.get('dividend_ratio_ttm') if snap else None
    wk52l   = snap.get('lowest52wk') if snap else None
    wk52h   = snap.get('highest52wk') if snap else None
    net_asset = snap.get('net_asset') if snap else None
    net_profit_snap = snap.get('net_profit') if snap else None

    # ROE 优先用 Futu snapshot 计算
    if net_profit_snap and net_asset and net_asset > 0:
        roe_v = net_profit_snap / net_asset * 100
    elif annual:
        cur = annual[0]
        roe_v = cur['ni'] / max(cur['equity'], 1) * 100
    else:
        roe_v = 0

    mktcap_v = snap.get('total_mkt_val') if snap else None

    # ── 指标计算（优先用 FMP/Finviz，AV 仅作兜底）───────
    metrics = {}
    if annual and not fmp_limited:
        cur = annual[0]; prev = annual[1] if len(annual)>1 else cur
        rev=max(cur["rev"],1); ni=cur["ni"]; gross=cur["gross"]
        equity=max(cur["equity"],1); assets=max(cur["assets"],1); liab=cur["liab"]
        prev_rev=max(prev["rev"],1); prev_gross=prev["gross"]
        prev_ni=prev["ni"]; prev_assets=max(prev["assets"],1); prev_liab=prev["liab"]
        roa_v = ni/assets*100
        gm_v  = gross/rev*100 if gross else 0
        nm_v  = ni/rev*100 if rev else 0
        dr_v  = liab/assets*100 if assets else 0

        if roe_v == 0:
            roe_v = ni/equity*100 if equity > 1 else 0

        if not snap or snap.get('eps') is None:
            eps_v = round(ni / (snap.get('issued_shares', 1e10) or 1e10) if snap else 0, 2)

        # 尝试从 Finviz 补充毛利率/负债率
        finviz_gm = finviz.get("gross_margin")
        finviz_dr = finviz.get("debt")
        if finviz_gm and not gm_v:
            try: gm_v = float(finviz_gm.replace("%",""))
            except: pass
        if finviz_dr and not dr_v:
            try: dr_v = float(finviz_dr.replace("%","").replace(",",""))
            except: pass

        metrics = dict(roa=roa_v, roe=roe_v, gm=gm_v, nm=nm_v, dr=dr_v)

        fs=0; fd=[]
        def fs_add(cond, label):
            nonlocal fs; fs+=(1 if cond else 0); fd.append(label)
        fs_add(roa_v>0,         "ROA>0 ✅" if roa_v>0 else "ROA<0 ❌")
        fs_add(ni>0,             "净利润>0 ✅" if ni>0 else "净利润<0 ❌")
        prev_roa_v=prev_ni/prev_assets*100 if prev_assets else 0
        fs_add(roa_v>prev_roa_v,"ROA同比提升 ✅" if roa_v>prev_roa_v else "ROA同比↓ ❌")
        curr_dr=liab/assets*100 if assets else 0
        prev_dr_v=prev_liab/prev_assets*100 if prev_assets else 0
        fs_add(curr_dr<prev_dr_v,"负债率↓ ✅" if curr_dr<prev_dr_v else "负债率↑/不变 ❌")
        prev_gm_v=prev_gross/rev*100 if prev_rev and prev_gross else 0
        fs_add(gm_v>prev_gm_v,  "毛利率↑ ✅" if gm_v>prev_gm_v else "毛利率↓ ❌")
        curr_tr=rev/assets; prev_tr=prev["rev"]/prev_assets if prev_assets else 0
        fs_add(curr_tr>prev_tr,  "资产周转率↑ ✅" if curr_tr>prev_tr else "资产周转率↓ ❌")
        prev_nm_v=prev_ni/rev*100 if prev_rev and prev_ni else 0
        fs_add(nm_v>prev_nm_v,   "净利率↑ ✅" if nm_v>prev_nm_v else "净利率↓ ❌")
        metrics["fscore"]=fs; metrics["fs_det"]=fd
    elif finviz.get("gross_margin") or finviz.get("beta"):
        # FMP 不可用但 Finviz 有数据：展示有限的 Finviz 数据
        gm_v2 = 0; dr_v2 = 0
        if finviz.get("gross_margin"):
            try: gm_v2 = float(finviz["gross_margin"].replace("%",""))
            except: pass
        if finviz.get("debt"):
            try: dr_v2 = float(finviz["debt"].replace("%","").replace(",",""))
            except: pass
        fs=0; fd=["（财务数据不可得，暂不计算 F-Score）"]
        metrics = dict(roa=None, roe=roe_v, gm=gm_v2, nm=0, dr=dr_v2,
                       fscore=fs, fs_det=fd, finviz_only=True)
    else:
        roa_v = None
        gm_v = nm_v = dr_v = 0
        fs=0; fd=["（财务数据不可用，F-Score 暂不计算）"]
        metrics = dict(roa=None, roe=roe_v, gm=0, nm=0, dr=0,
                       fscore=fs, fs_det=fd)

    # ── 6. 下次财报估算 ─────────────────────────────────
    def guess_next_earn(eps_hist):
        if not eps_hist: return "请关注官方公告"
        last_reported = next((r for r in eps_hist if r.get('rpt_date')), None)
        if not last_reported: return "请关注官方公告"
        cycle=["03-31","06-30","09-30","12-31"]
        last_dt=last_reported.get("period","")
        last_q=last_dt[5:] if last_dt else ""
        last_yr=int(last_dt[:4]) if last_dt else 2025
        try: idx=cycle.index(last_q)
        except ValueError: return "请关注官方公告"
        next_idx=(idx+1)%4; next_q=cycle[next_idx]
        next_yr=last_yr if next_idx>idx else last_yr+1
        announce={
            "03-31":(f"{next_yr}-05月中旬","post-market"),
            "06-30":(f"{next_yr}-08月中旬","post-market"),
            "09-30":(f"{next_yr}-11月中旬","post-market"),
            "12-31":(f"{next_yr}-02月中旬","pre-market")}
        est_date,est_time=announce.get(next_q,("待公告","post-market"))
        return f"{next_yr}-{next_q} 截止（预计 {est_date} {est_time}）"

    next_earn_str = guess_next_earn(eps_hist)

    W=68
    AV_WARN = "Alpha Vantage 今日配额耗尽，数据暂不可用"

    # ── 英文缩写中文含义表 ─────────────────────────────
    ABBR = {
        # 基本指标
        "PE（TTM）":      "市盈率（滚动12个月，Price-to-Earnings Trailing Twelve Months）",
        "PE（静态）":     "静态市盈率（Price-to-Earnings Ratio）",
        "PB":             "市净率（Price-to-Book Ratio，每股股价/每股净资产）",
        "EPS":            "每股收益（Earnings Per Share）",
        "ROE":            "净资产收益率（Return on Equity，净利润/净资产）",
        "ROA":            "资产收益率（Return on Assets，净利润/总资产）",
        "Beta":           "贝塔系数（Beta，股价相对市场的波动率，>1波动更高）",
        "Market Cap":     "总市值（Market Capitalization）",
        # 分红
        "股息（TTM）":    "近12个月股息（Trailing Twelve Months Dividend）",
        "股息率":        "股息收益率（Dividend Yield，即股息/股价）",
        # 机构持仓
        "机构持股":       "机构持股比例（Institutional Ownership）",
        "内部人持股":     "内部人持股比例（Insider Ownership）",
        "做空比例":       "被做空流通股占比（Short Float，被做空股票占总流通股的比率）",
        "做空率":         "做空股数/日均成交量比（Short Ratio）",
        # 分析师
        "Recom":          "Recommendation（1=强烈买入，5=强烈卖出）",
        "Downgrade":      "降级",
        "Upgrade":        "升级",
        "Reiterated":     "维持不变",
        "Initiated":      "首次覆盖",
        # VIX
        "VIX":            "恐慌指数（CBOE Volatility Index，衡量标普500期权波动率）",
        # F-Score
        "Piotroski F-Score": "Piotroski价值评分（9分制，考察盈利/杠杆/效率变化）",
        # 财务
        "毛利率":         "毛利润率（Gross Margin，毛利润/营收）",
        "净利润率":       "净利润率（Net Margin，净利润/营收）",
        "负债率":         "负债率（Debt-to-Assets Ratio，总负债/总资产）",
        # K线
        "开盘/最高/最低/收盘": "O/H/L/C（Open/High/Low/Close）",
        "营收":           "收入/营业额（Revenue）",
        "净利润":         "净利润（Net Income）",
    }
    def abbr(label):
        return ABBR.get(label, label)

    print("="*W)
    print(f"  📈 {name} ({symbol}) 价值投资分析报告")
    print("="*W)

    # 基本信息
    print("\n【基本信息】")
    if price:
        a="↑" if chg>=0 else "↓"
        print(f"  现价:        ${price}  {a}{abs(chg):.2f}%")
    if mktcap_v:
        print(f"  总市值:      ${mktcap_v/1e9:.1f}B")
        if snap and snap.get('circular_mkt_val'):
            print(f"  流通市值:    ${snap['circular_mkt_val']/1e9:.1f}B")
    if pe:    print(f"  PE（TTM）:   {pe:.1f}x")
    if pe_r:  print(f"  PE（静态）:  {pe_r:.1f}x")
    if pb:    print(f"  PB:          {pb:.2f}x")
    if eps_v: print(f"  EPS（TTM）:  ${eps_v:.2f}")
    if div_v: print(f"  股息（TTM）: ${div_v:.2f}")
    if div_r: print(f"  股息率:      {div_r:.2f}%")
    if wk52l and wk52h:
        pct52 = (price - wk52l)/(wk52h - wk52l)*100 if wk52h > wk52l else 0
        print(f"  52周区间:   ${wk52l:.2f} - ${wk52h:.2f}（当前 {pct52:.0f}% 分位）")
    if net_profit_snap and net_asset:
        roe_snap = net_profit_snap/net_asset*100
        print(f"  ROE（TTM）: {roe_snap:.1f}%  （净利润 ${net_profit_snap/1e9:.1f}B / 净资产 ${net_asset/1e9:.1f}B）")
    if snap and snap.get('outstanding_shares') and snap.get('issued_shares'):
        print(f"  总股本:      {snap['issued_shares']/1e9:.2f}B 股（流通 {snap['outstanding_shares']/1e9:.2f}B 股）")
    if finviz.get("sector"):
        print(f"  行业:        {finviz['sector']} / {finviz.get('industry','')}")
    if finviz.get("beta"):
        try:
            beta_val = float(finviz["beta"])
            print(f"  Beta:        {beta_val:.2f}  {'（波动高于大盘）' if beta_val>1 else '（波动低于大盘）'}")
        except: pass

    # VIX
    if vix:
        vp=vix.get('price',0); vc=vix.get('chg',0); vc_p=vix.get('chg_pct',0)
        vd_h=vix.get('day_h',0); vd_l=vix.get('day_l',0)
        vy_h=vix.get('yr_h',0); vy_l=vix.get('yr_l',0)
        v50=vix.get('avg50',0); v200=vix.get('avg200',0)
        if vp>=30:    vix_mood="极度恐慌 😱"
        elif vp>=25:  vix_mood="恐慌 😰"
        elif vp>=20:  vix_mood="中性 😶"
        elif vp>=15:  vix_mood="贪婪 😏"
        else:         vix_mood="极度贪婪 🤑"
        pct=(vp-vy_l)/(vy_h-vy_l)*100 if vy_h>vy_l else 0
        va="↑" if vc>=0 else "↓"
        print(f"\n【VIX {abbr('VIX')}】")
        print(f"  VIX:       {vp:.2f}  {va}{abs(vc_p):.2f}%  （{vix_mood}）")
        print(f"  日区间:    {vd_l:.2f} - {vd_h:.2f}")
        print(f"  52W区间:  {vy_l:.2f} - {vy_h:.2f}（当前百分位: {pct:.1f}%）")
        if v50>0: print(f"  均线:      50日均={v50:.2f}  200日均={v200:.2f}  {'价<均线（偏弱）' if vp<v50 else '价>均线（偏强）'}")

    # 季度EPS vs 预测
    if eps_hist and not (fmp_limited and av_limited):
        print("\n【季度EPS vs 分析师预测】")
        print(f"  {'季度':<12} {'财年截止':<12} {'实际EPS':>9} {'预测EPS':>9} {'差异':>8} {'发布日':<12} {'盘口':<12}")
        print("  "+"-"*W)
        for r in eps_hist[:6]:
            period=r.get("period","")
            yr=period[:4]; q_num=(int(period[5:7])-1)//3+1 if len(period)>5 else 1
            rep=r.get("rep_eps","N/A"); est=r.get("est_eps","N/A")
            try: diff_s=f"{float(rep)-float(est):+.2f}"
            except: diff_s="N/A"
            print(f"  {yr}-Q{q_num:<8} {period:<12} {rep:>9} {est:>9} {diff_s:>8} {r.get('rpt_date',''):<12} {r.get('rpt_time',''):<12}")
        print(f"\n  📅 下次财报：{next_earn_str}")
    elif fmp_limited and av_limited:
        print(f"\n【季度EPS vs 分析师预测】")
        print(f"  ⚠️ 财务数据暂不可用（FMP + AV 均失败）")
        print(f"  📅 下次财报：{next_earn_str}")

    # 季度财务
    if quarterly and not fmp_limited:
        print(f"\n【季度财务数据】（单位：亿美元）")
        print(f"  {'季度':<12} {'营收':>10} {'净利润':>10} {'净利润率':>8} {'ROE':>8} {'负债率':>8}")
        print("  "+"-"*W)
        for q in quarterly:
            rev_q=q["rev"]/1e8; ni_q=q["ni"]/1e8
            eq_q=max(q["equity"],1); ast_q=max(q["assets"],1); liab_q=q["liab"]
            nm_q=ni_q/rev_q*100 if rev_q>0 else 0
            roe_q=ni_q/eq_q*100 if eq_q>1 else None
            dr_q=liab_q/ast_q*100 if ast_q>0 else 0
            q_num=(int(q["dt"][5:7])-1)//3+1 if q["dt"] else 1
            print(f"  {q['dt'][:4]}-Q{q_num:<7} {rev_q:>10.1f} {ni_q:>10.1f} {nm_q:>7.1f}% {(f'{roe_q:>7.1f}%') if roe_q is not None else '     -'} {dr_q:>7.1f}%")
    elif finviz.get("gross_margin") or finviz.get("debt"):
        gm_q = 0; dr_q = 0
        if finviz.get("gross_margin"):
            try: gm_q = float(finviz["gross_margin"].replace("%",""))
            except: pass
        if finviz.get("debt"):
            try: dr_q = float(finviz["debt"].replace("%","").replace(",",""))
            except: pass
        print(f"\n【季度财务数据】")
        print(f"  ⚠️ FMP 财报不可用，仅展示 Finviz 有限数据：")
        print(f"  毛利率: {gm_q:.1f}%   负债率: {dr_q:.1f}%")
    else:
        print(f"\n【季度财务数据】")
        print(f"  ⚠️ 财务数据暂不可用（FMP + AV 均失败）")

    # 年度财务
    if annual and not fmp_limited:
        print(f"\n【年度财务数据】（单位：亿美元）")
        print(f"  {'年度':<6} {'营收':>10} {'净利润':>10} {'净利润率':>8} {'ROE':>8} {'负债率':>8}")
        print("  "+"-"*W)
        for yr in annual:
            rev_a=yr["rev"]/1e8; ni_a=yr["ni"]/1e8
            eq_a=max(yr["equity"],1)/1e8; ast_a=max(yr["assets"],1)/1e8; liab_a=yr["liab"]/1e8
            nm_a=ni_a/rev_a*100 if rev_a>0 else 0
            roe_a=ni_a/eq_a*100 if eq_a>0.01 else None
            dr_a=liab_a/ast_a*100 if ast_a>0 else 0
            print(f"  {yr['yr']:<6} {rev_a:>10.1f} {ni_a:>10.1f} {nm_a:>7.1f}% {(f'{roe_a:>7.1f}%') if roe_a is not None else '     -'} {dr_a:>7.1f}%")
    elif finviz.get("gross_margin"):
        print(f"\n【年度财务数据】")
        print(f"  ⚠️ FMP 不可用，仅 Finviz 有限数据")
    else:
        print(f"\n【年度财务数据】")
        print(f"  ⚠️ 财务数据暂不可用（FMP + AV 均失败）")

    # SEC 财报分析（仅美股）
    if mkt == "US":
        sec_data = extract_sec_data(symbol)
        if sec_data:
            print(f"\n【SEC 财报分析】（来源：SEC EDGAR — {sec_data.get('_source', '')}）")
            print(f"  数据单位：百万美元（原始数据，未转换）")
            if 'revenue' in sec_data:
                print(f"  营收:          ${sec_data['revenue']:,.0f}M")
            if 'net_income' in sec_data:
                ni = sec_data['net_income']
                arrow = "↑" if ni > 0 else "↓"
                print(f"  净利润:        ${ni:,.0f}M  {arrow}")
            if 'gross_profit' in sec_data:
                print(f"  毛利润:        ${sec_data['gross_profit']:,.0f}M")
            if 'operating_income' in sec_data:
                print(f"  营业利润:      ${sec_data['operating_income']:,.0f}M")
            if 'total_assets' in sec_data:
                print(f"  总资产:        ${sec_data['total_assets']:,.0f}M")
            if 'total_liabilities' in sec_data:
                print(f"  总负债:        ${sec_data['total_liabilities']:,.0f}M")
            if 'stockholders_equity' in sec_data:
                print(f"  股东权益:      ${sec_data['stockholders_equity']:,.0f}M")
            # 衍生指标
            rev = sec_data.get('revenue', 0)
            ni_val = sec_data.get('net_income', 0)
            gp = sec_data.get('gross_profit', 0)
            ta = sec_data.get('total_assets', 0)
            tl = sec_data.get('total_liabilities', 0)
            se = sec_data.get('stockholders_equity', 0)
            if rev > 0 and ni_val != 0:
                print(f"  净利润率:      {ni_val/rev*100:.1f}%")
            if rev > 0 and gp > 0:
                print(f"  毛利率:        {gp/rev*100:.1f}%")
            if ta > 0 and ni_val != 0:
                print(f"  ROA:           {ni_val/ta*100:.1f}%")
            if se > 0 and ni_val != 0:
                print(f"  ROE:           {ni_val/se*100:.1f}%")
            if ta > 0 and tl > 0:
                print(f"  负债率:        {tl/ta*100:.1f}%")
        else:
            print(f"\n【SEC 财报分析】")
            print(f"  ⚠️ 未获取到 SEC 财报数据")
            print(f"  提示：运行 `python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py download {symbol}` 下载")

    # K线
    if klines:
        print(f"\n【近30交易日K线】（O=开盘 H=最高 L=最低 C=收盘）")
        print(f"  {'日期':<12} {'开盘O':>8} {'最高H':>8} {'最低L':>8} {'收盘C':>8} {'涨跌':>8}")
        print("  "+"-"*W)
        for row in klines[-30:]:
            a="↑" if row["chg"]>=0 else "↓"
            print(f"  {row['d']:<12} {row['o']:>8.2f} {row['h']:>8.2f} {row['l']:>8.2f} {row['c']:>8.2f} {a}{abs(row['chg']):>6.2f}%")

    # F-Score
    fs=metrics.get("fscore",0); fd=metrics.get("fs_det",[])
    finviz_only = metrics.get("finviz_only", False)
    print(f"\n【{abbr('Piotroski F-Score')}】 {fs}/9")
    print(f"  {'⭐'*fs}{'☆'*(9-fs)}")
    for d in fd: print(f"    {d}")
    if fs>=7:   print("  → 高价值信号")
    elif fs>=5: print("  → 中等价值")
    else:       print("  → 低价值信号")

    # 核心指标
    print("\n【核心指标】")
    roe_v2=metrics.get("roe",roe_v)
    roa_v2=metrics.get("roa")
    gm_v2=metrics.get("gm",0); nm_v2=metrics.get("nm",0); dr_v2=metrics.get("dr",0)
    finviz_only = metrics.get("finviz_only", False)
    if fmp_limited and av_limited:
        print(f"  {abbr('ROE')}:       {roe_v2:.1f}%  {'✅ >15%' if roe_v2>15 else '❌ <15%'}（Futu数据）")
        print(f"  {abbr('ROA')}:       ⚠️ 总资产不可得")
        print(f"  {abbr('毛利率')}:    {gm_v2:.1f}%  {'✅ 优秀' if gm_v2>40 else '⚠️ 偏低'}")
        print(f"  {abbr('净利润率')}:  ⚠️ 数据不可得")
        print(f"  {abbr('负债率')}:    {dr_v2:.1f}%  {'⚠️ 偏高' if dr_v2>50 else '✅ 正常'}")
    else:
        print(f"  {abbr('ROE')}:       {roe_v2:.1f}%  {'✅ >15%' if roe_v2>15 else '❌ <15%'}")
        roa_str = f"{roa_v2:.1f}%" if roa_v2 is not None else "N/A"
        print(f"  {abbr('ROA')}:       {roa_str}  {'✅ >0' if roa_v2 and roa_v2>0 else '❌ <0'}")
        print(f"  {abbr('毛利率')}:    {gm_v2:.1f}%  {'✅ 优秀' if gm_v2>40 else '⚠️ 偏低'}")
        print(f"  {abbr('净利润率')}:  {nm_v2:.1f}%")
        print(f"  {abbr('负债率')}:    {dr_v2:.1f}%  {'⚠️ 偏高' if dr_v2>50 else '✅ 正常'}")

    # ── 分析师评级（来自 Finviz）────────────────────────
    if finviz and finviz.get("ratings"):
        print("\n【分析师评级】（Finviz）")
        print(f"  {'日期':<12} {'行动':<12} {'机构':<22} {'评级变化':<18} {'目标价变化'}")
        print("  "+"-"*W)
        for r in finviz["ratings"][:8]:
            print(f"  {r['date']:<12} {r['action']:<12} {r['firm']:<22} {r['rating']:<18} {r['target']}")
        if finviz.get("recom"):
            print(f"\n  综合评级（{abbr('Recom')}）：{finviz['recom']}")
        if finviz.get("target_price"):
            print(f"  分析师目标中价：{finviz['target_price']}")

    # ── 机构与持仓（来自 Finviz）────────────────────────
    if finviz:
        print(f"\n【机构与持仓】（来源：Finviz）")
        if finviz.get("inst_own"):
            print(f"  {abbr('机构持股')}：{finviz['inst_own']}")
        if finviz.get("ins_own"):
            print(f"  {abbr('内部人持股')}：{finviz['ins_own']}")
        if finviz.get("short_float"):
            print(f"  {abbr('做空比例')}：{finviz['short_float']}")
        if finviz.get("short_ratio"):
            print(f"  {abbr('做空率')}：{finviz['short_ratio']}")
        if finviz.get("short_interest"):
            print(f"  做空股数（Short Interest）：{finviz['short_interest']}")

    # 综合评估
    def star(s): return "⭐"*min(s,5)
    s_roe=min(5,max(1,int(roe_v2/8)))
    s_dr=5 if dr_v2<40 else(4 if dr_v2<50 else(3 if dr_v2<60 else 2))
    s_fs=min(5,max(1,int(fs/2))) if not finviz_only else 0
    pe_val = pe if pe else 0
    s_pe = 5 if 0<pe_val<15 else(4 if pe_val<20 else(3 if pe_val<25 else(2 if pe_val<30 else 1)))
    print("\n【价值投资综合评估】")
    if finviz_only:
        print(f"  盈利能力   {'⭐'*s_roe}  ROE {roe_v2:.1f}%")
        print(f"  财务健康   ⚠️  负债率 {dr_v2:.1f}%（Finviz数据）")
    elif fmp_limited and av_limited:
        print(f"  盈利能力   {'⭐'*s_roe}  ROE {roe_v2:.1f}% ⚠️")
        print(f"  财务健康   ⚠️  财务数据暂不可用")
    else:
        print(f"  盈利能力   {'⭐'*s_roe}  ROE {roe_v2:.1f}%")
        print(f"  财务健康   {'⭐'*s_dr}  负债率 {dr_v2:.1f}%")
    print(f"  F-Score    {'⭐'*s_fs if s_fs else '⚠️'}  {fs}/9{'（数据不足）' if finviz_only else ''}")
    print(f"  估值       {'⭐'*s_pe}  PE {pe if pe else 'N/A'}")

    print("\n【结论】")
    if finviz_only:
        print("  ⚠️ FMP 财报不可用，仅有 Finviz 有限数据，综合评估受限。")
    elif fmp_limited and av_limited:
        print("  ⚠️ 财务数据暂不可用（FMP + AV 均失败），综合评估受限。")
        print("  请稍后重试。")
    else:
        tot=s_roe+s_dr+s_fs+s_pe
        if tot>=16 and fs>=7:
            print(f"  ✅ 综合评分良好，ROE {roe_v2:.1f}%，F-Score {fs}/9，建议结合估值判断。")
        elif tot>=13:
            print(f"  ⚠️ 中等价值，建议进一步研究竞争格局。")
        else:
            print(f"  ❌ 当前指标显示价值信号偏弱，建议等待更好买点。")

    print("\n"+"="*W)
    print("  ⚠️ 本报告仅供参考，不构成投资建议。数据可能有延迟。")
    print(f"  缓存目录: {CACHE_DIR}")
    print("="*W)


if __name__=="__main__":
    p=argparse.ArgumentParser(description="价值投资分析报告 v0.9")
    p.add_argument("code", help="股票代码，如 US.NKE、HK.00700")
    p.add_argument("--no-cache", action="store_true", help="禁用缓存，强制从网络拉取")
    p.add_argument("--cache-ttl", type=int, default=3600, help="财务数据缓存TTL（秒），默认3600")
    p.add_argument("--output", "-o", metavar="PATH", help="报告输出路径（默认自动生成：股票代码+时间戳）")
    args=p.parse_args()

    # 生成报告
    output_buffer = io.StringIO()
    original_stdout = sys.stdout

    def run_with_output(use_cache):
        sys.stdout = output_buffer
        report_analyze(args.code, use_cache=use_cache)
        sys.stdout = original_stdout

    if args.no_cache:
        run_with_output(use_cache=False)
    else:
        run_with_output(use_cache=True)

    report_text = output_buffer.getvalue()
    original_stdout.write(report_text)

    # 写文件
    if args.output:
        out_path = args.output
    else:
        # 自动命名：SYMBOL_YYYYMMDD_HHMMSS.txt
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[.\s]+', '_', args.code)
        out_path = os.path.expanduser(f"~/.openclaw/reports/{safe_name}_{ts}.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report_text)
    original_stdout.write(f"\n  📁 报告已保存: {out_path}\n")
