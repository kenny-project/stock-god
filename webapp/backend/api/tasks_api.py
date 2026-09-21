import asyncio
import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from deps import get_db
from models import Stock, Task
from schemas import TaskCreate, TaskOut
from executor import TaskExecutor
from api.reports import safe_log_path

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
_executor: TaskExecutor | None = None
_bg: set = set()  # fire-and-forget 任务的强引用，防止事件循环丢弃前被 GC；完成后自动移除


def set_executor(ex: TaskExecutor):
    global _executor
    _executor = ex


def get_executor() -> TaskExecutor:
    if _executor is None:
        raise RuntimeError("executor not initialized")
    return _executor


def schedule_task(task: Task) -> None:
    """在事件循环上调度 executor.run_one（fire-and-forget）。必须在异步上下文中调用。"""
    t = asyncio.get_running_loop().create_task(get_executor().run_one(task))
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def create_task_internal(db: Session, task_type: str, stock, params: dict) -> Task:
    task = Task(task_type=task_type, stock_id=stock.id if stock else None, params=params)
    db.add(task)
    db.commit()
    return task


@router.post("")
async def create_task(body: TaskCreate, db: Session = Depends(get_db)):
    stock = db.scalar(select(Stock).where(Stock.ticker == body.ticker.upper()))
    if not stock:
        raise HTTPException(404, f"unknown ticker {body.ticker}")
    dup = db.scalar(select(Task).where(Task.stock_id == stock.id, Task.task_type == body.task_type,
                                       Task.status.in_(("pending", "running"))))
    if dup:
        state = "排队" if dup.status == "pending" else "执行"
        raise HTTPException(409, f"同类型任务已在{state}中 (task #{dup.id})")
    task = create_task_internal(db, body.task_type, stock, body.params)
    schedule_task(task)
    return TaskOut.model_validate(task)


@router.get("")
def list_tasks(status: str = "", db: Session = Depends(get_db)):
    stmt = select(Task).order_by(Task.id.desc()).limit(200)
    if status:
        stmt = stmt.where(Task.status == status)
    return [TaskOut.model_validate(t) for t in db.scalars(stmt).all()]


TERMINAL_STATUSES = ("success", "failed", "cancelled")


@router.delete("")
def clear_tasks(db: Session = Depends(get_db)):
    """清空终态任务（success/failed/cancelled）：删行 + 删日志文件（仅限 logs/tasks/ 内，
    守卫复用 reports.safe_log_path 的 realpath 白名单语义）。排队/运行中的任务不动。"""
    tasks = db.scalars(select(Task).where(Task.status.in_(TERMINAL_STATUSES))).all()
    for t in tasks:
        path = safe_log_path(t.log_path)
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass  # 单个日志文件删不掉不阻塞删行
        db.delete(t)
    db.commit()
    return {"deleted": len(tasks)}


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    if task.status not in ("pending", "running"):
        raise HTTPException(409, f"任务已结束({task.status})，无法取消")
    if task.status == "running":
        # 先落库 cancelled 终态再杀进程（spec §6）：子进程被 SIGTERM 后，executor
        # finally 会读 DB 权威状态决定是否覆盖。若此刻仍是 running，子进程
        # exit -15 会被记为 failed 而非 cancelled
        task.status = "cancelled"
        task.finished_at = datetime.now()
        task.error_summary = "任务被手动取消"
        db.commit()
        await get_executor().cancel(task)
    else:
        task.status = "cancelled"
        task.error_summary = "任务被手动取消"
    db.commit()
    return task
