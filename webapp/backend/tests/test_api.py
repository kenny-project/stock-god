import asyncio
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db import Base, json_dumps
import models  # noqa
from main import app, set_engine_for_test


class DummyExecutor:
    def __init__(self):
        self.calls = []  # 记录 run_one 收到的任务，供断言调度行为

    async def run_one(self, task):
        self.calls.append(task)

    async def cancel(self, task):
        return False


_dummy_executor: DummyExecutor | None = None  # 指向当前 fixture 装的 DummyExecutor，供测试断言
_factory = None  # 当前 fixture 的 sessionmaker，供测试伪造 running 状态 / 读 DB 终态


@pytest.fixture
def client():
    global _dummy_executor, _factory
    global _dummy_executor
    # StaticPool：内存库全局共享单连接。FastAPI 同步端点跑在线程池里，
    # sqlite:// 默认的 SingletonThreadPool 会给每个线程一个空库（no such table）
    # json_serializer 与 db.make_engine 一致：中文不转义，JSON 文本列可 ilike
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, json_serializer=json_dumps)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    set_engine_for_test(engine, Factory)
    _factory = Factory
    import api.tasks_api as tasks_api
    _dummy_executor = DummyExecutor()
    tasks_api.set_executor(_dummy_executor)
    from models import Stock
    with Factory() as s:
        s.add(Stock(ticker="NKE", name_en="NIKE, Inc.", market="US"))
        s.commit()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_list_stocks(client):
    async with client as c:
        r = await c.get("/api/stocks?q=NIKE")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1 and data["items"][0]["ticker"] == "NKE"


async def test_stock_detail_404(client):
    async with client as c:
        r = await c.get("/api/stocks/XXXX")
        assert r.status_code == 404


async def test_create_task_conflict(client):
    async with client as c:
        body = {"task_type": "analysis", "ticker": "NKE", "params": {}}
        r1 = await c.post("/api/tasks", json=body)
        assert r1.status_code == 200
        r2 = await c.post("/api/tasks", json=body)
        assert r2.status_code == 409


async def test_create_row_task_conflict_semantics(client):
    """行内单文件任务冲突语义：整股任务（无 file）在跑 → 行任务被拒（覆盖重叠）；
    同一文件的行任务在跑 → 拒；不同文件的行任务 → 允许并行排队。"""
    from models import Filing
    async with client as c:
        with _factory() as s:
            st = s.query(models.Stock).filter_by(ticker="NKE").one()
            s.add_all([Filing(stock_id=st.id, form_type="10-K", period="2022-05-31",
                              local_path="reports/sec_filings/NKE/nke-20220531.htm"),
                       Filing(stock_id=st.id, form_type="10-Q", period="2022-02-28",
                              local_path="reports/sec_filings/NKE/nke-20220228.htm")])
            s.commit()
            f1, f2 = s.query(Filing).order_by(Filing.id).all()
        # 行任务 A（f1）
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE",
                                             "params": {"filing_id": f1.id}})
        assert r.status_code == 200
        # 不同文件行任务 B → 允许并行
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE",
                                             "params": {"filing_id": f2.id}})
        assert r.status_code == 200
        # 同文件行任务 → 409
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE",
                                             "params": {"filing_id": f1.id}})
        assert r.status_code == 409
        # 清掉后跑整股任务，行任务再点 → 409（整股覆盖重叠）
        for t in (await c.get("/api/tasks")).json():
            await c.post(f"/api/tasks/{t['id']}/cancel")
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE", "params": {}})
        assert r.status_code == 200
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE",
                                             "params": {"filing_id": f1.id}})
        assert r.status_code == 409


async def test_create_task_bad_type(client):
    async with client as c:
        r = await c.post("/api/tasks", json={"task_type": "nope", "ticker": "NKE"})
        assert r.status_code == 422


async def test_create_analysis_task_resolves_filing_file(client):
    """行内单文件分析：params.filing_id → 后端解析该行财报主文档文件名存入 params.file，
    runner 据此构造 --file 单文件命令；filing 不属于该股/不存在 → 404。"""
    from models import Filing
    async with client as c:
        with _factory() as s:
            st = s.query(models.Stock).filter_by(ticker="NKE").one()
            f = Filing(stock_id=st.id, form_type="10-K", period="2022-05-31",
                       local_path="reports/sec_filings/NKE/nke-20220531.htm")
            s.add(f)
            s.commit()
            fid = f.id
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE",
                                             "params": {"filing_id": fid, "force": True}})
        assert r.status_code == 200
        assert r.json()["params"]["file"] == "nke-20220531.htm"
        # filing_id 不属于该股 → 404
        r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE",
                                             "params": {"filing_id": fid + 99999}})
        assert r.status_code == 404


async def test_task_flow_and_cancel(client):
    async with client as c:
        r = await c.get("/api/tasks")
        assert r.status_code == 200
        # 创建后立即取消
        r = await c.post("/api/tasks", json={"task_type": "dcf", "ticker": "NKE", "params": {"years": 5}})
        tid = r.json()["id"]
        r = await c.post(f"/api/tasks/{tid}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        r = await c.post(f"/api/tasks/{tid}/cancel")
        assert r.status_code == 409


async def test_cancel_running_task_writes_cancelled_before_kill(client):
    """取消 running 任务：API 必须先落库 cancelled 终态再终止子进程。
    否则子进程被 SIGTERM（exit -15）后，executor finally 读到的权威状态仍是
    running，任务会被记为 failed 而非 cancelled。"""
    import api.tasks_api as tasks_api
    from models import Task

    seen_at_kill = []  # executor 终止子进程时读到的 DB 状态

    class RunningExecutor(DummyExecutor):
        async def cancel(self, task):
            with _factory() as s:
                seen_at_kill.append(s.get(Task, task.id).status)
            return True

    tasks_api.set_executor(RunningExecutor())
    async with client as c:
        r = await c.post("/api/tasks", json={"task_type": "dcf", "ticker": "NKE", "params": {}})
        tid = r.json()["id"]
        # 伪造任务已进入 running（真实路径由 executor.run_one 置位）
        with _factory() as s:
            t = s.get(Task, tid)
            t.status = "running"
            t.started_at = datetime.now()
            s.commit()
        r = await c.post(f"/api/tasks/{tid}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        assert r.json()["finished_at"] is not None
    # 杀进程时 DB 已是 cancelled（写库先于终止子进程）
    assert seen_at_kill == ["cancelled"]
    with _factory() as s:
        t = s.get(Task, tid)
        assert t.status == "cancelled"  # 终态是已取消而非 failed
        assert t.error_summary == "任务被手动取消"


async def test_created_task_held_by_module_ref(client):
    """fire-and-forget 任务必须被模块级强引用防 GC，结束后自动移除。"""
    import api.tasks_api as tasks_api

    gate = asyncio.Event()

    class BlockedExecutor(DummyExecutor):
        async def run_one(self, task):
            await gate.wait()

    tasks_api.set_executor(BlockedExecutor())
    try:
        async with client as c:
            r = await c.post("/api/tasks", json={"task_type": "analysis", "ticker": "NKE", "params": {}})
            assert r.status_code == 200
            # 运行中：模块级强引用存在，任务不会被 GC
            assert len(tasks_api._bg) == 1
            gate.set()
            pending = list(tasks_api._bg)
            await asyncio.gather(*pending)
            await asyncio.sleep(0)  # 让 done_callback 先于断言执行
            assert tasks_api._bg == set()
    finally:
        tasks_api.set_executor(DummyExecutor())


async def test_index_and_has_filings_filters(client):
    """index(sp500/ndx100) 与 has_filings 过滤，且与 q/favorite 组合正确。"""
    from models import Filing, Stock
    from sqlalchemy import select
    async with client as c:
        with _factory() as s:
            nke = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
            nke.in_sp500 = True
            s.add(Stock(ticker="AAPL", name_en="Apple", market="US", in_ndx100=True))
            s.add(Filing(stock_id=nke.id, form_type="10-K", local_path="/tmp/a.htm"))
            s.commit()
        async def tickers(**params):
            r = await c.get("/api/stocks", params=params)
            assert r.status_code == 200
            return [i["ticker"] for i in r.json()["items"]]
        assert await tickers(index="sp500") == ["NKE"]
        assert await tickers(index="ndx100") == ["AAPL"]
        assert await tickers(has_filings="true") == ["NKE"]
        # 组合：指数 + 已下载 + 搜索
        assert await tickers(index="sp500", has_filings="true") == ["NKE"]
        assert await tickers(index="sp500", q="NKE") == ["NKE"]
        assert await tickers(index="sp500", q="AAPL") == []
        assert await tickers(index="sp500", favorite="true") == []
        # 空/未知 index 不过滤
        assert len(await tickers(index="")) == 2
        assert len(await tickers(index="foo")) == 2


def test_get_executor_uninitialized_raises():
    import api.tasks_api as tasks_api
    saved = tasks_api._executor
    tasks_api._executor = None
    try:
        with pytest.raises(RuntimeError):
            tasks_api.get_executor()
    finally:
        tasks_api._executor = saved


async def test_sync_stocks_dup_guard(client):
    """同步股票任务进行中（pending/running）时重复触发 → 409。"""
    async with client as c:
        r1 = await c.post("/api/stocks/sync")
        assert r1.status_code == 200
        r2 = await c.post("/api/stocks/sync")
        assert r2.status_code == 409
        assert "股票同步任务已在进行中" in r2.json()["detail"]


async def test_sync_stocks_schedules_executor(client):
    """POST /api/stocks/sync 必须调度 executor.run_one，否则任务永远 pending。"""
    async with client as c:
        r = await c.post("/api/stocks/sync")
        assert r.status_code == 200
        task_id = r.json()["task_id"]
        await asyncio.sleep(0)  # 让 fire-and-forget 协程先跑一步，记录调用
        assert _dummy_executor is not None
        assert len(_dummy_executor.calls) == 1
        task = _dummy_executor.calls[0]
        assert task.task_type == "sync_stocks"
        assert task.stock_id is None
        assert task.id == task_id


async def test_favorite_filter_and_toggle(client):
    """收藏切换：404 未知 ticker / 成功切换 / favorite 过滤。"""
    async with client as c:
        r = await c.post("/api/stocks/XXXX/favorite", json={"favorite": True})
        assert r.status_code == 404
        r = await c.post("/api/stocks/NKE/favorite", json={"favorite": True})
        assert r.status_code == 200
        assert r.json()["is_favorite"] is True
        # 默认视图可见收藏标记
        r = await c.get("/api/stocks")
        assert r.json()["items"][0]["is_favorite"] is True
        # favorite=true 只返回收藏
        r = await c.get("/api/stocks", params={"favorite": True})
        assert r.json()["total"] == 1 and r.json()["items"][0]["ticker"] == "NKE"
        # 取消收藏后过滤视图为空
        r = await c.post("/api/stocks/NKE/favorite", json={"favorite": False})
        assert r.json()["is_favorite"] is False
        r = await c.get("/api/stocks", params={"favorite": True})
        assert r.json()["total"] == 0


async def test_search_cn_en_name(client):
    """中英文公司名都能命中：搜中文"苹果"与英文 "apple" 都返回 AAPL。"""
    from models import Stock
    async with client as c:
        with _factory() as s:
            s.add(Stock(ticker="AAPL", name_cn="苹果", name_en="Apple Inc.", market="US"))
            s.commit()
        for q in ("苹果", "apple"):
            r = await c.get("/api/stocks", params={"q": q})
            assert r.status_code == 200
            assert r.json()["total"] == 1, f"q={q} 应命中 AAPL"
            assert r.json()["items"][0]["ticker"] == "AAPL"
        #  ticker 前缀仍可搜
        r = await c.get("/api/stocks", params={"q": "AAPL"})
        assert r.json()["total"] == 1


async def test_alias_save_and_search(client):
    """别名覆盖式保存（去空白/去重/丢空串）；搜别名命中 ticker；详情含 aliases。"""
    from models import Stock
    async with client as c:
        with _factory() as s:
            # 名称不含 "google"，命中只能靠别名
            s.add(Stock(ticker="GOOGL", name_en="Alphabet Inc.", market="US"))
            s.commit()
        r = await c.post("/api/stocks/GOOGL/aliases",
                         json={"aliases": [" google ", "谷歌", "google", "", "  "]})
        assert r.status_code == 200
        assert r.json()["aliases"] == ["google", "谷歌"]
        assert r.json()["ticker"] == "GOOGL"
        for q in ("google", "谷歌"):
            r = await c.get("/api/stocks", params={"q": q})
            assert r.json()["total"] == 1, f"q={q} 应靠别名命中 GOOGL"
            assert r.json()["items"][0]["ticker"] == "GOOGL"
        r = await c.get("/api/stocks/GOOGL")
        assert r.json()["aliases"] == ["google", "谷歌"]
        # 覆盖式：再保存一次替换旧别名
        r = await c.post("/api/stocks/GOOGL/aliases", json={"aliases": ["Alphabet"]})
        assert r.json()["aliases"] == ["Alphabet"]
        r = await c.get("/api/stocks", params={"q": "google"})
        assert r.json()["total"] == 0


async def test_alias_save_404(client):
    async with client as c:
        r = await c.post("/api/stocks/XXXX/aliases", json={"aliases": ["google"]})
        assert r.status_code == 404


async def test_filed_count(client):
    """列表与详情均返回已下载财报数；无财报为 0。"""
    from models import Filing, Stock
    from sqlalchemy import select
    async with client as c:
        with _factory() as s:
            st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
            s.add(Filing(stock_id=st.id, form_type="10-K", local_path="/tmp/a.htm"))
            s.add(Filing(stock_id=st.id, form_type="10-Q", local_path="/tmp/b.htm"))
            s.add(Stock(ticker="MSFT", name_en="Microsoft", market="US"))
            s.commit()
        r = await c.get("/api/stocks", params={"q": "NIKE"})
        assert r.json()["items"][0]["filed_count"] == 2
        r = await c.get("/api/stocks", params={"q": "Microsoft"})
        assert r.json()["items"][0]["filed_count"] == 0
        r = await c.get("/api/stocks/NKE")
        assert r.json()["filed_count"] == 2


async def test_versions_endpoint(client):
    """/api/versions 返回当前生成器版本，与单一定义点 scripts/dataversion.py 一致。"""
    import os
    import sys
    scripts_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import dataversion as scripts_dv
    async with client as c:
        r = await c.get("/api/versions")
        assert r.status_code == 200
        assert r.json() == {"analysis": scripts_dv.ANALYSIS_VERSION,
                            "dcf": scripts_dv.DCF_VERSION}


async def test_clear_tasks_deletes_terminal_only(client, tmp_path, monkeypatch):
    """清空任务：只删终态行（success/failed/cancelled）+ logs/tasks/ 白名单内的日志文件；
    排队/运行中任务保留，白名单外日志文件不动（safe_log_path 守卫语义）。"""
    # safe_log_path 的守卫读 api.reports.ROOT：patch 到 tmp root 构造真实日志文件
    root = tmp_path / "root"
    log_dir = root / "logs" / "tasks"
    log_dir.mkdir(parents=True)
    (root / "logs" / "other").mkdir(parents=True)
    monkeypatch.setattr("api.reports.ROOT", str(root))
    with _factory() as s:
        from models import Task
        s.add_all([
            Task(task_type="download", status="success", log_path="logs/tasks/task_1.log"),
            Task(task_type="analysis", status="failed", log_path="logs/tasks/task_2.log"),
            Task(task_type="dcf", status="cancelled", log_path="logs/tasks/task_3.log"),
            Task(task_type="dcf", status="failed", log_path="logs/other/evil.log"),  # 越界日志
            Task(task_type="dcf", status="failed", log_path=None),                   # 无日志
            Task(task_type="dcf", status="pending"),
            Task(task_type="dcf", status="running"),
        ])
        s.commit()
    ok_logs = [log_dir / f"task_{i}.log" for i in (1, 2, 3)]
    for p in ok_logs:
        p.write_text("log", encoding="utf-8")
    evil = root / "logs" / "other" / "evil.log"
    evil.write_text("x", encoding="utf-8")

    async with client as c:
        r = await c.delete("/api/tasks")
        assert r.status_code == 200 and r.json() == {"deleted": 5}
        # 幂等：再删一次为 0
        assert (await c.delete("/api/tasks")).json() == {"deleted": 0}

    for p in ok_logs:
        assert not p.exists()
    assert evil.exists()  # 白名单外文件不删
    with _factory() as s:
        from models import Task
        left = [t.status for t in s.query(Task).all()]
        assert sorted(left) == ["pending", "running"]
