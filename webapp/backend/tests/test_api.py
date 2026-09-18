import asyncio
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db import Base
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
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
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


async def test_create_task_bad_type(client):
    async with client as c:
        r = await c.post("/api/tasks", json={"task_type": "nope", "ticker": "NKE"})
        assert r.status_code == 422


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
