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
from services.pairing import pair_analyses_to_filings
from services.quote import get_price
from services.ttm import compute_ttm

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
    filings = db.scalars(select(Filing).where(Filing.stock_id == st.id)).all()
    # 语义配对：10-Q 按（财年, 季度）、10-K/20-F 按财年从 filing.period 推导精确
    # 匹配（services/pairing.py）；period/filing_id 临时挂 ORM 实例（不落库），
    # 供 from_attributes 校验与行内三态按钮判定
    pair_analyses_to_filings(analyses, filings)
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


# 财报数据 Tab 指标行：固定 12 行顺序（key, 展示名, 类型, metrics 中文键）；
# money_yi = DB 百万值 ÷100 展示为亿，ratio/eps 原值。缺数据置 null，不编造。
_FIN_ROWS = (
    ("revenue", "营收（亿$）", "money_yi", "营收"),
    ("net_income", "净利润（亿$）", "money_yi", "净利润"),
    ("net_margin", "净利率（%）", "ratio", "净利率"),
    ("gross_profit", "毛利润（亿$）", "money_yi", "毛利润"),
    ("gross_margin", "毛利率（%）", "ratio", "毛利率"),
    ("eps", "每股收益（$）", "eps", "每股收益"),
    ("cash", "现金及等价物（亿$）", "money_yi", "现金及等价物"),
    ("fcf", "自由现金流（亿$）", "money_yi", "自由现金流"),
    ("total_assets", "总资产（亿$）", "money_yi", "总资产"),
    ("total_liabilities", "总负债（亿$）", "money_yi", "总负债"),
    ("debt_ratio", "资产负债率（%）", "ratio", "资产负债率"),
    ("roe", "净资产收益率（%）", "ratio", "净资产收益率"),
)
_QUARTER_COLUMNS = 4  # 最近季度列数


@router.get("/stocks/{ticker}/financials")
def financials(ticker: str, db: Session = Depends(get_db)):
    """财报数据 Tab：基础信息表（年报全量 + 最近 4 季）+ TTM 基期 + 股数/现价。

    现价取腾讯行情，任何异常降级为 null，不阻塞本接口（行情仅展示）。
    """
    st = _get_stock(db, ticker)
    analyses = db.scalars(select(Analysis).where(Analysis.stock_id == st.id)).all()
    annuals = sorted((a for a in analyses if a.quarter is None),
                     key=lambda a: a.fiscal_year, reverse=True)
    quarters = sorted((a for a in analyses if a.quarter is not None),
                      key=lambda a: (a.fiscal_year, a.quarter or ""), reverse=True)
    columns = ([{"kind": "annual", "label": f"FY{a.fiscal_year}", "form_type": a.form_type, "analysis_id": a.id}
                for a in annuals]
               + [{"kind": "quarter", "label": a.quarter, "form_type": a.form_type, "analysis_id": a.id}
                  for a in quarters[:_QUARTER_COLUMNS]])
    # 同一 FY 存在 10-K/20-F 等多条年报（或同季度双 form）时 label 会重复，
    # 导致 rows[].values 的键互相覆盖 + 前端 :key 冲突：后续重复项追加 form_type 消歧。
    # 必须先定稿 columns 再填 values，保证 values 键与最终 label 一致。
    seen_labels: set[str] = set()
    for c in columns:
        label = c["label"]
        if label in seen_labels:
            label = f'{label} ({c["form_type"]})'
            n = 2
            while label in seen_labels:  # 同 FY 同 form_type 重复的极端兜底
                label = f'{c["label"]} ({c["form_type"]}){n}'
                n += 1
        c["label"] = label
        seen_labels.add(label)
    by_id = {a.id: a for a in analyses}
    rows = []
    for key, label, typ, metric_key in _FIN_ROWS:
        values = {}
        for c in columns:
            v = (by_id[c["analysis_id"]].metrics or {}).get(metric_key)
            if v is None:
                values[c["label"]] = None
            elif typ == "money_yi":
                values[c["label"]] = round(v / 100, 1)
            else:
                values[c["label"]] = v
        rows.append({"key": key, "label": label, "type": typ, "values": values})
    # TTM 基期：最新年报 + 年后季报（公式与 scripts/dcf.py compute_ttm 同一套，
    # 实现在 services/ttm.py；无年报数据返回 None）
    latest_annual = annuals[0] if annuals else None
    ttm = compute_ttm(
        {"fiscal_year": latest_annual.fiscal_year,
         "metrics": latest_annual.metrics or {}} if latest_annual else None,
        [{"fiscal_year": a.fiscal_year, "quarter": a.quarter,
          "form_type": a.form_type, "metrics": a.metrics or {}} for a in quarters])
    # 股数：最近一期（年报+季报一起取最新）metrics 的流通股数
    shares = None
    for a in sorted(analyses, key=lambda x: (x.fiscal_year, x.quarter or ""), reverse=True):
        if a.metrics and a.metrics.get("流通股数") is not None:
            shares = a.metrics["流通股数"]
            break
    try:
        price = get_price(st.symbol())
    except Exception:
        price = None  # 双保险：行情异常只降级，不阻塞接口
    return {"columns": columns, "rows": rows, "ttm": ttm,
            "shares_outstanding": shares, "price": price}


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


@router.delete("/stocks/{ticker}/dcf/{dcf_id}")
def delete_dcf(ticker: str, dcf_id: int, db: Session = Depends(get_db)):
    """删除单条 DCF 报告：删 dcf_report 行 + 删磁盘 md 文件（仅限 reports/dcf/ 内）。"""
    st = _get_stock(db, ticker)
    d = db.get(DcfReport, dcf_id)
    if not d or d.stock_id != st.id:
        raise HTTPException(404, "dcf report not found")
    # 并发防护：与 DCF 任务并行会产生竞态（任务回写孤儿记录或删掉任务产物），先拒绝
    busy = db.scalar(select(func.count(Task.id)).where(
        Task.stock_id == st.id, Task.task_type == "dcf",
        Task.status.in_(("pending", "running"))))
    if busy:
        raise HTTPException(409, "该股票有 DCF 任务进行中，请先取消或等待完成")
    full = os.path.realpath(os.path.join(ROOT, d.local_path))
    base = os.path.realpath(os.path.join(ROOT, "reports", "dcf")) + os.sep
    if full.startswith(base) and os.path.isfile(full):
        try:
            os.remove(full)
        except OSError:
            pass  # 文件缺失/删不掉不阻塞删行
    db.delete(d)
    db.commit()
    return {"deleted": 1}


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
