"""后台任务执行器：semaphore 并发限制、状态机流转、日志落盘、进程组取消。"""
import asyncio
import os
import signal
from datetime import datetime
from sqlalchemy import select
import services.register as register
from db import ROOT
from models import Task
from services.runners import build_command, classify_error

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "tasks")
MAX_CONCURRENCY = 2

# 存函数名而非函数引用：调用时经 getattr 取，便于测试 monkeypatch 生效
_REGISTER = {"download": "register_download",
             "analysis": "register_analysis",
             "dcf": "register_dcf"}


def _has_artifacts(task_type: str, stock) -> bool:
    """判断该股票是否已有对应类型的产物落盘，用于区分幂等重跑与真正的空产物。"""
    from db import ROOT
    if task_type == "download":
        return os.path.isdir(os.path.join(ROOT, "reports", "sec_filings", stock.ticker))
    if task_type == "analysis":
        d = os.path.join(ROOT, "reports", "sec_analysis", stock.ticker)
        return os.path.isdir(d) and any(f.endswith(".md") for f in os.listdir(d))
    if task_type == "dcf":
        d = os.path.join(ROOT, "reports", "dcf")
        if not os.path.isdir(d):
            return False
        return any(n.startswith((f"{stock.symbol()}_DCF", f"{stock.ticker}_DCF")) and n.endswith(".md")
                   for n in os.listdir(d))
    return True


class TaskExecutor:
    def __init__(self, session_factory, log_dir=None):
        # session_factory: 可调用，返回新的 Session（每个任务独立会话）
        self._factory = session_factory
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._procs: dict[int, asyncio.subprocess.Process] = {}
        self.log_dir = os.path.abspath(log_dir or LOG_DIR)
        os.makedirs(self.log_dir, exist_ok=True)

    async def run_one(self, task):
        async with self._sem:
            session = self._factory()
            task = session.merge(task)
            # 以数据库中的最新状态为准：排队期间被取消的任务不再执行
            session.refresh(task)
            if task.status != "pending":
                session.close()
                return
            task.status = "running"
            task.started_at = datetime.now()
            log_path = os.path.join(self.log_dir, f"task_{task.id}.log")
            task.log_path = log_path
            session.commit()
            try:
                if task.task_type == "sync_stocks":
                    # 同步股票列表不走子进程，直接在线程池拉 EDGAR
                    from services.edgar import fetch_company_tickers, upsert_stocks
                    data = await asyncio.get_running_loop().run_in_executor(None, fetch_company_tickers)
                    n = upsert_stocks(session, data)
                    with open(log_path, "ab") as log_fh:
                        log_fh.write(f"synced {n} companies\n".encode("utf-8"))
                    task.status = "success"
                    return
                with open(log_path, "ab") as log_fh:
                    proc = await self._exec(build_command(task.task_type,
                                                          task.stock.symbol() if task.stock else "",
                                                          task.params or {}), log_fh)
                    self._procs[task.id] = proc
                    try:
                        out, err = await proc.communicate()
                        log_fh.write(err)
                    finally:
                        self._procs.pop(task.id, None)
                if proc.returncode == 0:
                    registered = getattr(register, _REGISTER[task.task_type])(session, task.stock) \
                        if task.stock else 0
                    if task.stock and registered == 0 and task.task_type != "sync_stocks":
                        if _has_artifacts(task.task_type, task.stock):
                            # 幂等重跑：产物此前已登记，只是本次无新增，视为成功
                            task.status = "success"
                            with open(log_path, "ab") as log_fh:
                                log_fh.write("无新增产物\n".encode("utf-8"))
                        else:
                            task.status = "failed"
                            task.error_code = "EMPTY_OUTPUT"
                            task.error_summary = "脚本退出码为 0 但未产生新的产物文件，按失败处理"
                    else:
                        task.status = "success"
                else:
                    task.status = "failed"
                    tail = (err or b"").decode("utf-8", "replace")[-2000:]
                    task.error_code, task.error_summary = classify_error(proc.returncode, tail)
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.error_summary = "任务被手动取消"
                raise
            except Exception as e:  # 任何意外都落为失败，不留悬挂任务
                task.status = "failed"
                task.error_code = "EXECUTOR_INTERNAL_ERROR"
                task.error_summary = f"执行器内部错误: {e}"
            finally:
                task.finished_at = datetime.now()
                # 取消竞态：API 层可能已把 DB 状态置为 cancelled。必须用全新会话
                # 读权威状态——本会话里 dirty 的 failed/success 尚未落库，若经本会话
                # 读取（含 autoflush）只会看到自己的脏值，无法发现取消标记
                with self._factory() as chk:
                    db_status = chk.scalar(select(Task.status).where(Task.id == task.id))
                if db_status == "cancelled":
                    task.status = "cancelled"
                    if not task.error_summary:
                        task.error_summary = "任务被手动取消"
                try:
                    session.commit()
                finally:
                    session.close()

    async def _exec(self, cmd: list[str], log_fh):
        return await asyncio.create_subprocess_exec(
            *cmd, stdout=log_fh, stderr=asyncio.subprocess.PIPE,
            cwd=ROOT,
            start_new_session=True)

    async def cancel(self, task) -> bool:
        proc = self._procs.get(task.id)
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), timeout=10)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return True
        return False  # pending 任务由 API 层直接置 cancelled
