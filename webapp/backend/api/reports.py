import os
import re
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
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
    analyses = db.scalars(select(Analysis).where(Analysis.stock_id == st.id)).all()
    # 按 form_type 分组配对 filing.period：双方升序后从最新端（尾部）对齐 zip，
    # 最长公共尾部一一对应；多出的老分析配不到 period 置 None（不编造日期）。
    # UNKNOWN 等历史遗留 form_type 的 filing 只与同 form_type 的分析配对。
    filings: dict[str, list[Filing]] = {}
    for f in db.scalars(select(Filing).where(Filing.stock_id == st.id)):
        if f.period is None:
            continue  # period 未知的 filing 不进配对池：宁缺毋滥，避免静默错位
        filings.setdefault(f.form_type, []).append(f)
    for group in filings.values():
        group.sort(key=lambda f: f.period or "")
    groups: dict[str, list[Analysis]] = {}
    for a in analyses:
        groups.setdefault(a.form_type, []).append(a)
    for ft, group in groups.items():
        group.sort(key=lambda a: (a.fiscal_year, a.quarter or ""))
        for a, f in zip(reversed(group), reversed(filings.get(ft, []))):
            a.period = f.period  # 临时属性挂 ORM 实例（不落库），供 from_attributes 校验
    out = [AnalysisOut.model_validate(a) for a in analyses]
    # period 降序（None 置底），period 相同/None 时按 财年、季度（NULL 同空串）、生成时间 降序
    out.sort(key=lambda x: x.generated_at, reverse=True)
    out.sort(key=lambda x: x.quarter or "", reverse=True)
    out.sort(key=lambda x: x.fiscal_year, reverse=True)
    out.sort(key=lambda x: (x.period is not None, x.period or ""), reverse=True)
    return out


@router.delete("/stocks/{ticker}/analyses")
def clear_analyses(ticker: str, db: Session = Depends(get_db)):
    """清空该股全部分析记录：删 analysis 行 + 删磁盘分析文件（仅限 sec_analysis/{ticker}/ 内）。"""
    st = _get_stock(db, ticker)
    # 并发防护：清空与分析/DCF 任务并行会产生竞态（任务回写孤儿记录或删掉任务产物），先拒绝
    busy = db.scalar(select(func.count(Task.id)).where(
        Task.stock_id == st.id, Task.task_type.in_(("analysis", "dcf")),
        Task.status.in_(("pending", "running"))))
    if busy:
        raise HTTPException(409, "该股票有分析/DCF 任务进行中，请先取消或等待完成")
    analyses = db.scalars(select(Analysis).where(Analysis.stock_id == st.id)).all()
    base = os.path.realpath(os.path.join(ROOT, "reports", "sec_analysis", st.ticker)) + os.sep
    for a in analyses:
        full = os.path.realpath(os.path.join(ROOT, a.local_path))
        if full.startswith(base) and os.path.isfile(full):
            try:
                os.remove(full)
            except OSError:
                pass  # 单个文件删不掉不阻塞清库
    db.execute(delete(Analysis).where(Analysis.stock_id == st.id))
    db.commit()
    return {"deleted": len(analyses)}


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
def filing_file(filing_id: int, download: bool = False, db: Session = Depends(get_db)):
    f = db.get(Filing, filing_id)
    if not f:
        raise HTTPException(404, "filing not found")
    full = os.path.realpath(os.path.join(ROOT, f.local_path))
    if not full.startswith(os.path.realpath(ROOT) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, f"财报文件缺失: {f.local_path}")
    ext = os.path.splitext(full)[1].lower()
    # basename 后仍过滤引号/换行，防止破坏 Content-Disposition header 结构
    name = re.sub(r'["\r\n]', "", os.path.basename(full))
    # 打开（inline，浏览器按 Content-Type 预览）与下载（attachment）拆开
    disposition = f'attachment; filename="{name}"' if download else "inline"
    return FileResponse(full, media_type=_MD_TYPES.get(ext, "application/octet-stream"),
                        headers={"Content-Disposition": disposition})


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
