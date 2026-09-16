import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from deps import get_db
from models import Stock, Task
from schemas import TaskCreate, TaskOut
from executor import TaskExecutor

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
_executor: TaskExecutor | None = None


def set_executor(ex: TaskExecutor):
    global _executor
    _executor = ex


def get_executor() -> TaskExecutor:
    assert _executor is not None, "executor not initialized"
    return _executor


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
    asyncio.get_running_loop().create_task(get_executor().run_one(task))
    return TaskOut.model_validate(task)


@router.get("")
def list_tasks(status: str = "", db: Session = Depends(get_db)):
    stmt = select(Task).order_by(Task.id.desc()).limit(200)
    if status:
        stmt = stmt.where(Task.status == status)
    return [TaskOut.model_validate(t) for t in db.scalars(stmt).all()]


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
        await get_executor().cancel(task)
    else:
        task.status = "cancelled"
        task.error_summary = "任务被手动取消"
    db.commit()
    return task
