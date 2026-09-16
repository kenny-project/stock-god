import os
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
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
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "failed"
        assert task.error_code == "EMPTY_OUTPUT"
