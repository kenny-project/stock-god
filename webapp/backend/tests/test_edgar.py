from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from db import Base
from models import Stock
from services.edgar import upsert_stocks

SAMPLE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 320187, "ticker": "NKE", "title": "NIKE, Inc."},
}


def test_upsert():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        upsert_stocks(s, SAMPLE)
        assert s.scalar(select(func.count(Stock.id))) == 2
        # 再次同步：更新而非重复插入
        SAMPLE["1"]["title"] = "NIKE Inc."
        upsert_stocks(s, SAMPLE)
        assert s.scalar(select(func.count(Stock.id))) == 2
        assert s.scalar(select(Stock).where(Stock.ticker == "NKE")).name_en == "NIKE Inc."
