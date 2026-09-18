"""存量财报 form_type='UNKNOWN' 修复：用 SEC submissions API 按 reportDate 回填。

EDGAR 下载的主文档文件名形如 qcom-20201227.htm（不含 form token），
register._form_of 匹配不到落入 UNKNOWN。本模块按股票 CIK 拉一次
submissions JSON（含分页），由 filings.recent 构建 reportDate → form 映射回填。

匹配规则（build_report_form_map）：
- 只认四类定期报告：10-K / 10-Q / 20-F / 6-K；ARS、NT 10-K、S-8 等其他 form 一律忽略。
- 同一 reportDate 多条记录时（如 ARS 与 10-K 同期、10-K/A 修订）：
  1) 先在"原始 form"（form 与四类精确相等）中按优先级 10-K > 10-Q > 20-F > 6-K 取；
  2) 无精确命中时才回退到修订件（form 形如 10-K/A，剥掉 /A 后属四类），
     同样按上述优先级，再以 filingDate 最早者胜（原始申报先于再次修订）。
- reportDate 为空/null 的记录（如部分 6-K/杂项申报）不进映射。
- 映射不到的 filing 保持 UNKNOWN 并在结果中列出，绝不臆造（数据不可造假）。
"""
import json
import urllib.request

from sqlalchemy import select
from models import Filing, Stock

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# 与 services/edgar.py 保持一致的 EDGAR UA（SEC 强制要求带 UA）
HEADERS = {"User-Agent": "stock-god personal research wmh@example.com"}
# 优先级：同一 reportDate 同时有年报/季报时年报胜出（ARS 等已在上游被过滤）
FORM_PRIORITY = {"10-K": 0, "10-Q": 1, "20-F": 2, "6-K": 3}
_MAX_PAGES = 5  # recent 之外最多回溯的分页数，覆盖申报频繁公司（Form 4 多）的 5 年窗口


def fetch_submissions(cik: int) -> dict:
    """拉取一个 CIK 的 submissions JSON（主响应 + 分页合并进 recent）。

    整股票只拉这一次（调用方缓存结果），不逐文件调用。
    """
    url = SUBMISSIONS_URL.format(cik=cik)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    recent = data.get("filings", {}).get("recent", {})
    for f in (data.get("filings", {}).get("files") or [])[:_MAX_PAGES]:
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(f"{SUBMISSIONS_URL.rsplit('/', 1)[0]}/{f['name']}",
                                           headers=HEADERS), timeout=30) as resp:
                page = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError):
            break  # 分页失败不影响 recent 已覆盖的记录
        for key, values in page.items():
            if isinstance(recent.get(key), list) and isinstance(values, list):
                recent[key].extend(values)
    return recent


def _pick(records: list[tuple[str, str]]) -> str | None:
    """(form, filingDate) 列表按 form 优先级 → filingDate 最早 选出 form。"""
    return min(records, key=lambda r: (FORM_PRIORITY[r[0]], r[1]))[0]


def build_report_form_map(recent: dict) -> dict[str, str]:
    """filings.recent（form/reportDate/filingDate 三列等长数组）→ {reportDate: form}。

    见模块 docstring 的匹配规则说明。
    """
    forms = recent.get("form") or []
    report_dates = recent.get("reportDate") or []
    filing_dates = recent.get("filingDate") or []
    exact: dict[str, list[tuple[str, str]]] = {}
    amended: dict[str, list[tuple[str, str]]] = {}
    for form, rd, fd in zip(forms, report_dates, filing_dates):
        if not rd:
            continue  # reportDate 为空的申报不进映射
        base = form.split("/")[0].strip() if "/" in form else form
        if base not in FORM_PRIORITY:
            continue  # ARS / NT 10-K / S-8 / Form 4 等一律忽略
        bucket = exact if form == base else amended
        bucket.setdefault(rd, []).append((base, fd))
    return {rd: _pick(recs) for rd, recs in exact.items()} | \
           {rd: _pick(recs) for rd, recs in amended.items() if rd not in exact}


def fix_unknown_filings(session, fetcher=fetch_submissions, dry_run: bool = False) -> dict:
    """回填所有 form_type='UNKNOWN' 的 filing。返回统计 dict。

    fetcher(cik) → recent dict（测试可注入 mock，不造假网络数据）。
    同 (stock_id, form_type, period) 已有非 UNKNOWN 记录时跳过该条（唯一约束冲突），
    保持 UNKNOWN 并计入 skipped_conflict。幂等：修过的行不再是 UNKNOWN，重跑为 0 改动。
    """
    rows = session.execute(
        select(Filing, Stock).join(Stock, Filing.stock_id == Stock.id)
        .where(Filing.form_type == "UNKNOWN")).all()
    existing_fp = {(f.stock_id, f.form_type, f.period)
                   for f in session.scalars(select(Filing)).all() if f.period}
    recent_cache: dict[int, dict] = {}
    stats = {"total": len(rows), "fixed": 0, "unmatched": [], "skipped_conflict": [],
             "fetch_failed": []}
    for filing, stock in rows:
        if stock.cik is None:
            stats["unmatched"].append((stock.ticker, filing.period, "no CIK"))
            continue
        if stock.cik not in recent_cache:
            try:
                recent_cache[stock.cik] = fetcher(stock.cik)
            except Exception as e:  # 网络失败：整股票回填中止，宁可保持 UNKNOWN
                recent_cache[stock.cik] = None
                print(f"[form_fix] WARNING: fetch submissions CIK{stock.cik} 失败: {e}")
        recent = recent_cache[stock.cik]
        if recent is None:
            stats["fetch_failed"].append(stock.ticker)
            continue
        form = build_report_form_map(recent).get(filing.period)
        if form is None:
            stats["unmatched"].append((stock.ticker, filing.period, "no match"))
            continue
        if filing.period and (filing.stock_id, form, filing.period) in existing_fp:
            stats["skipped_conflict"].append((stock.ticker, form, filing.period))
            continue
        if not dry_run:
            filing.form_type = form
        if filing.period:
            existing_fp.add((filing.stock_id, form, filing.period))
        stats["fixed"] += 1
    if not dry_run:
        session.commit()
    return stats
