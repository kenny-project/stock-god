import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from db import Base
from models import Stock, Filing, Analysis, DcfReport, Task


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_create_stock_with_relations(session):
    st = Stock(ticker="NKE", name_en="NIKE Inc", cik=320187, market="US", exchange="NYSE")
    s = session
    s.add(st)
    s.flush()
    s.add_all([
        Filing(stock_id=st.id, form_type="UNKNOWN", period="2024-05-31",
               local_path="reports/sec_filings/NKE/nke-20240531/nke-20240531.htm"),
        Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024,
                 local_path="reports/sec_analysis/NKE/10-K_FY2024.md", metrics={"营收": 51362}),
        DcfReport(stock_id=st.id, growth=8.0, discount=10.0, years=5, safety=0.3,
                  local_path="reports/dcf/US.NKE_DCF.md", valuation={"intrinsic": 47099}),
        Task(task_type="download", stock_id=st.id, status="pending", params={"years": 5}),
    ])
    s.commit()
    got = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    assert got.market == "US"
    assert len(got.filings) == 1
    assert got.analyses[0].metrics["营收"] == 51362
    assert got.dcf_reports[0].valuation["intrinsic"] == 47099
    assert got.tasks[0].status == "pending"


def test_unique_ticker(session):
    s = session
    s.add(Stock(ticker="NKE"))
    s.commit()
    s.add(Stock(ticker="NKE"))
    with pytest.raises(IntegrityError):
        s.commit()


def test_index_flags_default_false(session):
    """in_sp500/in_ndx100 缺省 False（ORM default 与 server_default 双保险）。"""
    s = session
    s.add(Stock(ticker="NKE"))
    s.commit()
    got = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    assert got.in_sp500 is False
    assert got.in_ndx100 is False


def test_analysis_quarter_unique_constraint(session):
    """uq_analysis 四列：同 (stock, form, fy, quarter) 冲突；同财年年报(quarter=NULL)与季报可共存。"""
    s = session
    st = Stock(ticker="NKE")
    s.add(st)
    s.commit()
    s.add(Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024, quarter=None,
                   local_path="reports/sec_analysis/NKE/10-K_FY2024.md"))
    # 同财年的季报不与年报冲突
    s.add(Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=2024, quarter="2024Q3",
                   local_path="reports/sec_analysis/NKE/10-Q_2024Q3.md"))
    s.commit()
    # 再插一条同 (10-Q, 2024, 2024Q3) → 违反 uq_analysis
    s.add(Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=2024, quarter="2024Q3",
                   local_path="reports/sec_analysis/NKE/dup.md"))
    with pytest.raises(IntegrityError):
        s.commit()
