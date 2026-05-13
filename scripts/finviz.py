#!/usr/bin/env python3
"""
finviz.py - Finviz 数据获取模块
v1.0: 新闻 + 快照 + 分析师评级 + 机构持仓
"""
import re, urllib.request, os, time, json, hashlib

# ── 缓存 ────────────────────────────────────────────────
CACHE_DIR = os.path.expanduser("~/.openclaw/cache/stock-god/")
os.makedirs(CACHE_DIR, exist_ok=True)

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
    except Exception:
        pass


# ── 新闻解析 ────────────────────────────────────────────
def finviz_parse_news(html):
    """解析 Finviz HTML 中的新闻列表
    返回: [{"date": "Apr-24-26", "time": "01:45PM", "source": "...", "title": "...", "url": "..."}, ...]
    """
    news = []
    # 找到 news-table section
    m_table = re.search(r'id="news-table"[^>]*>(.*?)</table>', html, re.DOTALL)
    if not m_table:
        return news
    table_html = m_table.group(1)
    # 每条新闻是 <tr class="cursor-pointer has-label" onclick="trackAndOpenNews(...)">...</tr>
    rows = re.findall(
        r'<tr[^>]*class="cursor-pointer has-label"[^>]*onclick="trackAndOpenNews\([^)]+\)"[^>]*>(.*?)</tr>',
        table_html, re.DOTALL)
    if not rows:
        # 备用：匹配所有 cursor-pointer tr
        rows = re.findall(r'<tr[^>]*class="cursor-pointer[^"]*"[^>]*>(.*?)</tr>', table_html, re.DOTALL)

    last_date = ""
    for row in rows:
        # 来源: onclick 第二个参数
        src_match = re.search(r"trackAndOpenNews\([^,]+,\s*'([^']+)'\s*,", row)
        source = src_match.group(1) if src_match else ""
        if not source:
            # 备用: <span>(Investing.com)</span>
            sp = re.search(r'<span>\(([^)]+)\)</span>', row)
            source = sp.group(1) if sp else ""

        # 日期+时间格: <td align="right">Apr-24-26 01:45PM</td> 或 Today 02:06AM 或 11:55AM
        # 先提取整个td内容，再分情况解析
        d_match = re.search(r'<td[^>]*align="right"[^>]*>(.*?)</td>', row, re.DOTALL)
        cell = d_match.group(1).strip() if d_match else ""
        # 尝试三种格式
        m2 = re.match(r'(Today|Yesterday)\s+(\d{1,2}:\d{2}[AP]M)', cell)
        m3 = re.match(r'(\d{1,2}:\d{2}[AP]M)\s+(\d{1,2}:\d{2}[AP]M)', cell)  # shouldn't happen
        m4 = re.match(r'([A-Za-z]{3}-\d{2}-\d{2})\s+(\d{1,2}:\d{2}[AP]M)', cell)
        m5 = re.match(r'([A-Za-z]{3}-\d{2}-\d{2})', cell)
        m6 = re.match(r'(\d{1,2}:\d{2}[AP]M)', cell)
        if m2:
            date_str, time_str = m2.group(1), m2.group(2)
        elif m4:
            date_str, time_str = m4.group(1), m4.group(2)
        elif m5:
            date_str, time_str = m5.group(1), ""
        elif m6:
            date_str, time_str = last_date, m6.group(1)
        else:
            date_str, time_str = last_date, ""
        if d_match:
            first = d_match.group(1)
            second = d_match.group(2) or ""
            if first in ("Today", "Yesterday"):
                date_str = first
                time_str = second.strip() if second else ""
            else:
                date_str = first  # e.g. Apr-24-26
                time_str = second.strip() if second else ""
        else:
            date_str = last_date
            time_str = ""

        # 链接和标题
        l_match = re.search(r'<a[^>]*class="tab-link-news"[^>]*href="([^"]+)"[^>]*>\s*([^<\n]+)', row)
        if l_match:
            url = l_match.group(1)
            title = l_match.group(2).strip()
            last_date = date_str
            news.append({
                "date": date_str,
                "time": time_str,
                "source": source,
                "title": title,
                "url": url
            })
    return news


# ── 快照解析 ────────────────────────────────────────────
def finviz_parse_snapshot(html):
    """解析 Finviz HTML 中的快照表格
    返回: dict of label -> value
    """
    def extract(label):
        p1 = r'<div class="snapshot-td-label">' + re.escape(label) + r'</div></td><td[^>]*>[^<]*<div class="snapshot-td-content"><b>([^<]+)</b>'
        p2 = re.escape(label) + r'(?:</a>)?</div></td><td[^>]*>[^<]*<div[^>]*><(?:a[^>]*>)?<b>([^<]+)</b>'
        for p in [p1, p2]:
            m = re.search(p, html)
            if m:
                return m.group(1).strip()
        return None

    result = {}
    fields = [
        "Inst Own", "Insider Own",
        "Short Float", "Short Ratio",
        "Recom",
        "Market Cap", "P/E", "EPS (ttm)", "Sales", "P/S", "P/B",
        "Dividend Est.", "Beta", "Avg Volume", "Employees",
        "Sector", "Industry",
        "EPS Growth", "Revenue Growth",
        "Gross Margin", "Operating Margin", "Profit Margin",
        "Debt", "Debt/Equity", "Current Ratio",
    ]
    for f in fields:
        v = extract(f)
        if v is not None:
            key = f.lower().replace(" ", "_").replace("(", "").replace(")", "")
            if "de_ ratio" not in key:
                result[key] = v
            else:
                result["de_ratio"] = v
    return result


# ── 分析师评级解析 ──────────────────────────────────────
def finviz_parse_ratings(html):
    """解析 Finviz HTML 中的分析师评级变化表格
    返回: [{"date": "...", "action": "...", "firm": "...", "rating": "...", "target": "..."}, ...]
    """
    ratings = []
    pattern = re.findall(
        r'<tr[^>]*><td[^>]*>(\w{3}-\d{2}-\d{2})</td>'
        r'<td[^>]*>(?:<span[^>]*>)?([^<]+?)(?:</span>)?</td>\s*'
        r'<td[^>]*>([^<]+)</td>\s*'
        r'<td[^>]*>([^<]+)</td>\s*'
        r'<td[^>]*(?:class="[^"]*\s)?tabular-nums[^>]*>([^<]+)</td>',
        html)
    for row in pattern[:10]:
        date_str, action, firm, rating_ch, target_ch = row
        ratings.append({
            "date": date_str.strip(),
            "action": action.strip(),
            "firm": firm.strip(),
            "rating": rating_ch.strip().replace('&rarr;', '→'),
            "target": target_ch.strip().replace('&rarr;', '→')
        })
    return ratings


# ── 主获取函数 ───────────────────────────────────────────
def finviz_news(symbol, use_cache=True, ttl=3600):
    """获取 Finviz 新闻（50条）"""
    mkt, code = (symbol.split(".", 1) + [None])[:2]
    ticker = (code or symbol).upper()

    cache_key = f"finviz_news_{ticker}"
    if use_cache:
        data, hit = cache_get("finviz", cache_key)
        if hit:
            print(f"  [CACHE HIT] Finviz News({symbol})")
            return data

    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
        news = finviz_parse_news(html)
        if news:
            print(f"  [Finviz OK] {symbol} — 新闻 {len(news)} 条")
        else:
            print(f"  [Finviz WARN] {symbol} — 新闻解析为空")
        if use_cache:
            cache_set("finviz", cache_key, news, ttl)
        return news
    except Exception as e:
        print(f"[WARN] Finviz News({symbol}): {e}")
        return []


def finviz_get(symbol, use_cache=True, ttl=3600):
    """获取 Finviz 快照 + 分析师评级 + 机构持仓
    返回 dict，失败返回 {}
    """
    mkt, code = (symbol.split(".", 1) + [None])[:2]
    ticker = (code or symbol).upper()

    cache_key = f"finviz_{symbol}"
    if use_cache:
        data, hit = cache_get("finviz", cache_key)
        if hit:
            print(f"  [CACHE HIT] Finviz({symbol})")
            return data

    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")

        snapshot = finviz_parse_snapshot(html)
        ratings = finviz_parse_ratings(html)

        result = {**snapshot, "ratings": ratings}
        result["news"] = []  # 预留，新闻由 finviz_news() 单独获取

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


# ── 测试入口 ────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NKE"
    print(f"=== Finviz 测试: {ticker} ===")
    news = finviz_news(ticker)
    print(f"\n新闻列表（前5条）:")
    for n in news[:5]:
        print(f"  [{n['date']} {n['time']}] {n['source']}")
        print(f"    {n['title']}")
    data = finviz_get(ticker, use_cache=False)
    print(f"\n快照字段: {list(data.keys())}")
    print(f"分析师评级: {len(data.get('ratings', []))} 条")
