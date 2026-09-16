import os
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db import Base
from models import Task, Stock, Analysis
from executor import TaskExecutor


class FakeProc:
    def __init__(self, code=0, out=b"ok"):
        self.returncode = code
        self._out = out
    async def communicate(self):
        return self._out, b""


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Factory() as s:
        stock = Stock(ticker="NKE", market="US")
        s.add(stock)
        # 任务必须关联股票：EMPTY_OUTPUT 判定与产物登记都依赖 task.stock
        s.add(Task(task_type="analysis", status="pending", params={}, stock=stock))
        s.commit()
    return Factory


async def test_success_flow(session_factory, tmp_path, monkeypatch):
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(0)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "success" and task.finished_at is not None
        assert os.path.exists(task.log_path)


async def test_failure_flow(session_factory, tmp_path, monkeypatch):
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(1)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "failed"
        assert task.error_code == "SCRIPT_EXIT_NONZERO"
        assert task.error_summary


async def test_empty_output_is_failure(session_factory, tmp_path, monkeypatch):
    import executor as executor_mod
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(0)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    monkeypatch.setattr(executor_mod.register, "register_analysis", lambda *a, **k: 0)
    monkeypatch.setattr(executor_mod, "_has_artifacts", lambda tt, stock: False)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "failed"
        assert task.error_code == "EMPTY_OUTPUT"


class BoomProc:
    returncode = None
    async def communicate(self):
        raise RuntimeError("boom")


async def test_cancelled_task_skipped(session_factory, tmp_path, monkeypatch):
    """排队期间被取消的任务不得再执行：状态保持 cancelled，不留日志，不启动子进程。"""
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    called = []
    async def fake_exec(cmd, log_fh):
        called.append(cmd)
        return FakeProc(0)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with session_factory() as s:
        task = s.scalar(select(Task))
        task_id = task.id
    with session_factory() as s2:
        t2 = s2.get(Task, task_id)
        t2.status = "cancelled"
        s2.commit()
    await ex.run_one(task)  # 传入仍是 pending 的旧快照
    with session_factory() as s3:
        t3 = s3.get(Task, task_id)
        assert t3.status == "cancelled"
        assert t3.log_path is None
    assert not called
    assert os.listdir(str(tmp_path)) == []


async def test_sync_stocks_success(tmp_path, monkeypatch):
    """sync_stocks 必须走共享 finally 收尾：状态 success、finished_at 落库、日志落盘、股票入库。"""
    import services.edgar as edgar_mod
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Factory() as s:
        s.add(Task(task_type="sync_stocks", status="pending", params={}))
        s.commit()
    sample = {"0": {"ticker": "nke", "title": "NIKE Inc", "cik_str": 1234},
              "1": {"ticker": "AAPL", "title": "Apple Inc", "cik_str": 5678}}
    # executor 在函数体内 import services.edgar，故必须 patch 源模块属性
    monkeypatch.setattr(edgar_mod, "fetch_company_tickers", lambda: sample)
    ex = TaskExecutor(Factory, log_dir=str(tmp_path))
    with Factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "success"
        assert task.finished_at is not None
        assert os.path.exists(task.log_path)
        with open(task.log_path, "rb") as fh:
            content = fh.read()
        assert b"synced" in content and b"2" in content
    with Factory() as s2:
        assert len(s2.scalars(select(Stock)).all()) == 2


async def test_procs_cleaned_on_exception(session_factory, tmp_path, monkeypatch):
    """communicate 抛异常时 _procs 必须被清理，任务落为 EXECUTOR_INTERNAL_ERROR。"""
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return BoomProc()
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "failed"
        assert task.error_code == "EXECUTOR_INTERNAL_ERROR"
        assert task.finished_at is not None
    assert ex._procs == {}


async def test_idempotent_rerun_succeeds(session_factory, tmp_path, monkeypatch):
    """exit 0 且 registered==0 但产物已存在 → 幂等重跑视为成功而非 EMPTY_OUTPUT。"""
    import executor as executor_mod
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(0)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    monkeypatch.setattr(executor_mod.register, "register_analysis", lambda *a, **k: 0)
    monkeypatch.setattr(executor_mod, "_has_artifacts", lambda tt, stock: True)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "success"
        assert task.error_code is None
        assert os.path.exists(task.log_path)
