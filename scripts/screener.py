#!/usr/bin/env python3
"""
价值股精选池筛选器 v1.0

数据流：
  ① 加载 S&P500 + Nasdaq100 成分股（739只）
  ② Finviz 财务筛选器粗筛（ROE>15%, PE<35, 市值>50亿）
  ③ FMP 获取详细财务数据（ROE、负债率、净利润率）
  ④ 综合评分排序，输出 Markdown 报告

用法：
  python3 screener.py [--no-cache] [--top N]
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import importlib.util
from datetime import datetime

# ── 加载 futuapi common ────────────────────────────────
_SPEC = importlib.util.spec_from_file_location(
    "_fc", "/Users/wmh/.openclaw/skills/futuapi/scripts/common.py")
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["_fc"] = _MOD
_SPEC.loader.exec_module(_MOD)
create_quote_context = _MOD.create_quote_context
from futu import RET_OK, SimpleFilter, StockField, Market

# ── 常量 ──────────────────────────────────────────────
FMP_KEY  = "OeyYwkTOzQUfkywmNu8p0NFIP1pTSv6x"
FMP_URL  = "https://financialmodelingprep.com/stable"
AV_KEY   = "UQ3XI876M9S3PKND"
AV_URL   = "https://www.alphavantage.co/query"
SCREENER_DATA = os.path.expanduser("~/.openclaw/workspace-stock_god/value-screener/data/")
CACHE_DIR = os.path.expanduser("~/.openclaw/cache/stock-god/screener/")
os.makedirs(CACHE_DIR, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# ── 工具函数 ──────────────────────────────────────────
def safe_int(v, default=0):
    if v is None or v == 'None' or v == '': return default
    try: return int(float(v))
    except: return default

def safe_float(v, default=0.0):
    if v is None or v == 'None' or v == '': return default
    try: return float(v)
    except: return default

def b(v):
    try:
        v = float(v)
        if abs(v) >= 1e12: return f"${v/1e12:.1f}T"
        if abs(v) >= 1e9:  return f"${v/1e9:.1f}B"
        if abs(v) >= 1e6:  return f"${v/1e6:.1f}M"
        return f"${v:.0f}"
    except: return str(v)

def _cache(ns, key, ttl=3600):
    p = os.path.join(CACHE_DIR, f"{ns}_{key}.json")
    if os.path.exists(p):
        try:
            with open(p) as f: e = json.load(f)
            if time.time() - e.get('_ts',0) <= e.get('_ttl',0):
                return e.get('data'), True
        except: pass
    return None, False

def _save(ns, key, data, ttl=3600):
    p = os.path.join(CACHE_DIR, f"{ns}_{key}.json")
    try:
        with open(p, 'w') as f:
            json.dump({'_ts': time.time(), '_ttl': ttl, 'data': data}, f)
    except: pass

def fmp_get(endpoint, symbol, use_cache=True, ttl=3600, max_retries=3):
    """FMP API 调用，带缓存和重试（处理 429 rate limit）"""
    cache_key = f"{endpoint}_{symbol}"
    if use_cache:
        data, hit = _cache("fmp", cache_key, ttl)
        if hit: return data, None
    url = f"{FMP_URL}/{endpoint}?symbol={symbol}&apikey={FMP_KEY}"
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                raw = json.loads(r.read())
                if isinstance(raw, dict) and raw.get('error'): return None, raw.get('error')
                if use_cache: _save("fmp", cache_key, raw, ttl)
                return raw, None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s exponential backoff
                print(f"  [FMP 限流] 等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            return None, f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return None, str(e)
    return None, "Max retries exceeded"

def fmp_calc_roe(symbol):
    """用 FMP 数据计算 ROE，返回 % 或 None"""
    # 最近的财年净利润 / 股东权益
    inc_raw, _ = fmp_get("income-statement", symbol, True, 3600)
    bs_raw, _  = fmp_get("balance-sheet-statement", symbol, True, 3600)
    if not inc_raw or not bs_raw: return None, None, None

    def to_list(d, key):
        v = d.get(key, [])
        if isinstance(v, list): return v
        return d.get('annual', []) or []

    inc_list = to_list(inc_raw, 'income-statement')
    bs_list  = to_list(bs_raw, 'balance-sheet-statement')

    if not inc_list or not bs_list: return None, None, None

    latest_inc = inc_list[0]
    ni = safe_float(latest_inc.get('netIncome', 0))
    if not ni: return None, None, None

    # 找到匹配的资产负债表日期
    inc_dt = latest_inc.get('date','')
    # 取最近一期的 equity
    equity = None
    for b in bs_list:
        if b.get('date') == inc_dt:
            equity = safe_float(b.get('totalEquity', 0))
            break
    if not equity:
        for b in bs_list:
            e = safe_float(b.get('totalEquity', 0))
            if e > 0:
                equity = e
                break

    if not equity: return None, None, None

    roe = ni / equity * 100
    rev = safe_float(latest_inc.get('revenue', 0))
    gross = safe_float(latest_inc.get('grossProfit', 0))

    # 负债率
    assets = None
    for b in bs_list:
        a = safe_float(b.get('totalAssets', 0))
        if a > 0:
            assets = a
            break
    liab = safe_float(b.get('totalLiabilities', 0)) if b else 0
    debt_ratio = (liab / assets * 100) if assets else None

    return round(roe, 1), round(gross/rev*100 if rev and gross else 0, 1), round(debt_ratio, 1) if debt_ratio is not None else None

# ── ① 加载成分股列表 ──────────────────────────────────
def load_constituents():
    path = os.path.join(SCREENER_DATA, "constituents.json")
    d = json.load(open(path))
    tickers = d.get("combined", [])
    # 转换为 US.XXX 格式
    return [f"US.{t}" for t in tickers]

# ── ② Futu 粗筛 ────────────────────────────────────
def futu_prescreen(tickers, max_count=200):
    """用 Futu SimpleFilter 粗筛：PE 5-35 + 市值>50亿
    注意：Futu get_stock_filter API 有已知限制：
      - MARKET_VAL 范围筛选不可用（返回错误码 2159）
      - PE_TTM 可用，但部分股票 market_val 字段为空
    因此实际策略：先用 PE_TTM 粗筛，再在 Python 层用 cached 数据补充市值验证。
    """
    ctx = create_quote_context()
    preselected = []

    # PE_TTM 筛选器
    f_pe = SimpleFilter()
    f_pe.stock_field = StockField.PE_TTM
    f_pe.is_no_filter = False
    f_pe.filter_min = 5
    f_pe.filter_max = 35

    # 市值 > 50亿 筛选器
    f_mkt = SimpleFilter()
    f_mkt.stock_field = StockField.MARKET_VAL
    f_mkt.is_no_filter = False
    f_mkt.filter_min = 5e9

    ret, result = ctx.get_stock_filter(
        Market.US,
        filter_list=[f_pe, f_mkt],
        begin=0,
        num=500
    )

    if ret == RET_OK and isinstance(result, tuple) and len(result) == 3:
        success, code, data_list = result
        # 即使 success=False，Futu 仍可能返回数据（code=3004 表示有警告但有数据）
        if data_list:
            for item in data_list:
                code_str = str(item.stock_code) if hasattr(item, 'stock_code') else ''
                if not code_str.startswith('US.'):
                    code_str = 'US.' + code_str
                mkt_val = item.market_val if hasattr(item, 'market_val') and item.market_val else 0
                preselected.append({
                    'code': code_str,
                    'name': str(item.stock_name) if hasattr(item, 'stock_name') else '',
                    'pe': item.pe_ttm if hasattr(item, 'pe_ttm') else None,
                    'mkt_b': mkt_val / 1e9,
                })

    ctx.close()

    # 如果 Futu 返回不足（可能因为 MARKET_VAL 限制），回退到 cached 数据
    if len(preselected) < 10:
        print(f"  [Futu粗筛] 仅返回 {len(preselected)} 只（API限制），回退到 cached 数据...")
        cached = os.path.join(SCREENER_DATA, "screened_us_stocks.json")
        if os.path.exists(cached):
            cached_data = json.load(open(cached))
            for item in cached_data:
                pe = item.get('pe')
                mkt = item.get('mkt_b', 0)
                if pe and 5 <= pe <= 35 and mkt > 5:
                    preselected.append(item)
            # 去重
            seen = set()
            unique = []
            for c in preselected:
                if c['code'] not in seen:
                    seen.add(c['code'])
                    unique.append(c)
            preselected = unique

    print(f"  [Futu粗筛] → {len(preselected)} 只")
    return preselected[:max_count]

# ── ③ FMP 精筛（ROE/负债率）─────────────────────────────
def fmp_refine(candidates, max_stocks=50):
    """对候选股逐只获取 FMP 财务数据，计算 ROE，返回达标股票
    FMP 失败时使用 Finviz ROE 作为备选。
    """
    results = []
    for i, c in enumerate(candidates):
        sym = c['code']
        ticker = sym.split('.')[1] if '.' in sym else sym
        print(f"  [{i+1}/{len(candidates)}] {sym} ...", end='', flush=True)

        roe, gm, dr = fmp_calc_roe(ticker)
        source = 'FMP'

        # FMP 失败时，尝试从 Finviz 获取 ROE
        if roe is None or roe == 0:
            fv = finviz_snapshot(sym)
            roe_str = fv.get('roe')
            if roe_str:
                try:
                    roe = float(roe_str.replace('%', ''))
                    source = 'Finviz'
                except:
                    roe = None
            dr_str = fv.get('debt_equity')
            if dr_str and dr is None:
                try:
                    dr = float(dr_str.replace('%', ''))
                except:
                    pass

        if roe is None:
            print(f" {source} ROE=N/A")
            time.sleep(1)
            continue

        c['roe'] = roe
        c['gross_margin'] = gm
        c['debt_ratio'] = dr
        print(f" {source} ROE={roe}% GM={gm}% DR={dr}%")

        if roe >= 15 and (dr is None or dr < 60):
            results.append(c)

        if len(results) >= max_stocks:
            print(f"\n  [INFO] 已达到 {max_stocks} 只目标，停止精筛")
            break

        # FMP 免费账号限制：1s 延迟
        time.sleep(1.2)

    print(f"\n  [FMP精筛] → {len(results)} 只（ROE≥15%, 负债率<60%）")
    return results

# ── ④ Finviz 补充数据 ─────────────────────────────────
def finviz_snapshot(ticker):
    """从 Finviz 获取基本面快照"""
    t = ticker.split('.')[1] if '.' in ticker else ticker
    cache_key = f"finviz_{ticker}"
    data, hit = _cache("finviz", cache_key, 3600)
    if hit: return data or {}

    url = f"https://finviz.com/quote.ashx?t={t.upper()}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode('utf-8', errors='replace')

        def extract(label):
            for p in [
                r'<div class="snapshot-td-label">' + re.escape(label) + r'</div></td><td[^>]*>[^<]*<div class="snapshot-td-content"><b>([^<]+)</b>',
                re.escape(label) + r'(?:</a>)?</div></td><td[^>]*>[^<]*<div[^>]*><(?:a[^>]*>)?<b>([^<]+)</b>',
            ]:
                m = re.search(p, html)
                if m: return m.group(1).strip()
            return None

        result = {}
        for lbl in ['Market Cap', 'P/E', 'EPS (ttm)', 'ROE', 'Debt', 'Debt/Equity',
                    'Gross Margin', 'Operating Margin', 'Profit Margin', 'Beta',
                    'Inst Own', 'Short Float', 'Recom']:
            v = extract(lbl)
            if v: result[lbl.lower().replace(' ', '_')] = v
        if result: _save("finviz", cache_key, result, 3600)
        return result
    except:
        return {}

# ── ⑤ 综合评分 ────────────────────────────────────────
def score_stock(c, finviz):
    """综合评分 1-10"""
    s = 0
    roe = c.get('roe', 0) or 0
    pe  = c.get('pe') or 0
    dr  = c.get('debt_ratio') or 0
    gm  = c.get('gross_margin') or 0

    # ROE (30%)
    if roe >= 25: s += 3.0
    elif roe >= 20: s += 2.5
    elif roe >= 15: s += 2.0
    elif roe >= 10: s += 1.0

    # 估值 PE (25%)
    if pe > 0:
        if pe <= 15: s += 2.5
        elif pe <= 20: s += 2.0
        elif pe <= 25: s += 1.5
        elif pe <= 30: s += 1.0

    # 负债率 (20%)
    if dr > 0:
        if dr < 30: s += 2.0
        elif dr < 50: s += 1.5
        elif dr < 60: s += 1.0

    # 毛利率 (15%)
    if gm > 0:
        if gm >= 40: s += 1.5
        elif gm >= 20: s += 1.0
        else: s += 0.5

    # 机构持股 (10%)
    inst = finviz.get('inst_own','')
    if inst:
        inst_pct = safe_float(inst.replace('%',''))
        if inst_pct >= 70: s += 1.0
        elif inst_pct >= 40: s += 0.5

    return round(s, 1)

# ── 主函数 ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="价值股精选池筛选器 v1.0")
    parser.add_argument("--no-cache", action="store_true", help="强制刷新，忽略缓存")
    parser.add_argument("--top", type=int, default=20, help="输出前N只（默认20）")
    parser.add_argument("--limit", type=int, default=100, help="FMP精筛上限（默认100）")
    args = parser.parse_args()

    print("=" * 60)
    print("  📊 价值股精选池筛选器 v1.0")
    print("=" * 60)

    # Step 1: 加载成分股
    print("\n① 加载 S&P500 + Nasdaq100 成分股...")
    tickers = load_constituents()
    print(f"  合计: {len(tickers)} 只")

    # Step 2: Futu 粗筛
    print("\n② Futu SimpleFilter 粗筛（PE 5-35, 市值>50亿）...")
    candidates = futu_prescreen(tickers, max_count=200)

    if not candidates:
        print("  ⚠️ Futu 粗筛无结果，尝试读取已缓存数据...")
        cached = os.path.join(SCREENER_DATA, "screened_us_stocks.json")
        if os.path.exists(cached):
            candidates = json.load(open(cached))
            print(f"  使用缓存数据: {len(candidates)} 只")

    if not candidates:
        print("  ❌ 无法获取候选股，退出")
        return

    # Step 3: FMP 精筛
    print(f"\n③ FMP 精筛（ROE≥15%, 负债率<60%）限前{args.limit}只...")
    top_candidates = candidates[:args.limit]
    qualified = fmp_refine(top_candidates, max_stocks=50)

    if not qualified:
        print("  ❌ 无达标股票，建议放宽条件重试")
        return

    # Step 4: Finviz 补充 + 评分
    print("\n④ Finviz 补充数据 + 综合评分...")
    scored = []
    for c in qualified:
        fv = finviz_snapshot(c['code'])
        c['finviz'] = fv
        c['score'] = score_stock(c, fv)
        scored.append(c)
    scored.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n  综合评分 Top {min(args.top, len(scored))}:")
    for c in scored[:args.top]:
        fv = c.get('finviz', {})
        inst = fv.get('inst_own', 'N/A')
        pe = c.get('pe', 'N/A')
        dr = c.get('debt_ratio', 'N/A')
        print(f"  {c['code']:<12} ROE={c['roe']}% PE={pe} DR={dr}% 机构持股={inst} ⭐{c['score']}")

    # Step 5: 输出 Markdown 报告
    print("\n" + "=" * 60)
    print("  📈 价值股精选池报告")
    print("=" * 60)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n生成时间：{ts}")
    print(f"数据来源：Futu OpenD（行情）+ FMP（财务）+ Finviz（补充）")
    print(f"筛选条件：ROE≥15% | 负债率<60% | PE 5-35 | 市值>50亿")
    print(f"候选范围：S&P500 + Nasdaq100（共739只）")
    print(f"\n精选池（%d只，按综合评分排序）：".format(len(scored)))
    print("| 排名 | 代码 | ROE | PE | 负债率 | 毛利率 | 机构持股 | 综合评分 |")
    print("|:---|:---|---:|---:|---:|---:|---:|---:|")
    for i, c in enumerate(scored[:args.top], 1):
        fv = c.get('finviz', {})
        inst = fv.get('inst_own', '-')
        gm = c.get('gross_margin', '-')
        dr = c.get('debt_ratio', '-')
        pe = c.get('pe', '-')
        print(f"| {i} | {c['code']} | {c['roe']}% | {pe} | {dr}% | {gm}% | {inst} | ⭐{c['score']} |")

    print(f"\n说明：")
    print(f"- ROE = 净利润/股东权益（最新财年）")
    print(f"- 负债率 = 总负债/总资产（最新财年）")
    print(f"- 毛利率 = 毛利润/营收")
    print(f"- 综合评分：ROE(30%) + 估值(25%) + 负债率(20%) + 毛利率(15%) + 机构持股(10%)")
    print(f"\n⚠️ 本报告仅供参考，不构成投资建议。")

    # 保存报告
    out_dir = os.path.expanduser("~/.openclaw/reports/")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"value_screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    report_lines = [f"# 价值股精选池报告", f"生成时间：{ts}",
        f"数据来源：Futu OpenD + FMP + Finviz",
        f"筛选条件：ROE≥15% | 负债率<60% | PE 5-35 | 市值>50亿",
        f"候选范围：S&P500 + Nasdaq100（共739只）",
        "",
        f"## 精选池（共{len(scored)}只）",
        "",
        "| 排名 | 代码 | ROE | PE | 负债率 | 毛利率 | 机构持股 | 综合评分 |",
        "|:---|:---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, c in enumerate(scored[:args.top], 1):
        fv = c.get('finviz', {})
        inst = fv.get('inst_own', '-')
        gm = c.get('gross_margin', '-')
        dr = c.get('debt_ratio', '-')
        pe = c.get('pe', '-')
        report_lines.append(f"| {i} | {c['code']} | {c['roe']}% | {pe} | {dr}% | {gm}% | {inst} | ⭐{c['score']} |")

    report_lines.extend(["", "⚠️ 本报告仅供参考，不构成投资建议。数据可能有延迟。"])
    with open(out_path, 'w') as f:
        f.write('\n'.join(report_lines))
    print(f"\n  📁 报告已保存: {out_path}")


if __name__ == "__main__":
    main()
