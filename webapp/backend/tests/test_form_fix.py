"""form_fix 核心逻辑单测：submissions 响应解析 + reportDate→form 匹配规则。

fetcher 一律注入 mock（返回手工构造的 submissions 结构，不造假网络数据）。
"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from db import Base
from models import Stock, Filing
from services.form_fix import build_report_form_map, fix_unknown_filings


def _recent(*records):
    """records: (form, reportDate, filingDate) → submissions filings.recent 形状。"""
    return {"form": [r[0] for r in records],
            "reportDate": [r[1] for r in records],
            "filingDate": [r[2] for r in records]}


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


# ---------- build_report_form_map：纯函数规则 ----------

def test_map_prefers_10k_over_ars_same_report_date():
    # 年报期同日有 ARS（股东年报）/NT 10-K 等杂项，只有精确 form 进映射
    m = build_report_form_map(_recent(
        ("ARS", "2025-09-28", "2026-01-22"),
        ("10-K", "2025-09-28", "2025-11-05"),
        ("10-Q", "2025-09-28", "2025-10-20"),
    ))
    assert m["2025-09-28"] == "10-K"


def test_map_falls_back_to_amendment():
    # 无原始 10-K 时才回退修订件 10-K/A
    m = build_report_form_map(_recent(("10-K/A", "2024-12-31", "2025-03-01")))
    assert m["2024-12-31"] == "10-K"
    # 原始与修订并存 → 原始胜出
    m2 = build_report_form_map(_recent(
        ("10-K", "2024-12-31", "2025-02-01"),
        ("10-K/A", "2024-12-31", "2025-03-01"),
    ))
    assert m2["2024-12-31"] == "10-K"


def test_map_ignores_non_periodic_forms_and_null_report_date():
    m = build_report_form_map(_recent(
        ("8-K", "2025-01-15", "2025-01-15"),
        ("S-8", "2025-02-01", "2025-02-01"),
        ("4", None, "2025-03-01"),
        ("6-K", "2025-06-30", "2025-07-10"),
    ))
    assert m == {"2025-06-30": "6-K"}


def test_map_tiebreak_earliest_filing_date():
    # 同 form 同 reportDate（罕见重复）→ filingDate 最早者胜
    m = build_report_form_map(_recent(
        ("10-Q", "2025-03-31", "2025-05-10"),
        ("10-Q", "2025-03-31", "2025-04-28"),
    ))
    assert m["2025-03-31"] == "10-Q"


def test_map_priority_order():
    m = build_report_form_map(_recent(
        ("20-F", "2024-12-31", "2025-04-01"),
        ("6-K", "2024-12-31", "2025-03-01"),
        ("10-K", "2023-12-31", "2024-02-01"),
    ))
    assert m["2024-12-31"] == "20-F" and m["2023-12-31"] == "10-K"


def test_map_empty_recent():
    assert build_report_form_map({}) == {}


# ---------- fix_unknown_filings：会话级行为（mock fetcher） ----------

def _mk_filing(session, ticker, cik, period, form="UNKNOWN"):
    st = session.scalar(select(Stock).where(Stock.ticker == ticker))
    if st is None:
        st = Stock(ticker=ticker, market="US", cik=cik)
        session.add(st)
        session.flush()
    f = Filing(stock_id=st.id, form_type=form, period=period,
               local_path=f"reports/sec_filings/{ticker}/{ticker.lower()}-{period}.htm")
    session.add(f)
    session.commit()
    return f


QCOM_RECENT = _recent(
    ("10-Q", "2026-06-28", "2026-07-29"),
    ("10-K", "2025-09-28", "2025-11-05"),
    ("ARS", "2025-09-28", "2026-01-22"),
    ("10-Q", "2025-06-29", "2025-07-30"),
)


def test_fix_updates_matched_rows():
    s = _session()
    f1 = _mk_filing(s, "QCOM", 804328, "2025-09-28")   # → 10-K（同日 ARS 不干扰）
    f2 = _mk_filing(s, "QCOM", 804328, "2026-06-28")   # → 10-Q
    _mk_filing(s, "QCOM", 804328, "2020-01-01")        # 映射不到 → 保持 UNKNOWN
    called = []
    stats = fix_unknown_filings(s, fetcher=lambda cik: (called.append(cik), QCOM_RECENT)[1])
    assert called == [804328]  # 整股票只拉一次
    s.refresh(f1), s.refresh(f2)
    assert f1.form_type == "10-K" and f2.form_type == "10-Q"
    assert stats["fixed"] == 2 and stats["total"] == 3
    assert stats["unmatched"] == [("QCOM", "2020-01-01", "no match")]


def test_fix_idempotent():
    s = _session()
    _mk_filing(s, "QCOM", 804328, "2025-09-28")
    assert fix_unknown_filings(s, fetcher=lambda cik: QCOM_RECENT)["fixed"] == 1
    stats2 = fix_unknown_filings(s, fetcher=lambda cik: QCOM_RECENT)
    assert stats2["total"] == 0 and stats2["fixed"] == 0  # 已修行不再是 UNKNOWN


def test_fix_dry_run_writes_nothing():
    s = _session()
    f = _mk_filing(s, "QCOM", 804328, "2025-09-28")
    stats = fix_unknown_filings(s, fetcher=lambda cik: QCOM_RECENT, dry_run=True)
    assert stats["fixed"] == 1
    s.refresh(f)
    assert f.form_type == "UNKNOWN"


def test_fix_skips_unique_constraint_conflict():
    s = _session()
    st = Stock(ticker="QCOM", market="US", cik=804328)
    s.add(st)
    s.flush()
    s.add(Filing(stock_id=st.id, form_type="10-K", period="2025-09-28",
                 local_path="reports/sec_filings/QCOM/existing.htm"))
    unknown = Filing(stock_id=st.id, form_type="UNKNOWN", period="2025-09-28",
                     local_path="reports/sec_filings/QCOM/qcom-20250928.htm")
    s.add(unknown)
    s.commit()
    stats = fix_unknown_filings(s, fetcher=lambda cik: QCOM_RECENT)
    s.refresh(unknown)
    assert unknown.form_type == "UNKNOWN"  # 冲突保持 UNKNOWN，不臆造
    assert stats["skipped_conflict"] == [("QCOM", "10-K", "2025-09-28")]


def test_fix_fetch_failure_keeps_unknown():
    s = _session()
    _mk_filing(s, "QCOM", 804328, "2025-09-28")
    def boom(cik):
        raise OSError("network down")
    stats = fix_unknown_filings(s, fetcher=boom)
    assert stats["fixed"] == 0 and stats["fetch_failed"] == ["QCOM"]
    f = s.scalar(select(Filing))
    assert f.form_type == "UNKNOWN"


def test_fix_stock_without_cik():
    s = _session()
    _mk_filing(s, "XXXX", None, "2025-09-28")
    stats = fix_unknown_filings(s, fetcher=lambda cik: QCOM_RECENT)
    assert stats["fixed"] == 0
    assert stats["unmatched"] == [("XXXX", "2025-09-28", "no CIK")]
