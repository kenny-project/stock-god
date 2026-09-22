"""后台任务执行器：semaphore 并发限制、状态机流转、日志落盘、进程组取消。"""
import asyncio
import contextlib
import os
import signal
from datetime import datetime
from sqlalchemy import select
import services.register as register
from db import ROOT
from models import Task
from services.form_fix import fix_unknown_filings
from services.progress import DownloadProgressTracker, parse_download_progress
from services.runners import build_command, classify_error

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "tasks")
MAX_CONCURRENCY = 2
PROGRESS_POLL_INTERVAL = 2.0  # 下载任务进度监控轮询间隔（秒）

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
                    loop = asyncio.get_running_loop()
                    try:
                        data = await loop.run_in_executor(None, fetch_company_tickers)
                        n = upsert_stocks(session, data)
                    except Exception:
                        # 回滚会话里的半状态（部分 add/脏字段），避免随 finally 的 commit 落库
                        session.rollback()
                        raise
                    with open(log_path, "ab") as log_fh:
                        log_fh.write(f"synced {n} companies\n".encode("utf-8"))
                        # 指数成分标记是附加目标：失败只记日志警告行，不影响任务成功
                        try:
                            from services.indices import (fetch_ndx100_symbols,
                                                          fetch_sp500_symbols,
                                                          sync_stock_indexes)
                            sp = await loop.run_in_executor(None, fetch_sp500_symbols)
                            ndx = await loop.run_in_executor(None, fetch_ndx100_symbols)
                            report = sync_stock_indexes(session, sp_symbols=sp, ndx_symbols=ndx)
                            log_fh.write(
                                f"指数标记: 标普500 {report['sp500']} 只"
                                f"(名单未匹配 {len(report['sp500_unmatched'])}), "
                                f"纳斯达克100 {report['ndx100']} 只"
                                f"(名单未匹配 {len(report['ndx100_unmatched'])})\n"
                                .encode("utf-8"))
                        except Exception as e:
                            # 丢弃指数标记的半状态（全量置 False 后只置了一部分），
                            # 防止随成功 commit 连带落库；异常类型一并记入便于定位
                            session.rollback()
                            log_fh.write(f"指数成分同步失败（不影响股票列表）: "
                                         f"{type(e).__name__}: {e}\n".encode("utf-8"))
                    task.status = "success"
                    return
                with open(log_path, "ab") as log_fh:
                    proc = await self._exec(build_command(task.task_type,
                                                          task.stock.symbol() if task.stock else "",
                                                          task.params or {}), log_fh)
                    self._procs[task.id] = proc
                    # 下载任务启动实时进度监控（其他类型日志无可解析的进度，不启用）
                    monitor = asyncio.create_task(self._monitor_download_progress(task.id, log_path)) \
                        if task.task_type == "download" else None
                    try:
                        out, err = await proc.communicate()
                        log_fh.write(err)
                    finally:
                        self._procs.pop(task.id, None)
                        if monitor:
                            # 先停监控再收尾，避免监控协程晚于终态解析写库
                            monitor.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await monitor
                if proc.returncode == 0:
                    registered = getattr(register, _REGISTER[task.task_type])(session, task.stock) \
                        if task.stock else 0
                    if task.task_type == "download" and task.stock:
                        # 下载产物文件名多不含 form token（如 avgo-20260201.htm），
                        # register 落 UNKNOWN；用 submissions API 按 reportDate 回填。
                        # 幂等，且会顺带清理存量 UNKNOWN；失败不阻塞任务成功
                        try:
                            fix_unknown_filings(session)
                        except Exception as e:  # noqa: BLE001 网络等异常只记日志
                            with open(log_path, "ab") as log_fh:
                                log_fh.write(f"[form_fix] 回填失败: {e}\n".encode("utf-8"))
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
                # 终态兜底：无论成功/失败/取消，都按日志全文做最后一次解析，
                # 保证 progress 终值准确（覆盖增量监控可能的截断误差）
                if task.task_type == "download":
                    self._finalize_download_progress(task.id, log_path)
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
        # stdout 直写文件时 Python 子进程默认全缓冲（攒满 4-8KB 才落盘），
        # 运行中日志文件几乎为空；PYTHONUNBUFFERED=1 强制非缓冲，日志实时可见
        return await asyncio.create_subprocess_exec(
            *cmd, stdout=log_fh, stderr=asyncio.subprocess.PIPE,
            cwd=ROOT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})

    # ---------- 下载进度监控 ----------

    def _poll_progress_once(self, task_id: int, log_path: str, tracker: DownloadProgressTracker,
                            offset: int) -> int:
        """读日志从 offset 起的新增段，累计解析并独立会话写库；返回新 offset。

        供监控协程循环调用，也可单独测试。日志读写异常（文件暂不存在等）不抛出。
        """
        try:
            with open(log_path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return offset
        if not data:
            return offset
        tracker.feed(data.decode("utf-8", "replace"))
        self._save_progress(task_id, tracker.done, tracker.total)
        return offset + len(data)

    def _save_progress(self, task_id: int, done: int, total: int) -> None:
        """独立会话写进度；失败仅记日志，绝不影响任务本身。"""
        try:
            with self._factory() as s:
                row = s.get(Task, task_id)
                if row is None:
                    return
                row.progress_done = done
                row.progress_total = total
                s.commit()
        except Exception as e:
            print(f"[executor] 任务 {task_id} 进度写库失败: {type(e).__name__}: {e}")

    def _finalize_download_progress(self, task_id: int, log_path: str) -> None:
        """任务收尾时按日志全文解析一次，落最终进度。"""
        try:
            with open(log_path, "rb") as fh:
                done, total = parse_download_progress(fh.read().decode("utf-8", "replace"))
            self._save_progress(task_id, done, total)
        except OSError as e:
            print(f"[executor] 任务 {task_id} 终态进度解析失败: {e}")

    async def _monitor_download_progress(self, task_id: int, log_path: str) -> None:
        """进程运行期间每 PROGRESS_POLL_INTERVAL 秒读一次日志新增段，增量累计进度写库。

        仅 task_type=download 启用；子进程退出后由调用方 cancel。任何轮询内异常
        都只记日志不中断（下一次循环重试），保证监控本身不拖垮任务。
        """
        tracker = DownloadProgressTracker()
        offset = 0
        try:
            while True:
                await asyncio.sleep(PROGRESS_POLL_INTERVAL)
                offset = self._poll_progress_once(task_id, log_path, tracker, offset)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 防御：监控循环意外崩溃不应影响任务执行
            print(f"[executor] 任务 {task_id} 进度监控异常退出: {type(e).__name__}: {e}")

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
