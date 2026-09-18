import json
import urllib.request
from sqlalchemy import select
from models import Stock

EDGAR_URL = "https://www.sec.gov/files/company_tickers.json"
HEADERS = {"User-Agent": "stock-god personal research wmh@example.com"}


def fetch_company_tickers() -> dict:
    req = urllib.request.Request(EDGAR_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upsert_stocks(session, data: dict) -> int:
    existing = {s.ticker: s for s in session.scalars(select(Stock)).all()}
    n = 0
    for item in data.values():
        ticker = item["ticker"].strip().upper()
        if not ticker:
            continue
        st = existing.get(ticker)
        if st is None:
            st = Stock(ticker=ticker)
            session.add(st)
        # 只更新 EDGAR 来源字段；name_cn（本地维护）与 is_favorite（用户数据）不得覆盖
        st.name_en = item.get("title")
        st.cik = item.get("cik_str")
        st.market = "US"
        n += 1
    session.commit()
    return n
