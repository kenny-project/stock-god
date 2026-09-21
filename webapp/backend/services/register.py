"""任务成功后的产物登记：校验文件存在且非空才入库。路径一律存相对项目根的 POSIX 路径。"""
import os
import re
import sys
from datetime import datetime
from sqlalchemy import select
from db import ROOT
from models import Filing, Analysis, DcfReport
from services.metrics import parse_analysis_metrics, parse_dcf_valuation
from services.dataversion import parse_md_version

_PERIOD = re.compile(r"[-_](\d{8})$")
_FORM = re.compile(r"(10-?K|10-?Q|20-?F|6-?K)", re.I)
_FY = re.compile(r"FY(\d{4})", re.I)
# 季报文件名形如 10-Q_2024Q3.md 或 10-Q_FY2024Q3.md（后者同时含 FY 字样），
# fiscal_year 直接取季度标签里的年份；匹配顺序必须先于 _FY（见 register_analysis）
_QTR = re.compile(r"(\d{4})Q([1-4])", re.I)
_MAIN_EXT = {".htm", ".html", ".pdf"}


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _valid_file(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _period_of(name: str) -> str | None:
    """从文件/目录名提取申报期：先剥离扩展名，再匹配 -YYYYMMDD 或 _YYYYMMDD 结尾。"""
    m = _PERIOD.search(os.path.splitext(name)[0])
    if not m:
        return None
    d = m.group(1)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _form_of(name: str) -> str | None:
    """在名字任意位置找 form token（容忍大小写与缺连字符，如 msft-10k_20220630），
    归一化为 10-K/10-Q/20-F/6-K；找不到返回 None。"""
    m = _FORM.search(name)
    if not m:
        return None
    tok = m.group(1).upper().replace("-", "")
    return f"{tok[:-1]}-{tok[-1]}"


def _representative(dirpath: str) -> str | None:
    """选申报期目录的代表文件：优先主文档（htm/html/pdf 且名字不含 exhibit），
    无主文档则回退到字母序第一个有效文件。"""
    files = [x for x in sorted(os.listdir(dirpath)) if _valid_file(os.path.join(dirpath, x))]
    if not files:
        return None
    main = [x for x in files
            if os.path.splitext(x)[1].lower() in _MAIN_EXT and "exhibit" not in x.lower()]
    return os.path.join(dirpath, main[0] if main else files[0])


def register_download(session, stock, base_dir: str | None = None) -> int:
    """登记 reports/sec_filings/{TICKER}/ 下新增文件/目录。返回新增条数。

    目录只登记一个代表文件（主文档优先），同一 (form_type, period) 只入库一行。
    两遍扫描：顶层文件（EDGAR 主文档，如 nke-20220531.htm）先于同名申报期目录登记，
    让主文档占据 (form_type, period) 槽位，目录里的 exhibit/XBRL 附件不会挤掉它。

    form 类型来源：目录用代表文件名提取（如 pfe-exh101x3292026x10q.htm 含 10q），
    提取不到再回退目录名，仍提取不到才 UNKNOWN；顶层文件名即代表文件名。
    兜底查重：某申报期已有任何已知类型（非 UNKNOWN）行时不再登记 UNKNOWN 行，
    杜绝同一 (stock, period) 出现 10-Q + UNKNOWN 成对重复。
    """
    base = base_dir or os.path.join(ROOT, "reports", "sec_filings", stock.ticker)
    if not os.path.isdir(base):
        return 0
    rows = session.scalars(select(Filing).where(Filing.stock_id == stock.id)).all()
    existing = {f.local_path for f in rows}
    # uq_filing 唯一约束是 (stock_id, form_type, period)：一个申报期只允许一条记录。
    existing_fp = {(f.form_type, f.period) for f in rows if f.period}
    known_periods = {f.period for f in rows if f.period and f.form_type != "UNKNOWN"}
    n = 0
    entries = sorted(os.listdir(base))
    for file_pass in (True, False):
        for name in entries:
            full = os.path.join(base, name)
            if file_pass:
                if not os.path.isfile(full):
                    continue
                cand = full if _valid_file(full) else None
            else:
                if not os.path.isdir(full):
                    continue
                cand = _representative(full)
            if cand is None:
                continue
            rel = _rel(cand)
            if rel in existing:
                continue
            period = _period_of(name)
            # 目录的代表文件名优先（目录名如 pfe-20260329 不含 form token），
            # 顶层文件 basename 即 name，两者统一从 cand 取
            form = _form_of(os.path.basename(cand)) or _form_of(name) or "UNKNOWN"
            if period is not None and (form, period) in existing_fp:
                continue
            if period is not None and form == "UNKNOWN" and period in known_periods:
                continue  # 该申报期已有已知类型行，UNKNOWN 行只会成对重复，不再登记
            session.add(Filing(stock_id=stock.id, form_type=form, period=period, local_path=rel))
            existing.add(rel)
            if period is not None:
                existing_fp.add((form, period))
                if form != "UNKNOWN":
                    known_periods.add(period)
            n += 1
    session.commit()
    return n


def register_analysis(session, stock, base_dir: str | None = None) -> int:
    base = base_dir or os.path.join(ROOT, "reports", "sec_analysis", stock.ticker)
    if not os.path.isdir(base):
        return 0
    existing = {(a.form_type, a.fiscal_year, a.quarter)
                for a in session.scalars(select(Analysis).where(Analysis.stock_id == stock.id))}
    n = 0
    for name in sorted(os.listdir(base)):
        if not name.endswith(".md"):
            continue
        form = _form_of(name)
        if form is None:
            continue  # 文件名无申报类型（如 {TICKER}_analysis_*.md 旧汇总），跳过不造假
        quarter = None
        if (qm := _QTR.search(name)):
            # 季度标签优先：10-Q_FY2024Q3.md 这类变体同时含 FY 与季度字样，
            # 若先匹配 _FY 会被误判成年报（quarter=None）挤占年报槽位
            year = int(qm.group(1))
            quarter = f"{qm.group(1)}Q{qm.group(2)}"
        elif (fy := _FY.search(name)):
            year = int(fy.group(1))  # 年报文件名形如 10-K_FY2024.md
        else:
            # form token 有、FY/季度标签都没有 → 显式告警后跳过，不静默丢弃
            print(f"[register] skip {name}: no fiscal year/quarter in filename", file=sys.stderr)
            continue
        full = os.path.join(base, name)
        if not _valid_file(full) or (form, year, quarter) in existing:
            continue
        with open(full, encoding="utf-8") as f:
            content = f.read()
        metrics = parse_analysis_metrics(content)
        # 头部 `生成器版本: analysis-v2` 行 → "v2"；无该行 → NULL（legacy 旧数据）
        session.add(Analysis(stock_id=stock.id, form_type=form, fiscal_year=year, quarter=quarter,
                             local_path=_rel(full), metrics=metrics or None,
                             generator_version=parse_md_version(content, "analysis")))
        existing.add((form, year, quarter))
        n += 1
    session.commit()
    return n


def register_dcf(session, stock, base_dir: str | None = None) -> int:
    base = base_dir or os.path.join(ROOT, "reports", "dcf")
    if not os.path.isdir(base):
        return 0
    # 幂等：同一 local_path 只登记一次
    existing = set(session.scalars(
        select(DcfReport.local_path).where(DcfReport.stock_id == stock.id)))
    n = 0
    for name in sorted(os.listdir(base)):
        if not (name.startswith(f"{stock.symbol()}_DCF") or name.startswith(f"{stock.ticker}_DCF")):
            continue
        if not name.endswith(".md"):
            continue
        full = os.path.join(base, name)
        if not _valid_file(full):
            continue
        rel = _rel(full)
        if rel in existing:
            continue
        with open(full, encoding="utf-8") as f:
            content = f.read()
        valuation = parse_dcf_valuation(content)
        # 头部 `生成器版本: dcf-v1` 行 → "v1"；无该行 → NULL（legacy 旧数据）
        session.add(DcfReport(stock_id=stock.id, local_path=rel, valuation=valuation or None,
                              generated_at=datetime.fromtimestamp(os.path.getmtime(full)),
                              generator_version=parse_md_version(content, "dcf")))
        existing.add(rel)
        n += 1
    session.commit()
    return n
