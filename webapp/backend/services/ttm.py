"""TTM 基期计算（后端侧，输入为 DB analysis 记录）。

与 scripts/dcf.py 的 compute_ttm 同一套公式（利润表科目单季滚动、
现金流科目 YTD 累计），但两侧互不 import：dcf.py 从 .md 文件读数、
本模块从 analysis 表 metrics JSON（中文键）读数，避免跨目录依赖。
公式变更须两侧同步。

数据纪律：任一成分缺失置 null 并在 components 注明原因，严禁编造。
"""

import re

# Owner Earnings 三成分：canonical 名 → analysis.metrics 中文键
_METRIC_KEYS = (
    ("net_income", "净利润"),
    ("depreciation", "折旧摊销"),
    ("capex", "资本支出"),
)
# 现金流量表科目（10-Q 披露 YTD 累计，TTM 公式与利润表单季不同；与 dcf.py _CF_METRICS 一致）
_CF_METRICS = ("depreciation", "capex")
# YTD 累计披露的季度 → 人读标签（Q1/H1/9M/FY），如 2026Q3 → "9M'26 累计"
_YTD_LABEL = {1: "Q1", 2: "H1", 3: "9M", 4: "FY"}

_MAINT_CAPEX_RATIO = 0.6  # 维护性 CapEx ≈ 总 CapEx × 60%（与 dcf.py 一致的经验值）


def _fyq(quarter):
    """"2026Q3" → (2026, 3)；其余返回 None"""
    m = re.match(r"^(\d{4})Q([1-4])$", str(quarter or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _fmt(v):
    return f"{v:,.0f}"


def compute_ttm(annual, quarterly):
    """由最新年报 + 年后季报计算 TTM 基期（Owner Earnings 三成分）。

    annual: {"fiscal_year": 2025, "metrics": {中文键: 值}} 或 None
    quarterly: [{"fiscal_year": 2026, "quarter": "2026Q3",
                 "form_type": "10-Q"|"6-K", "metrics": {...}}, ...]

    TTM = 最新年报 + 年后季度 − 上年同期季度：
      - 净利润（利润表科目，单季值）: 逐年滚动求和
      - 折旧摊销 / 资本支出（现金流科目）: 10-Q 为 YTD 累计 →
        TTM = 年报 + 最新 YTD − 上年同期 YTD；全 6-K 单季披露时与利润表同公式；
        10-Q/6-K 混合披露则口径不一致，该成分置 null
    无年后季报时回退最新年报基期（与 dcf.py 调用方回退逻辑一致）。

    返回 dict（base_period/net_income/depreciation/capex/maintenance_capex/
    owner_earnings/components/notes）或 None（无年报数据）；capex 一律输出正数
    （DB 提取为现金流出负数，与 dcf.py owner_earnings_for_record 同取绝对值）。

    notes: 成分名 → 推导/缺失说明（结构化，供前端基期明细表「说明」列）；
    components: 全部 notes 拼接串（与人读日志/契约兼容）。
    """
    if not annual or not annual.get("metrics"):
        return None
    fy = annual["fiscal_year"]
    m = annual["metrics"]

    after, prior = {}, {}  # 年后季度 / 年报自身财年的同期季度，季度号 → 记录
    for r in quarterly or []:
        fyq = _fyq(r.get("quarter"))
        if not fyq or not r.get("metrics"):
            continue
        y, q = fyq
        if y == fy + 1:
            after[q] = r
        elif y == fy:
            prior[q] = r

    ttm = {"base_period": None, "net_income": None, "depreciation": None,
           "capex": None, "maintenance_capex": None, "owner_earnings": None,
           "components": "", "notes": {}}
    notes: dict[str, str] = ttm["notes"]
    general: list[str] = []  # 非成分专属说明（如回退提示），拼在 components 最前

    if not after:
        # 回退最新年报基期：无年后季报时 TTM 公式无成分可加，成分直接取年报值
        ttm["base_period"] = f"最新年报 FY{fy}"
        general.append("无年后季报，回退最新年报基期")
        for key, cn in _METRIC_KEYS:
            if (a_val := m.get(cn)) is not None:
                ttm[key] = a_val
                notes[key] = f"{cn} = 年报 {_fmt(a_val)}"
            else:
                notes[key] = f"{cn}：年报缺该科目，置空"
    else:
        ttm["base_period"] = f"TTM 截至 {fy + 1}Q{max(after)}"
        forms = {r.get("form_type") for r in after.values()}
        ytd_ok = forms == {"10-Q"}  # 现金流 YTD 公式仅当年后季报全为 10-Q
        rolling_ok = forms == {"6-K"}  # 现金流单季公式仅当全为 6-K
        for key, cn in _METRIC_KEYS:
            a_val = m.get(cn)
            if a_val is None:
                notes[key] = f"{cn}：年报缺该科目，置空"
                continue
            if key in _CF_METRICS and ytd_ok:
                # YTD 累计：取期末最新一份与上年同期一份
                q_latest = max(after)
                prior_rec = prior.get(q_latest)
                if prior_rec is None or prior_rec["metrics"].get(cn) is None:
                    notes[key] = (f"{cn}：缺上年同期 {_YTD_LABEL[q_latest]}'{fy % 100} "
                                  f"累计季报，置空")
                    continue
                after_val = after[q_latest]["metrics"][cn]
                val = a_val + after_val - prior_rec["metrics"][cn]
                ttm[key] = val
                notes[key] = (f"{cn} = 年报 {_fmt(a_val)} + "
                              f"{_YTD_LABEL[q_latest]}'{(fy + 1) % 100} 累计 {_fmt(after_val)} − "
                              f"{_YTD_LABEL[q_latest]}'{fy % 100} 累计 "
                              f"{_fmt(prior_rec['metrics'][cn])} = {_fmt(val)}")
            elif key in _CF_METRICS and not rolling_ok:
                notes[key] = f"{cn}：10-Q/6-K 混合披露，YTD/单季口径不一致，无法推导，置空"
            else:
                # 单季值滚动求和（利润表科目恒走此分支；现金流科目仅全 6-K 时适用）
                comps = []
                total = a_val
                missing_q = None
                for q, r in sorted(after.items()):
                    prior_rec = prior.get(q)
                    pv = prior_rec["metrics"].get(cn) if prior_rec else None
                    if pv is None:
                        missing_q = f"{fy + 1}Q{q}"
                        total = None
                        break
                    comps.append((f"{fy + 1}Q{q}", r["metrics"][cn], pv))
                    total += r["metrics"][cn] - pv
                if total is None:
                    notes[key] = f"{cn}：缺 {missing_q} 或其上年同期季报，无法滚动，置空"
                    continue
                ttm[key] = total
                detail = " + ".join(f"{lbl}({_fmt(av)}−{_fmt(pv)})"
                                    for lbl, av, pv in comps)
                notes[key] = f"{cn} = 年报 {_fmt(a_val)} + {detail} = {_fmt(total)}"

    # 维护性 CapEx 与基期 OE（TTM/年报两条路径共用）
    capex = ttm["capex"]
    if capex is not None:
        capex_abs = abs(capex)
        maint = capex_abs * _MAINT_CAPEX_RATIO
        ttm["capex"] = capex_abs
        ttm["maintenance_capex"] = round(maint, 1)
        notes["maintenance_capex"] = (f"CapEx {_fmt(capex_abs)} × "
                                      f"{_MAINT_CAPEX_RATIO} = {_fmt(maint)}")
    else:
        notes["maintenance_capex"] = "CapEx TTM 缺失，置空"
    ni, dep = ttm["net_income"], ttm["depreciation"]
    if ni is not None and dep is not None and ttm["maintenance_capex"] is not None:
        oe = ni + dep - ttm["maintenance_capex"]
        ttm["owner_earnings"] = round(oe, 1)
        notes["owner_earnings"] = (f"净利润 {_fmt(ni)} + 折旧摊销 {_fmt(dep)} − "
                                   f"维护性 CapEx {_fmt(ttm['maintenance_capex'])} "
                                   f"= {_fmt(oe)}")
    else:
        notes["owner_earnings"] = "净利润/折旧摊销/维护性 CapEx 任一缺失，基期 OE 置空"
    ttm["components"] = "；".join(general + list(notes.values()))
    return ttm
