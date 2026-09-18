import asyncio
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
    """sync_stocks 必须走共享 finally 收尾：状态 success、finished_at 落库、日志落盘、股票入库。
    EDGAR 之后还会同步指数成分标记（同样 mock 掉网络，日志应含标记数）。"""
    import services.edgar as edgar_mod
    import services.indices as indices_mod
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
    monkeypatch.setattr(indices_mod, "fetch_sp500_symbols", lambda: ["AAPL"])
    monkeypatch.setattr(indices_mod, "fetch_ndx100_symbols", lambda: [])
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
        assert "指数标记: 标普500 1 只".encode("utf-8") in content
    with Factory() as s2:
        stocks = {st.ticker: st for st in s2.scalars(select(Stock)).all()}
        assert len(stocks) == 2
        assert stocks["AAPL"].in_sp500 is True and stocks["AAPL"].in_ndx100 is False
        assert stocks["NKE"].in_sp500 is False


async def test_sync_stocks_index_failure_not_fatal(tmp_path, monkeypatch):
    """指数成分拉取失败只写警告日志，任务仍 success（EDGAR 才是主目标）。"""
    import services.edgar as edgar_mod
    import services.indices as indices_mod
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Factory() as s:
        s.add(Task(task_type="sync_stocks", status="pending", params={}))
        s.commit()
    sample = {"0": {"ticker": "nke", "title": "NIKE Inc", "cik_str": 1234}}
    monkeypatch.setattr(edgar_mod, "fetch_company_tickers", lambda: sample)

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(indices_mod, "fetch_sp500_symbols", boom)
    ex = TaskExecutor(Factory, log_dir=str(tmp_path))
    with Factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "success"
        with open(task.log_path, "rb") as fh:
            assert "指数成分同步失败".encode("utf-8") in fh.read()
    with Factory() as s2:
        assert len(s2.scalars(select(Stock)).all()) == 1


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


class CancelMidwayProc:
    """communicate 期间把 DB 状态置为 cancelled，模拟 API 层并发取消。"""
    returncode = 1

    def __init__(self, factory, task_id):
        self._factory = factory
        self._task_id = task_id

    async def communicate(self):
        with self._factory() as s:
            row = s.get(Task, self._task_id)
            row.status = "cancelled"
            s.commit()
        return b"out", b""


async def test_cancel_race_respected_in_finally(session_factory, tmp_path, monkeypatch):
    """运行中 DB 已被置 cancelled：finally 收尾必须尊重权威状态，不得回写为 failed。"""
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    with session_factory() as s:
        task_id = s.scalar(select(Task)).id
    async def fake_exec(cmd, log_fh):
        return CancelMidwayProc(session_factory, task_id)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "cancelled"
        assert task.finished_at is not None


class StreamingDownloadProc:
    """communicate 期间分步向日志文件追加下载日志，模拟子进程边跑边写 stdout。"""

    def __init__(self, log_path, factory, task_id, observed, code=0, step_interval=0.15):
        self.returncode = code
        self._log_path = log_path
        self._factory = factory
        self._task_id = task_id
        self._observed = observed
        self._interval = step_interval
        self._step1 = "--- FY2021: 找到 4 份财报 ---\n    ✅ 下载完成\n    ✅ 下载完成\n"
        self._step2 = "    ✅ 下载完成\n    ✅ 下载完成\n=== 完成: 下载 4 份, 跳过 0 份 ===\n"

    def _append(self, text):
        with open(self._log_path, "ab") as fh:
            fh.write(text.encode("utf-8"))

    async def communicate(self):
        self._append(self._step1)
        await asyncio.sleep(self._interval)
        # 中途快照：监控协程此时应已把 (2, 4) 增量写入 DB
        with self._factory() as s:
            row = s.get(Task, self._task_id)
            self._observed.append((row.progress_done, row.progress_total))
        self._append(self._step2)
        await asyncio.sleep(self._interval)
        return b"", b""


def _download_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Factory() as s:
        stock = Stock(ticker="AMD", market="US")
        s.add(stock)
        s.add(Task(task_type="download", status="pending", params={}, stock=stock))
        s.commit()
    return Factory


async def test_download_progress_monitored(tmp_path, monkeypatch):
    """下载任务运行中由监控协程增量写 progress_done/total；结束后按日志全文终态解析，值精确。"""
    import executor as executor_mod
    Factory = _download_factory()
    monkeypatch.setattr(executor_mod, "PROGRESS_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(executor_mod.register, "register_download", lambda *a, **k: 1)
    ex = TaskExecutor(Factory, log_dir=str(tmp_path))
    observed = []
    async def fake_exec(cmd, log_fh):
        with Factory() as s:
            task_id = s.scalar(select(Task)).id
        return StreamingDownloadProc(log_fh.name, Factory, task_id, observed)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with Factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "success"
        # 终态以全文解析为准（4 完成/4 总数），不被增量累计的截断误差影响
        assert (task.progress_done, task.progress_total) == (4, 4)
    assert observed and observed[-1] == (2, 4)  # 运行中监控已写库


async def test_download_progress_finalized_on_failure(tmp_path, monkeypatch):
    """下载失败（退出码非 0）也要在收尾时按日志落终态进度。"""
    import executor as executor_mod
    Factory = _download_factory()
    monkeypatch.setattr(executor_mod, "PROGRESS_POLL_INTERVAL", 0.05)
    ex = TaskExecutor(Factory, log_dir=str(tmp_path))
    observed = []
    async def fake_exec(cmd, log_fh):
        with Factory() as s:
            task_id = s.scalar(select(Task)).id
        # 退出码非 0，但日志里两步都已写完（失败发生在下载全部尝试之后）
        return StreamingDownloadProc(log_fh.name, Factory, task_id, observed, code=1)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    with Factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "failed"
        assert (task.progress_done, task.progress_total) == (4, 4)


async def test_non_download_task_progress_stays_null(session_factory, tmp_path, monkeypatch):
    """非 download 任务不启用进度监控，progress 字段保持 NULL。"""
    import executor as executor_mod
    ex = TaskExecutor(session_factory, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(0)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    monkeypatch.setattr(executor_mod.register, "register_analysis", lambda *a, **k: 1)
    with session_factory() as s:
        task = s.scalar(select(Task))
        await ex.run_one(task)
        s.refresh(task)
        assert task.status == "success"
        assert task.progress_done is None and task.progress_total is None
