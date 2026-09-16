from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from deps import get_db
from models import Stock, Task
from schemas import StockPage, StockOut, StockDetail, TaskOut

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=StockPage)
def list_stocks(q: str = "", page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db)):
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Stock.ticker.ilike(like), Stock.name_en.ilike(like), Stock.name_cn.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Stock.ticker).offset((page - 1) * size).limit(size)).all()
    return StockPage(total=total, items=[StockOut.model_validate(s) for s in items])


@router.post("/sync")
def sync_stocks(db: Session = Depends(get_db)):
    from api.tasks_api import create_task_internal
    task = create_task_internal(db, "sync_stocks", None, {})
    return {"task_id": task.id}


@router.get("/{ticker}", response_model=StockDetail)
def stock_detail(ticker: str, db: Session = Depends(get_db)):
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, "stock not found")
    running = db.scalars(select(Task).where(Task.stock_id == st.id,
                                            Task.status.in_(("pending", "running")))).all()
    # pydantic v2 的 model_validate 无 update 参数：把 running_tasks 作为临时属性
    # 挂到 ORM 实例上（非映射列，不会落库），再走 from_attributes 校验
    st.running_tasks = [TaskOut.model_validate(t) for t in running]
    return StockDetail.model_validate(st)
