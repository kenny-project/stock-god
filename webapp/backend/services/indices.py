"""指数成分（标普500 / 纳斯达克100）名单拉取与库内标记同步。

数据源：
- 标普500: datasets/s-and-p-500-companies CSV（含 Symbol 列）
- 纳斯达克100: api.nasdaq.com（必须带浏览器请求头，否则 403/空响应）
"""
import csv
import io
import json
import logging
import urllib.request

from sqlalchemy import select

from models import Stock

log = logging.getLogger(__name__)

SP500_CSV_URL = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
                 "main/data/constituents.csv")
NDX100_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100?download=false"
# nasdaq 接口反爬：无 UA 直接 403
NDX_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
}


def _http_get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "stock-god/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_sp500_symbols() -> list[str]:
    raw = _http_get(SP500_CSV_URL)
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    if rows.fieldnames is None or "Symbol" not in rows.fieldnames:
        raise ValueError("标普500 CSV 缺少 Symbol 列")
    symbols = [r["Symbol"].strip().upper() for r in rows if (r.get("Symbol") or "").strip()]
    if not symbols:
        raise ValueError("标普500 CSV 解析结果为空")
    return symbols


def fetch_ndx100_symbols() -> list[str]:
    raw = _http_get(NDX100_URL, headers=NDX_HEADERS)
    payload = json.loads(raw.decode("utf-8"))
    rows = (((payload.get("data") or {}).get("data") or {}).get("rows")) or []
    symbols = [(r.get("symbol") or "").strip().upper() for r in rows]
    symbols = [s for s in symbols if s]
    if not symbols:
        raise ValueError("纳斯达克100 接口解析结果为空")
    return symbols


def _swap_dot_dash(t: str) -> str:
    """BRK.B ↔ BRK-B 风格互换（两源与库内 ticker 的分隔符可能不一致）。"""
    return t.replace(".", "-").replace("-", ".")


def match_tickers(tickers: list[str], symbols: list[str]) -> tuple[set[str], list[str]]:
    """返回 (匹配到的 ticker 集合, 名单中未匹配到库内股票的 symbol 列表)。

    先精确匹配，未命中的再尝试 `.`↔`-` 互换匹配（双向）。
    """
    sym_set = set(symbols)
    swapped_syms = {_swap_dot_dash(s) for s in symbols}
    matched = {t for t in tickers
               if t in sym_set or t in swapped_syms or _swap_dot_dash(t) in sym_set}
    ticker_set = set(tickers)
    swapped_tickers = {_swap_dot_dash(t) for t in tickers}
    unmatched = [s for s in symbols
                 if s not in ticker_set and s not in swapped_tickers
                 and _swap_dot_dash(s) not in ticker_set]
    return matched, unmatched


def sync_stock_indexes(db, sp_symbols: list[str] | None = None,
                       ndx_symbols: list[str] | None = None) -> dict:
    """刷新全部股票的指数成分标记：先置 False，再按名单置 True。

    sp_symbols/ndx_symbols 缺省时现场拉取（executor 已在线程池拉取时可直接传入）。
    返回 {"sp500": n, "sp500_unmatched": [...], "ndx100": n, "ndx100_unmatched": [...]}。
    网络/解析失败由 fetch 函数直接抛异常，调用方决定如何兜底。
    """
    if sp_symbols is None:
        sp_symbols = fetch_sp500_symbols()
    if ndx_symbols is None:
        ndx_symbols = fetch_ndx100_symbols()

    stocks = db.scalars(select(Stock)).all()
    for st in stocks:
        st.in_sp500 = False
        st.in_ndx100 = False

    report: dict = {}
    for name, symbols, attr in (("sp500", sp_symbols, "in_sp500"),
                                ("ndx100", ndx_symbols, "in_ndx100")):
        matched, unmatched = match_tickers([st.ticker for st in stocks], symbols)
        for st in stocks:
            if st.ticker in matched:
                setattr(st, attr, True)
        report[name] = len(matched)
        report[f"{name}_unmatched"] = unmatched
        # 名单里在库内找不到的 symbol：只报告，不臆造补录
        if unmatched:
            log.warning("%s 成分有 %d 只未匹配到库内股票: %s",
                        name, len(unmatched), unmatched)
    db.commit()
    return report
