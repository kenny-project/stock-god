import os
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db import Base
import models  # noqa
from main import app, set_engine_for_test
from api import tasks_api


class DummyExecutor:
    async def run_one(self, task):
        return

    async def cancel(self, task):
        return False


@pytest.fixture
def env():
    # StaticPool：内存库全局共享单连接，避免线程池端点拿到空库
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    set_engine_for_test(engine, Factory)
    tasks_api.set_executor(DummyExecutor())
    with Factory() as s:
        from models import Stock, Filing, Analysis, Task
        st = Stock(ticker="NKE", market="US")
        s.add(st)
        s.flush()
        f = Filing(stock_id=st.id, form_type="UNKNOWN", period="2021-08-31",
                   local_path="reports/sec_filings/NKE/nke-20210831.htm")
        a = Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2025,
                     local_path="reports/sec_analysis/NKE/10-K_FY2025.md", metrics={"营收": 51362})
        t = Task(task_type="download", stock_id=st.id, status="failed",
                 error_code="SCRIPT_EXIT_NONZERO", error_summary="exit=1",
                 log_path="logs/tasks/task_test.log")
        s.add_all([f, a, t])
        s.commit()
        ids = (f.id, a.id, t.id)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, Factory, ids


async def test_analysis_content(env, tmp_path, monkeypatch):
    client, _, ids = env
    _, aid, _ = ids
    # 报告正文端点从磁盘读取：用 tmp root 固定文件，
    # 不依赖真实 reports/ 目录（可能被分析任务重建/清理）
    root = tmp_path / "root"
    base = root / "reports" / "sec_analysis" / "NKE"
    base.mkdir(parents=True)
    (base / "10-K_FY2025.md").write_text("# NKE 10-K FY2025\n", encoding="utf-8")
    monkeypatch.setattr("api.reports.ROOT", str(root))
    async with client as c:
        r = await c.get("/api/stocks/NKE/analyses")
        assert r.status_code == 200 and len(r.json()) == 1
        r = await c.get(f"/api/stocks/NKE/analyses/{aid}")
        assert r.status_code == 200
        body = r.json()
        assert body["metrics"]["营收"] == 51362
        assert "# NKE 10-K FY2025" in body["markdown"]


async def test_analysis_404(env):
    client, _, _ = env
    async with client as c:
        assert (await c.get("/api/stocks/NKE/analyses/9999")).status_code == 404
        assert (await c.get("/api/stocks/XXXX/analyses")).status_code == 404


async def test_filing_file_download(env):
    client, _, ids = env
    fid, _, _ = ids
    async with client as c:
        r = await c.get(f"/api/filings/{fid}/file")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # 默认 inline：浏览器按 Content-Type 预览打开
        assert r.headers["content-disposition"] == "inline"
        # download=1 → attachment，文件名取 local_path basename（EDGAR 下载名，纯 ASCII）
        r2 = await c.get(f"/api/filings/{fid}/file", params={"download": True})
        assert r2.status_code == 200
        assert r2.headers["content-disposition"] == \
            'attachment; filename="nke-20210831.htm"'
        assert "text/html" in r2.headers["content-type"]


async def test_filing_missing_404(env):
    client, _, _ = env
    async with client as c:
        assert (await c.get("/api/filings/9999/file")).status_code == 404


async def test_task_log(env, tmp_path, monkeypatch):
    client, _, ids = env
    _, _, tid = ids
    log = tmp_path / "task_test.log"
    log.write_text("line1\nline2\nline3\n", encoding="utf-8")
    monkeypatch.setattr("api.reports.safe_log_path", lambda p: str(log))
    async with client as c:
        r = await c.get(f"/api/tasks/{tid}/log")
        assert r.status_code == 200
        body = r.json()
        assert "line3" in body["content"]  # offset<=0 → 尾部 64KB
        assert body["next_offset"] == log.stat().st_size
        # 用 next_offset 续读到 EOF
        r2 = await c.get(f"/api/tasks/{tid}/log", params={"offset": body["next_offset"]})
        assert r2.status_code == 200
        assert r2.json()["content"] == ""
        assert r2.json()["next_offset"] == log.stat().st_size


async def test_task_log_tail_64k(env, tmp_path, monkeypatch):
    client, _, ids = env
    _, _, tid = ids
    log = tmp_path / "task_big.log"
    log.write_text("HEAD\n" + "x" * 70000 + "\nTAIL\n", encoding="utf-8")
    monkeypatch.setattr("api.reports.safe_log_path", lambda p: str(log))
    async with client as c:
        body = (await c.get(f"/api/tasks/{tid}/log")).json()
        assert "HEAD" not in body["content"]  # 只保留尾部 64KB
        assert "TAIL" in body["content"]
        assert body["size"] == log.stat().st_size
        assert body["next_offset"] == log.stat().st_size


def test_safe_log_path_rejects_escape():
    from api.reports import safe_log_path
    from db import ROOT
    assert safe_log_path("../../etc/passwd") is None
    assert safe_log_path("logs/tasks/../other/x.log") is None
    assert safe_log_path("logs/tasks2/x.log") is None  # 兄弟目录
    assert safe_log_path("logs/tasks/ok.log") == os.path.realpath(
        os.path.join(ROOT, "logs/tasks/ok.log"))


async def test_dcf_list_and_content(env, tmp_path, monkeypatch):
    client, Factory, _ = env
    # 正文端点从磁盘读文件：用 tmp root 固定文件，
    # 不依赖真实 reports/ 目录（可能被估值任务/删除操作清理）
    root = tmp_path / "root"
    base = root / "reports" / "dcf"
    base.mkdir(parents=True)
    (base / "US.NKE_DCF.md").write_text("# NKE DCF 估值分析\n", encoding="utf-8")
    monkeypatch.setattr("api.reports.ROOT", str(root))
    with Factory() as s:
        from models import Stock, DcfReport
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        d = DcfReport(stock_id=st.id, local_path="reports/dcf/US.NKE_DCF.md",
                      valuation={"intrinsic_value_musd": 47099})
        s.add(d)
        s.commit()
        did = d.id
    async with client as c:
        r = await c.get("/api/stocks/NKE/dcf")
        assert r.status_code == 200 and len(r.json()) == 1
        r = await c.get(f"/api/stocks/NKE/dcf/{did}")
        assert r.status_code == 200
        assert r.json()["valuation"]["intrinsic_value_musd"] == 47099
        assert "DCF 估值分析" in r.json()["markdown"]


async def test_cross_stock_ownership_404(env):
    client, Factory, ids = env
    _, nke_aid, _ = ids
    with Factory() as s:
        from models import Stock, Analysis, DcfReport
        st = Stock(ticker="MSFT", market="US")
        s.add(st)
        s.flush()
        a = Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024,
                     local_path="reports/sec_analysis/MSFT/10-K_FY2024.md", metrics={})
        d = DcfReport(stock_id=st.id, local_path="reports/dcf/US.MSFT_DCF.md",
                      valuation={"intrinsic_value_musd": 1})
        s.add_all([a, d])
        s.commit()
        aid, did = a.id, d.id
    async with client as c:
        # MSFT 的 analysis/dcf 挂在 NKE ticker 下 → 404
        assert (await c.get(f"/api/stocks/NKE/analyses/{aid}")).status_code == 404
        assert (await c.get(f"/api/stocks/NKE/dcf/{did}")).status_code == 404
        # NKE 的 analysis 挂在 MSFT ticker 下 → 404
        assert (await c.get(f"/api/stocks/MSFT/analyses/{nke_aid}")).status_code == 404


async def test_filing_escape_404(env):
    client, Factory, _ = env
    with Factory() as s:
        from models import Stock, Filing
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        f = Filing(stock_id=st.id, form_type="10-K", period="2024",
                   local_path="../evil.htm")
        s.add(f)
        s.commit()
        fid = f.id
    async with client as c:
        assert (await c.get(f"/api/filings/{fid}/file")).status_code == 404


async def test_analysis_path_escape_404(env, tmp_path):
    client, Factory, _ = env
    outside = tmp_path / "evil.md"  # ROOT 之外的真实文件
    outside.write_text("secret", encoding="utf-8")
    with Factory() as s:
        from models import Stock, Analysis
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        a = Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2026,
                     local_path=str(outside), metrics={})
        s.add(a)
        s.commit()
        aid = a.id
    async with client as c:
        r = await c.get(f"/api/stocks/NKE/analyses/{aid}")
        assert r.status_code == 404  # 文件存在但逃逸 ROOT，必须拒绝


def _add_stock(Factory, ticker):
    with Factory() as s:
        from models import Stock
        st = Stock(ticker=ticker, market="US")
        s.add(st)
        s.commit()
        return st.id


async def test_analyses_pairing_same_count(env):
    """filings 与 analyses 同 form_type 数量一致 → 升序尾部对齐一一配对，列表按 period 降序。"""
    client, Factory, _ = env
    sid = _add_stock(Factory, "QCOM")
    with Factory() as s:
        from models import Filing, Analysis
        for fy, q, period in [(2025, "2025Q2", "2025-06-29"), (2025, "2025Q3", "2025-09-28"),
                              (2025, "2025Q4", "2025-12-28"), (2026, "2026Q1", "2026-03-29")]:
            s.add(Filing(stock_id=sid, form_type="10-Q", period=period,
                         local_path=f"reports/sec_filings/QCOM/10-Q_{period}.htm"))
            s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=fy, quarter=q,
                           local_path=f"reports/sec_analysis/QCOM/10-Q_{q}.md", metrics={}))
        s.commit()
    async with client as c:
        body = (await c.get("/api/stocks/QCOM/analyses")).json()
        assert [(a["quarter"], a["period"]) for a in body] == [
            ("2026Q1", "2026-03-29"), ("2025Q4", "2025-12-28"),
            ("2025Q3", "2025-09-28"), ("2025Q2", "2025-06-29")]


async def test_analyses_pairing_extra_analyses(env):
    """分析多于财报：尾部对齐后老分析配不到 period 置 None，且排在列表最后。"""
    client, Factory, _ = env
    sid = _add_stock(Factory, "QCOM")
    with Factory() as s:
        from models import Filing, Analysis
        for period in ("2026-03-29", "2026-06-28"):
            s.add(Filing(stock_id=sid, form_type="10-Q", period=period,
                         local_path=f"reports/sec_filings/QCOM/10-Q_{period}.htm"))
        for fy, q in [(2025, "2025Q4"), (2026, "2026Q1"), (2026, "2026Q2"), (2026, "2026Q3")]:
            s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=fy, quarter=q,
                           local_path=f"reports/sec_analysis/QCOM/10-Q_{q}.md", metrics={}))
        s.commit()
    async with client as c:
        body = (await c.get("/api/stocks/QCOM/analyses")).json()
        assert [(a["quarter"], a["period"]) for a in body] == [
            ("2026Q3", "2026-06-28"), ("2026Q2", "2026-03-29"),
            ("2026Q1", None), ("2025Q4", None)]


async def test_analyses_pairing_extra_filings(env):
    """财报多于分析：尾部对齐后老端多出的 filings 被 zip 丢弃，分析全部配到正确的最新 period。"""
    client, Factory, _ = env
    sid = _add_stock(Factory, "QCOM")
    with Factory() as s:
        from models import Filing, Analysis
        for period in ("2025-03-30", "2025-06-29", "2026-03-30", "2026-06-29"):
            s.add(Filing(stock_id=sid, form_type="10-Q", period=period,
                         local_path=f"reports/sec_filings/QCOM/10-Q_{period}.htm"))
        # period=None 的 filing 不进配对池，不影响其余配对结果
        s.add(Filing(stock_id=sid, form_type="10-Q", period=None,
                     local_path="reports/sec_filings/QCOM/10-Q_unknown.htm"))
        for fy, q in [(2026, "2026Q2"), (2026, "2026Q3")]:
            s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=fy, quarter=q,
                           local_path=f"reports/sec_analysis/QCOM/10-Q_{q}.md", metrics={}))
        s.commit()
    async with client as c:
        body = (await c.get("/api/stocks/QCOM/analyses")).json()
        # 2 条分析各自配到最新 2 个 period，老端 2 份财报（含 period=None）被丢弃
        assert [(a["quarter"], a["period"]) for a in body] == [
            ("2026Q3", "2026-06-29"), ("2026Q2", "2026-03-30")]


async def test_analyses_pairing_mixed_forms(env):
    """多 form_type 各自配对；UNKNOWN filing 走旧 zip 不与语义分析配对；
    年报与季报按 period 降序混排，None 置底。
    财年日历：10-K 期次 5 月 → M=5（财年止 5 月），10-Q 期次据此推（财年, 季度）。"""
    client, Factory, _ = env
    sid = _add_stock(Factory, "MSFT")
    with Factory() as s:
        from models import Filing, Analysis
        # 10-K：2026-05-31 → FY2026、2025-05-31 → FY2025，与两条分析精确配对
        s.add(Filing(stock_id=sid, form_type="10-K", period="2025-05-31", local_path="f1.htm"))
        s.add(Filing(stock_id=sid, form_type="10-K", period="2026-05-31", local_path="f2.htm"))
        s.add(Analysis(stock_id=sid, form_type="10-K", fiscal_year=2025, local_path="a1.md", metrics={}))
        s.add(Analysis(stock_id=sid, form_type="10-K", fiscal_year=2026, local_path="a2.md", metrics={}))
        # 10-Q：M=5 → 2024-08-31=(FY2025,Q1)、2024-11-30=(FY2025,Q2)，与标签一致
        s.add(Filing(stock_id=sid, form_type="10-Q", period="2024-08-31", local_path="f3.htm"))
        s.add(Filing(stock_id=sid, form_type="10-Q", period="2024-11-30", local_path="f4.htm"))
        s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=2024, quarter="2024Q3",
                       local_path="a3.md", metrics={}))
        s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=2025, quarter="2025Q1",
                       local_path="a4.md", metrics={}))
        s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=2025, quarter="2025Q2",
                       local_path="a5.md", metrics={}))
        # UNKNOWN filing（历史遗留）不与任何分析配对
        s.add(Filing(stock_id=sid, form_type="UNKNOWN", period="2024-05-31", local_path="f5.htm"))
        s.commit()
    async with client as c:
        body = (await c.get("/api/stocks/MSFT/analyses")).json()
        assert [(a["form_type"], a["quarter"], a["period"]) for a in body] == [
            ("10-K", None, "2026-05-31"),
            ("10-K", None, "2025-05-31"),
            ("10-Q", "2025Q2", "2024-11-30"),
            ("10-Q", "2025Q1", "2024-08-31"),
            ("10-Q", "2024Q3", None)]


async def test_analyses_pairing_avgo_regression(env):
    """AVGO 错位回归：分析是 filing 的非后缀子集（老分析 + 缺最新季度）时，
    尾部对齐会把 2021Q1 老分析错配到 2025-02-02 行（M=11 财年）。
    语义配对后各自精确落位：2021Q1 ↔ 2021-01-31，最新两季无分析配 None。"""
    client, Factory, _ = env
    sid = _add_stock(Factory, "AVGO")
    with Factory() as s:
        from models import Filing, Analysis
        # AVGO 真实期次：FY 止 11 月初，Q1 止次年 1/2 月
        for period in ("2021-01-31", "2021-05-02", "2024-02-04", "2024-05-05", "2024-08-04",
                       "2025-02-02", "2025-05-04", "2025-08-03"):
            s.add(Filing(stock_id=sid, form_type="10-Q", period=period,
                         local_path=f"reports/sec_filings/AVGO/avgo-{period}.htm"))
        s.add(Filing(stock_id=sid, form_type="10-K", period="2024-11-03",
                     local_path="reports/sec_filings/AVGO/avgo-20241103.htm"))
        s.add(Filing(stock_id=sid, form_type="10-K", period="2025-11-02",
                     local_path="reports/sec_filings/AVGO/avgo-20251102.htm"))
        # 只生成了 3 条老分析（2021Q1/2024Q2/2024Q3）+ 1 条年报
        for fy, q in [(2021, "2021Q1"), (2024, "2024Q2"), (2024, "2024Q3")]:
            s.add(Analysis(stock_id=sid, form_type="10-Q", fiscal_year=fy, quarter=q,
                           local_path=f"reports/sec_analysis/AVGO/10-Q_{q}.md", metrics={}))
        s.add(Analysis(stock_id=sid, form_type="10-K", fiscal_year=2025,
                       local_path="reports/sec_analysis/AVGO/10-K_FY2025.md", metrics={}))
        s.commit()
    async with client as c:
        body = (await c.get("/api/stocks/AVGO/analyses")).json()
        got = {a["quarter"] or f'FY{a["fiscal_year"]}': a["period"] for a in body}
        assert got == {"2021Q1": "2021-01-31",   # 旧实现错配到 2025-02-02
                       "2024Q2": "2024-05-05",
                       "2024Q3": "2024-08-04",
                       "FY2025": "2025-11-02"}


async def test_clear_analyses(env, tmp_path, monkeypatch):
    """清空分析：删全部行 + 删 sec_analysis/{ticker}/ 内文件；逃逸该目录的文件保留，仅删行。"""
    client, Factory, _ = env
    root = tmp_path / "root"
    base = root / "reports" / "sec_analysis" / "NKE"
    base.mkdir(parents=True)
    inside = base / "10-K_FY2025.md"       # 对应 env fixture 已有的 NKE 10-K 分析
    inside.write_text("# A", encoding="utf-8")
    inside2 = base / "10-Q_2026Q3.md"
    inside2.write_text("# B", encoding="utf-8")
    outside = root / "reports" / "evil.md"  # 逃逸出 sec_analysis/NKE/ 的文件
    outside.write_text("keep", encoding="utf-8")
    with Factory() as s:
        from models import Stock, Analysis
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        s.add(Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=2026, quarter="2026Q3",
                       local_path="reports/sec_analysis/NKE/10-Q_2026Q3.md", metrics={}))
        s.add(Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=2026, quarter="2026Q2",
                       local_path="reports/sec_analysis/NKE/../../evil.md", metrics={}))
        s.commit()
    monkeypatch.setattr("api.reports.ROOT", str(root))
    async with client as c:
        assert (await c.delete("/api/stocks/XXXX/analyses")).status_code == 404
        r = await c.delete("/api/stocks/NKE/analyses")
        assert r.status_code == 200 and r.json() == {"deleted": 3}  # fixture 1 条 + 新增 2 条
        assert not inside.exists() and not inside2.exists()
        assert outside.exists()  # 路径校验：逃逸文件不能被删
        with Factory() as s:
            st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
            assert s.scalar(select(func.count(Analysis.id))
                            .where(Analysis.stock_id == st.id)) == 0
        # 再删一次：已无分析 → deleted 0
        r2 = await c.delete("/api/stocks/NKE/analyses")
        assert r2.status_code == 200 and r2.json() == {"deleted": 0}


async def test_delete_dcf(env, tmp_path, monkeypatch):
    """删除单条 DCF：删行 + 删 reports/dcf/ 内文件；逃逸该目录的文件保留，仅删行。"""
    client, Factory, _ = env
    root = tmp_path / "root"
    base = root / "reports" / "dcf"
    base.mkdir(parents=True)
    inside = base / "US.NKE_DCF_1.md"
    inside.write_text("# DCF", encoding="utf-8")
    outside = root / "reports" / "evil.md"  # 逃逸出 reports/dcf/ 的文件
    outside.write_text("keep", encoding="utf-8")
    with Factory() as s:
        from models import Stock, DcfReport
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        d1 = DcfReport(stock_id=st.id, local_path="reports/dcf/US.NKE_DCF_1.md", valuation={})
        d2 = DcfReport(stock_id=st.id, local_path="reports/dcf/../evil.md", valuation={})
        s.add_all([d1, d2])
        s.commit()
        id1, id2 = d1.id, d2.id
    monkeypatch.setattr("api.reports.ROOT", str(root))
    async with client as c:
        assert (await c.delete("/api/stocks/XXXX/dcf/1")).status_code == 404
        assert (await c.delete("/api/stocks/NKE/dcf/9999")).status_code == 404
        r = await c.delete(f"/api/stocks/NKE/dcf/{id1}")
        assert r.status_code == 200 and r.json() == {"deleted": 1}
        assert not inside.exists()
        r2 = await c.delete(f"/api/stocks/NKE/dcf/{id2}")
        assert r2.status_code == 200 and r2.json() == {"deleted": 1}
        assert outside.exists()  # 路径校验：逃逸文件不能被删
        with Factory() as s:
            assert s.scalar(select(func.count(DcfReport.id))) == 0
        # 已删 → 再删 404
        assert (await c.delete(f"/api/stocks/NKE/dcf/{id1}")).status_code == 404


async def test_delete_dcf_file_missing_ok(env, tmp_path, monkeypatch):
    """文件缺失不阻塞删行。"""
    client, Factory, _ = env
    root = tmp_path / "root"
    (root / "reports" / "dcf").mkdir(parents=True)
    with Factory() as s:
        from models import Stock, DcfReport
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        d = DcfReport(stock_id=st.id, local_path="reports/dcf/ghost.md", valuation={})
        s.add(d)
        s.commit()
        did = d.id
    monkeypatch.setattr("api.reports.ROOT", str(root))
    async with client as c:
        r = await c.delete(f"/api/stocks/NKE/dcf/{did}")
        assert r.status_code == 200 and r.json() == {"deleted": 1}


async def test_delete_dcf_cross_stock_404(env):
    """别的股票的 dcf_id 挂到本 ticker 下删除 → 404，行保留。"""
    client, Factory, _ = env
    with Factory() as s:
        from models import Stock, DcfReport
        st = Stock(ticker="MSFT", market="US")
        s.add(st)
        s.flush()
        d = DcfReport(stock_id=st.id, local_path="reports/dcf/US.MSFT_DCF.md", valuation={})
        s.add(d)
        s.commit()
        did = d.id
    async with client as c:
        assert (await c.delete(f"/api/stocks/NKE/dcf/{did}")).status_code == 404
        with Factory() as s:
            assert s.get(DcfReport, did) is not None


async def test_delete_dcf_conflict_409(env):
    """有 dcf 任务 pending/running 时删除被 409 拦截，行保留；任务结束后可删。"""
    client, Factory, _ = env
    with Factory() as s:
        from models import Stock, Task, DcfReport
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        d = DcfReport(stock_id=st.id, local_path="reports/dcf/US.NKE_DCF.md", valuation={})
        s.add(d)
        s.add(Task(task_type="dcf", stock_id=st.id, status="running"))
        s.add(Task(task_type="download", stock_id=st.id, status="running"))  # 无关类型不拦截
        s.commit()
        did = d.id
    async with client as c:
        r = await c.delete(f"/api/stocks/NKE/dcf/{did}")
        assert r.status_code == 409
        assert r.json() == {"detail": "该股票有 DCF 任务进行中，请先取消或等待完成"}
        with Factory() as s:
            assert s.get(DcfReport, did) is not None  # 未被删除
        # 任务结束（failed）后恢复删除
        with Factory() as s:
            t = s.scalar(select(Task).where(Task.task_type == "dcf"))
            t.status = "failed"
            s.commit()
        r2 = await c.delete(f"/api/stocks/NKE/dcf/{did}")
        assert r2.status_code == 200 and r2.json() == {"deleted": 1}


async def test_clear_analyses_conflict_409(env):
    """有 analysis/dcf 任务 pending/running 时清空被 409 拦截，不删任何分析；任务结束后可清。"""
    client, Factory, _ = env
    with Factory() as s:
        from models import Stock, Task
        st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
        s.add(Task(task_type="analysis", stock_id=st.id, status="running"))
        s.add(Task(task_type="dcf", stock_id=st.id, status="pending"))
        s.add(Task(task_type="download", stock_id=st.id, status="running"))  # 无关类型不拦截
        s.commit()
    async with client as c:
        r = await c.delete("/api/stocks/NKE/analyses")
        assert r.status_code == 409
        assert r.json() == {"detail": "该股票有分析/DCF 任务进行中，请先取消或等待完成"}
        with Factory() as s:
            from models import Stock, Task, Analysis
            st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
            assert s.scalar(select(func.count(Analysis.id))
                            .where(Analysis.stock_id == st.id)) == 1  # 未被删除
        # 任务结束（failed）后恢复正常清空
        with Factory() as s:
            for t in s.scalars(select(Task).where(Task.stock_id == st.id)).all():
                t.status = "failed"
            s.commit()
        r2 = await c.delete("/api/stocks/NKE/analyses")
        assert r2.status_code == 200 and r2.json() == {"deleted": 1}

