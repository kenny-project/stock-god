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


def test_upsert_preserves_name_cn_and_favorite():
    """EDGAR 同步只更新 name_en/cik/market，不得覆盖已有 name_cn 与 is_favorite。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    # 独立样本：上面的 test_upsert 会原地改 SAMPLE，不能复用
    sample = {"9": {"cik_str": 320187, "ticker": "NKE", "title": "NIKE, Inc."}}
    with Session(engine) as s:
        s.add(Stock(ticker="NKE", name_cn="耐克", is_favorite=True))
        s.commit()
        upsert_stocks(s, sample)
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        assert st.name_cn == "耐克"
        assert st.is_favorite is True
        assert st.name_en == "NIKE, Inc."  # 新字段照常更新
