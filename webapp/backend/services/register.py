"""任务成功后的产物登记：校验文件存在且非空才入库。路径一律存相对项目根的 POSIX 路径。"""
import os
import re
from datetime import datetime
from sqlalchemy import select
from db import ROOT
from models import Filing, Analysis, DcfReport
from services.metrics import parse_analysis_metrics, parse_dcf_valuation

_PERIOD = re.compile(r"-(\d{8})$")
_FORM = re.compile(r"^(10-K|10-Q|20-F|6-K)", re.I)
_FY = re.compile(r"FY(\d{4})", re.I)


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _valid_file(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 0


def register_download(session, stock) -> int:
    """登记 reports/sec_filings/{TICKER}/ 下新增文件/目录。返回新增条数。"""
    base = os.path.join(ROOT, "reports", "sec_filings", stock.ticker)
    if not os.path.isdir(base):
        return 0
    rows = session.scalars(select(Filing).where(Filing.stock_id == stock.id)).all()
    existing = {f.local_path for f in rows}
    # uq_filing 唯一约束是 (stock_id, form_type, period)：一个申报期只允许一条记录。
    # 真实申报目录（如 nke-20240531/）里有多个 exhibit/图片文件共享同一申报期，
    # 全部入库必撞约束，故非 NULL 的 (form_type, period) 只登记首个遇到的文件。
    existing_fp = {(f.form_type, f.period) for f in rows if f.period}
    n = 0
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        candidates = []
        if os.path.isfile(full) and _valid_file(full):
            candidates.append(full)
        elif os.path.isdir(full):
            candidates = [os.path.join(full, x) for x in sorted(os.listdir(full))
                          if _valid_file(os.path.join(full, x))]
        for cand in candidates:
            rel = _rel(cand)
            if rel in existing:
                continue
            period = None
            m = _PERIOD.search(name)
            if m:
                period = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
            form = "UNKNOWN"
            fm = _FORM.match(name)
            if fm:
                form = fm.group(1).upper()
            if period is not None and (form, period) in existing_fp:
                continue
            session.add(Filing(stock_id=stock.id, form_type=form, period=period, local_path=rel))
            existing.add(rel)
            if period is not None:
                existing_fp.add((form, period))
            n += 1
    session.commit()
    return n


def register_analysis(session, stock) -> int:
    base = os.path.join(ROOT, "reports", "sec_analysis", stock.ticker)
    if not os.path.isdir(base):
        return 0
    existing = {(a.form_type, a.fiscal_year)
                for a in session.scalars(select(Analysis).where(Analysis.stock_id == stock.id))}
    n = 0
    for name in sorted(os.listdir(base)):
        if not name.endswith(".md"):
            continue
        fm = _FORM.match(name)
        fy = _FY.search(name)
        if not fm or not fy:
            continue  # 文件名不合规（如 {TICKER}_analysis_*.md 旧汇总），跳过不造假
        form, year = fm.group(1).upper(), int(fy.group(1))
        full = os.path.join(base, name)
        if not _valid_file(full) or (form, year) in existing:
            continue
        with open(full, encoding="utf-8") as f:
            metrics = parse_analysis_metrics(f.read())
        session.add(Analysis(stock_id=stock.id, form_type=form, fiscal_year=year,
                             local_path=_rel(full), metrics=metrics or None))
        existing.add((form, year))
        n += 1
    session.commit()
    return n


def register_dcf(session, stock) -> int:
    base = os.path.join(ROOT, "reports", "dcf")
    if not os.path.isdir(base):
        return 0
    n = 0
    for name in sorted(os.listdir(base)):
        if not (name.startswith(f"{stock.symbol()}_DCF") or name.startswith(f"{stock.ticker}_DCF")):
            continue
        if not name.endswith(".md"):
            continue
        full = os.path.join(base, name)
        if not _valid_file(full):
            continue
        with open(full, encoding="utf-8") as f:
            valuation = parse_dcf_valuation(f.read())
        session.add(DcfReport(stock_id=stock.id, local_path=_rel(full), valuation=valuation or None,
                              generated_at=datetime.fromtimestamp(os.path.getmtime(full))))
        n += 1
    session.commit()
    return n
