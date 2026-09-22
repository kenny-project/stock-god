"""分析 ↔ filing 配对：按财年/季度从 filing.period 语义推导，替代尾部对齐 zip。

旧实现是「同 form_type 组内双方升序后尾部对齐 zip」，隐含假设分析集合是
filing 集合的后缀。行级按需生成后该假设不再成立：AVGO 只生成了部分季度的
分析，2021Q1 的老分析被错配到 10-Q 2025-02-02 的行上（点击「财报打开」
打开的是 2021Q1 的内容）。

本模块改为内容语义配对，财年日历从年报 filing 的期次月份推导：
- 财年截止月 M = 年报（10-K/20-F）期次月份的众数（AVGO=11、MSFT=6、AAPL=9），
  众数容忍个别 53 周年份跨月（如 12 月底财年的 10-K 落到 1 月初）
- 10-Q 期次 P：month(P) > M → 财年 = year(P)+1，否则 = year(P)；
  季度 = max(1, round(((month(P)-M) % 12) / 3))——13 周季度跨月漂移 ±1 个月
  经四舍五入仍落对季度；offset 0（财年末月）是 Q4 形态，10-Q 不应有 → 不配对
- 年报期次 E：month(E) >= M → 财年 = year(E)，否则 = year(E)+1
- 无年报 filing 推导不出 M、或事件型 form（6-K/UNKNOWN 等）→ 回退旧尾部对齐
"""
from models import Analysis, Filing

_ANNUAL_FORMS = ("10-K", "20-F")
# 语义配对覆盖的 form：其余（6-K 事件型、UNKNOWN 遗留）走旧尾部对齐
_SEMANTIC_FORMS = ("10-Q",) + _ANNUAL_FORMS


def _mode_month(periods: list[str]) -> int | None:
    months = [int(p[5:7]) for p in periods if len(p) >= 7 and p[5:7].isdigit()]
    if not months:
        return None
    return max(set(months), key=months.count)


def _quarter_of(period: str, m: int) -> tuple[int, int] | None:
    """10-Q 期次（YYYY-MM-DD）→（财年, 季度 1-3）；财年末月/Q4 形态返回 None。"""
    try:
        y, mo = int(period[:4]), int(period[5:7])
    except ValueError:
        return None
    fy = y + 1 if mo > m else y
    offset = (mo - m) % 12
    if offset == 0:
        return None
    return fy, max(1, round(offset / 3))


def _annual_fy_of(period: str, m: int) -> int | None:
    """年报期次 → 财年。"""
    try:
        y, mo = int(period[:4]), int(period[5:7])
    except ValueError:
        return None
    return y + 1 if mo < m else y


def _legacy_pair(analyses: list[Analysis], filings: list[Filing]) -> None:
    """旧尾部对齐 zip：同 form_type 组内双方升序后从最新端（尾部）对齐。"""
    pool: dict[str, list[Filing]] = {}
    for f in filings:
        if f.period:
            pool.setdefault(f.form_type, []).append(f)
    for group in pool.values():
        group.sort(key=lambda f: f.period or "")
    groups: dict[str, list[Analysis]] = {}
    for a in analyses:
        groups.setdefault(a.form_type, []).append(a)
    for ft, group in groups.items():
        group.sort(key=lambda a: (a.fiscal_year or 0, a.quarter or ""))
        for a, f in zip(reversed(group), reversed(pool.get(ft, []))):
            a.period = f.period
            a.filing_id = f.id


def pair_analyses_to_filings(analyses: list[Analysis], filings: list[Filing]) -> None:
    """配对结果挂临时属性 a.period / a.filing_id（不落库），供 AnalysisOut 校验。

    语义配对只覆盖 10-Q/10-K/20-F：季报分析按（财年, 季度）、年报分析按财年
    精确匹配对应 filing；配不上的留空（宁缺毋滥，不回退 zip 再错配一次）。
    """
    annual_periods = [f.period for f in filings
                      if f.period and f.form_type in _ANNUAL_FORMS]
    m = _mode_month(annual_periods)
    if m is None:
        _legacy_pair(analyses, filings)
        return
    by_fq: dict[tuple, Filing] = {}   # (10-Q, fy, q) → filing
    by_fy: dict[tuple, Filing] = {}   # (10-K/20-F, fy) → filing
    for f in filings:
        if not f.period:
            continue
        if f.form_type == "10-Q":
            fq = _quarter_of(f.period, m)
            if fq:
                by_fq[(f.form_type, *fq)] = f
        elif f.form_type in _ANNUAL_FORMS:
            by_fy[(f.form_type, _annual_fy_of(f.period, m))] = f
    # 事件型/遗留 form（6-K、UNKNOWN 等）不适用财年推导，组内回退旧 zip
    _legacy_pair([a for a in analyses if a.form_type not in _SEMANTIC_FORMS],
                 [f for f in filings if f.form_type not in _SEMANTIC_FORMS])
    for a in analyses:
        if a.form_type not in _SEMANTIC_FORMS:
            continue  # 已由 _legacy_pair 处理
        f = None
        if a.fiscal_year:
            if a.quarter and a.form_type == "10-Q":
                f = by_fq.get((a.form_type, a.fiscal_year, int(a.quarter.split("Q")[-1])))
            elif not a.quarter and a.form_type in _ANNUAL_FORMS:
                f = by_fy.get((a.form_type, a.fiscal_year))
        a.filing_id = f.id if f else None
        a.period = f.period if f else None
