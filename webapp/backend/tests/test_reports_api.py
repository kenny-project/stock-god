import os
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
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
        a = Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024,
                     local_path="reports/sec_analysis/NKE/10-K_FY2024.md", metrics={"营收": 51362})
        t = Task(task_type="download", stock_id=st.id, status="failed",
                 error_code="SCRIPT_EXIT_NONZERO", error_summary="exit=1",
                 log_path="logs/tasks/task_test.log")
        s.add_all([f, a, t])
        s.commit()
        ids = (f.id, a.id, t.id)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, Factory, ids


async def test_analysis_content(env):
    client, _, ids = env
    _, aid, _ = ids
    async with client as c:
        r = await c.get("/api/stocks/NKE/analyses")
        assert r.status_code == 200 and len(r.json()) == 1
        r = await c.get(f"/api/stocks/NKE/analyses/{aid}")
        assert r.status_code == 200
        body = r.json()
        assert body["metrics"]["营收"] == 51362
        assert "# NKE 10-K FY2024" in body["markdown"]


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


async def test_dcf_list_and_content(env):
    client, Factory, _ = env
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
        a = Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2025,
                     local_path=str(outside), metrics={})
        s.add(a)
        s.commit()
        aid = a.id
    async with client as c:
        r = await c.get(f"/api/stocks/NKE/analyses/{aid}")
        assert r.status_code == 404  # 文件存在但逃逸 ROOT，必须拒绝
