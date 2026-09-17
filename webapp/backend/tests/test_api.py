import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db import Base
import models  # noqa
from main import app, set_engine_for_test


class DummyExecutor:
    async def run_one(self, task):
        return

    async def cancel(self, task):
        return False


@pytest.fixture
def client():
    # StaticPool：内存库全局共享单连接。FastAPI 同步端点跑在线程池里，
    # sqlite:// 默认的 SingletonThreadPool 会给每个线程一个空库（no such table）
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    set_engine_for_test(engine, Factory)
    import api.tasks_api as tasks_api
    tasks_api.set_executor(DummyExecutor())
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
