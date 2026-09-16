import os
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from db import Base
from models import Stock, Filing, Analysis, DcfReport
from services.register import register_download, register_analysis, register_dcf


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Stock(ticker="NKE", market="US"))
    s.commit()
    return s


def test_register_download_real_files():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_download(s, st)  # 扫描真实 reports/sec_filings/NKE/
    assert n > 0
    assert s.scalar(select(func.count(Filing.id))) == n
    from db import ROOT
    for f in s.scalars(select(Filing)).all():
        assert os.path.exists(os.path.join(ROOT, f.local_path))


def test_register_download_idempotent():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n1 = register_download(s, st)
    n2 = register_download(s, st)
    assert n1 > 0 and n2 == 0  # 二次登记不重复


def test_register_analysis_real_files():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_analysis(s, st)
    assert n >= 5  # 现有 10-K FY2022..FY2026
    a = s.scalar(select(Analysis).where(Analysis.fiscal_year == 2024))
    assert a.metrics["净利润"] == 5700


def test_register_dcf_real_file():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_dcf(s, st)
    assert n >= 1
    d = s.scalars(select(DcfReport)).first()
    assert d.valuation["intrinsic_value_musd"] == 47099
