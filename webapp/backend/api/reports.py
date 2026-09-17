import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from db import ROOT
from deps import get_db
from models import Analysis, DcfReport, Filing, Stock, Task
from schemas import AnalysisOut, DcfOut

router = APIRouter(prefix="/api", tags=["reports"])

_MD_TYPES = {".md": "text/markdown", ".htm": "text/html", ".html": "text/html", ".pdf": "application/pdf"}


def safe_log_path(p: str | None) -> str | None:
    """日志路径白名单：只允许 logs/tasks/ 下的真实文件，其余一律拒绝。"""
    if not p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    real = os.path.realpath(p)
    base = os.path.realpath(os.path.join(ROOT, "logs", "tasks")) + os.sep
    if not real.startswith(base):
        return None
    return real


def _get_stock(db: Session, ticker: str) -> Stock:
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, "stock not found")
    return st


@router.get("/stocks/{ticker}/analyses", response_model=list[AnalysisOut])
def list_analyses(ticker: str, db: Session = Depends(get_db)):
    st = _get_stock(db, ticker)
    return db.scalars(select(Analysis).where(Analysis.stock_id == st.id)
                      .order_by(Analysis.fiscal_year.desc())).all()


@router.get("/stocks/{ticker}/analyses/{analysis_id}")
def analysis_content(ticker: str, analysis_id: int, db: Session = Depends(get_db)):
    st = _get_stock(db, ticker)
    a = db.get(Analysis, analysis_id)
    if not a or a.stock_id != st.id:
        raise HTTPException(404, "analysis not found")
    full = os.path.realpath(os.path.join(ROOT, a.local_path))
    if not full.startswith(os.path.realpath(ROOT) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, f"报告文件缺失: {a.local_path}")
    with open(full, encoding="utf-8") as f:
        return {"id": a.id, "form_type": a.form_type, "fiscal_year": a.fiscal_year,
                "metrics": a.metrics, "markdown": f.read()}


@router.get("/stocks/{ticker}/dcf", response_model=list[DcfOut])
def list_dcf(ticker: str, db: Session = Depends(get_db)):
    st = _get_stock(db, ticker)
    return db.scalars(select(DcfReport).where(DcfReport.stock_id == st.id)
                      .order_by(DcfReport.generated_at.desc())).all()


@router.get("/stocks/{ticker}/dcf/{dcf_id}")
def dcf_content(ticker: str, dcf_id: int, db: Session = Depends(get_db)):
    st = _get_stock(db, ticker)
    d = db.get(DcfReport, dcf_id)
    if not d or d.stock_id != st.id:
        raise HTTPException(404, "dcf report not found")
    full = os.path.realpath(os.path.join(ROOT, d.local_path))
    if not full.startswith(os.path.realpath(ROOT) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, f"报告文件缺失: {d.local_path}")
    with open(full, encoding="utf-8") as f:
        return {"id": d.id, "valuation": d.valuation, "markdown": f.read()}


@router.get("/filings/{filing_id}/file")
def filing_file(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(Filing, filing_id)
    if not f:
        raise HTTPException(404, "filing not found")
    full = os.path.realpath(os.path.join(ROOT, f.local_path))
    if not full.startswith(os.path.realpath(ROOT) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, f"财报文件缺失: {f.local_path}")
    ext = os.path.splitext(full)[1].lower()
    return FileResponse(full, media_type=_MD_TYPES.get(ext, "application/octet-stream"),
                        filename=os.path.basename(full))


@router.get("/tasks/{task_id}/log")
def task_log(task_id: int, offset: int = 0, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    path = safe_log_path(task.log_path)
    if not path or not os.path.isfile(path):
        return {"content": "", "size": 0, "next_offset": 0}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if offset <= 0:
            f.seek(max(0, size - 64 * 1024))  # offset<=0 → 尾部 64KB
        else:
            f.seek(min(offset, size))
        content = f.read(64 * 1024).decode("utf-8", "replace")
        next_offset = f.tell()
    return {"content": content, "size": size, "next_offset": next_offset}
