"""指数成分同步：fetch 解析、ticker 归一匹配、sync_stock_indexes 标记与重置。"""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import Base, json_dumps
import models  # noqa
from models import Stock
from services.indices import sync_stock_indexes


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, json_serializer=json_dumps)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Factory() as s:
        yield s


def _add(db, *tickers):
    for t in tickers:
        db.add(Stock(ticker=t, name_en=t, market="US"))
    db.commit()


def _get(db, ticker):
    return db.scalar(select(Stock).where(Stock.ticker == ticker))


def test_sync_marks_members_and_resets_stale_flags(db):
    """命中的置 True，未命中/此前误标的重置 False。"""
    _add(db, "AAPL", "MSFT", "NKE")
    # 预置脏标记：验证同步会先全部重置再按名单标记
    for st in db.scalars(select(Stock)).all():
        st.in_sp500 = st.in_ndx100 = True
    db.commit()
    report = sync_stock_indexes(db, sp_symbols=["AAPL"], ndx_symbols=["AAPL", "MSFT"])
    assert report["sp500"] == 1 and report["ndx100"] == 2
    aapl, msft, nke = (_get(db, t) for t in ("AAPL", "MSFT", "NKE"))
    assert aapl.in_sp500 and aapl.in_ndx100
    assert not msft.in_sp500 and msft.in_ndx100
    assert not nke.in_sp500 and not nke.in_ndx100


def test_ticker_normalization_dot_dash(db):
    """BRK.B vs BRK-B 等分隔符差异：精确不中后走 `.`↔`-` 互换匹配。"""
    _add(db, "BRK-B", "BF.B", "XXX")
    report = sync_stock_indexes(db, sp_symbols=["BRK.B", "BRK-B-STYLE"], ndx_symbols=["BF-B"])
    assert _get(db, "BRK-B").in_sp500 is True
    assert _get(db, "BF.B").in_ndx100 is True
    assert _get(db, "XXX").in_sp500 is False
    # BRK-B-STYLE 在库内无对应：计入未匹配名单
    assert report["sp500_unmatched"] == ["BRK-B-STYLE"]
    assert report["ndx100_unmatched"] == []


def test_unmatched_reported_when_absent_from_db(db):
    """名单里的 symbol 库内不存在 → 计数 0 并报告未匹配。"""
    _add(db, "NKE")
    report = sync_stock_indexes(db, sp_symbols=["AAPL", "NKE"], ndx_symbols=["TSLA"])
    assert report["sp500"] == 1
    assert report["sp500_unmatched"] == ["AAPL"]
    assert report["ndx100"] == 0
    assert report["ndx100_unmatched"] == ["TSLA"]


def test_fetcher_mock_injection(db, monkeypatch):
    """executor 不传名单时走模块 fetch 函数；测试可 monkeypatch 注入。"""
    import services.indices as m
    _add(db, "AAPL")
    monkeypatch.setattr(m, "fetch_sp500_symbols", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(m, "fetch_ndx100_symbols", lambda: [])
    report = sync_stock_indexes(db)
    assert report["sp500"] == 1 and report["sp500_unmatched"] == ["MSFT"]
    assert _get(db, "AAPL").in_sp500 is True


def test_fetch_sp500_parses_csv(monkeypatch):
    import services.indices as m
    csv_body = (b"Symbol,Security,GICS Sector,CIK\n"
                b"AAPL,Apple Inc.,Information Technology,320193\n"
                b"BRK.B,Berkshire Hathaway,Financials,1067983\n")
    monkeypatch.setattr(m, "_http_get", lambda url, headers=None, timeout=30: csv_body)
    assert m.fetch_sp500_symbols() == ["AAPL", "BRK.B"]


def test_fetch_ndx100_parses_json_with_browser_headers(monkeypatch):
    import services.indices as m
    payload = json.dumps({"data": {"data": {"rows": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]}}})
    seen = {}

    def fake_get(url, headers=None, timeout=30):
        seen["url"], seen["headers"] = url, headers
        return payload.encode()

    monkeypatch.setattr(m, "_http_get", fake_get)
    assert m.fetch_ndx100_symbols() == ["AAPL", "MSFT"]
    # 必须带浏览器 UA + Accept，否则 nasdaq 403
    assert "nasdaq100" in seen["url"]
    assert seen["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert seen["headers"]["Accept"] == "application/json"


def test_fetch_raises_on_bad_payload(monkeypatch):
    import services.indices as m
    monkeypatch.setattr(m, "_http_get", lambda url, headers=None, timeout=30: b"{}")
    with pytest.raises(ValueError):
        m.fetch_ndx100_symbols()
