from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from deps import get_db
from models import Stock, Task, Filing
from schemas import StockPage, StockOut, StockDetail, TaskOut, FavoriteUpdate

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


def _filed_counts(db: Session, stock_ids: list[int]) -> dict[int, int]:
    """stock_id → 已下载财报数（一次聚合查询，避免 N+1）。"""
    if not stock_ids:
        return {}
    rows = db.execute(
        select(Filing.stock_id, func.count(Filing.id))
        .where(Filing.stock_id.in_(stock_ids))
        .group_by(Filing.stock_id)
    ).all()
    return {sid: n for sid, n in rows}


def _stock_out(s: Stock, filed_count: int) -> StockOut:
    base = StockOut.model_validate(s)
    return base.model_copy(update={"filed_count": filed_count})


@router.get("", response_model=StockPage)
def list_stocks(q: str = "", page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                favorite: bool = False, db: Session = Depends(get_db)):
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Stock.ticker.ilike(like), Stock.name_en.ilike(like), Stock.name_cn.ilike(like)))
    if favorite:
        stmt = stmt.where(Stock.is_favorite.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Stock.ticker).offset((page - 1) * size).limit(size)).all()
    counts = _filed_counts(db, [s.id for s in items])
    return StockPage(total=total,
                     items=[_stock_out(s, counts.get(s.id, 0)) for s in items])


@router.post("/{ticker}/favorite", response_model=StockOut)
def set_favorite(ticker: str, body: FavoriteUpdate, db: Session = Depends(get_db)):
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, f"unknown ticker {ticker}")
    st.is_favorite = body.favorite
    db.commit()
    filed_count = db.scalar(select(func.count(Filing.id)).where(Filing.stock_id == st.id)) or 0
    return _stock_out(st, filed_count)


@router.post("/sync")
async def sync_stocks(db: Session = Depends(get_db)):
    dup = db.scalar(select(Task).where(Task.task_type == "sync_stocks", Task.stock_id.is_(None),
                                       Task.status.in_(("pending", "running"))))
    if dup:
        raise HTTPException(409, f"股票同步任务已在进行中 (task #{dup.id})")
    from api.tasks_api import create_task_internal, schedule_task
    task = create_task_internal(db, "sync_stocks", None, {})
    schedule_task(task)  # 必须调度执行，否则任务永远 pending；async 端点保证有 running loop
    return {"task_id": task.id}


@router.get("/{ticker}", response_model=StockDetail)
def stock_detail(ticker: str, db: Session = Depends(get_db)):
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, "stock not found")
    running = db.scalars(select(Task).where(Task.stock_id == st.id,
                                            Task.status.in_(("pending", "running")))).all()
    # pydantic v2 的 model_validate 无 update 参数：把非列字段作为临时属性
    # 挂到 ORM 实例上（不会落库），再走 from_attributes 校验
    st.running_tasks = [TaskOut.model_validate(t) for t in running]
    st.filed_count = len(st.filings)
    return StockDetail.model_validate(st)
