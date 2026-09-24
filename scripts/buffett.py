#!/usr/bin/env python3
"""
巴菲特/芒格式价值投资数据包生成器。

用法：
    python3 scripts/buffett.py US.QCOM
    python3 scripts/buffett.py QCOM --output reports/buffett/QCOM_buffett_data.md

依赖：本地 webapp 服务已启动（默认 http://127.0.0.1:8000）。
脚本通过 urllib 调用 /api/stocks/{ticker} 相关接口，汇总定量数据后输出 Markdown，
供 buffett_investment_skill.md 中的分析框架使用。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

DEFAULT_API = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm_ticker(raw: str) -> str:
    """US.QCOM -> QCOM；统一大写。"""
    t = raw.strip().upper()
    if "." in t:
        t = t.split(".", 1)[1]
    return t


def api_get(base: str, path: str):
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"API 错误 {e.code}: {url}\n{body}")
    except Exception as e:
        raise SystemExit(f"无法连接后端 {url}: {e}（请先启动 webapp/start.sh）")


def yi(v) -> float | None:
    """百万 -> 亿，保留三位小数。"""
    return round(v / 100, 3) if v is not None else None


def avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    return round(sum(nums) / len(nums), 2) if nums else None


def roic_approx(analysis: dict) -> float | None:
    """近似 ROIC：税后营业利润 / 投入资本（= 总资产 - 现金）。

    税务按 21% 法定税率近似；投入资本用总资产扣除现金及等价物估算，
    因为 SEC 提取规则不区分有息负债与非经营性资产。结果仅作护城河验证参考。
    """
    m = analysis.get("metrics") or {}
    op = m.get("营业利润")
    ta = m.get("总资产")
    cash = m.get("现金及等价物")
    if op is None or ta is None or cash is None:
        return None
    invested = ta - cash
    if invested <= 0:
        return None
    return round(op * 0.79 / invested * 100, 2)


def fmt(v, unit: str = "") -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        s = f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    else:
        s = str(v)
    return f"{s}{unit}" if unit else s


def row_by_key(rows: list[dict], key: str) -> dict | None:
    for r in rows:
        if r["key"] == key:
            return r
    return None


def annual_series(rows: list[dict], annual_labels: list[str], key: str) -> list[tuple[str, float | None]]:
    r = row_by_key(rows, key)
    if not r:
        return []
    return [(lbl, r["values"].get(lbl)) for lbl in annual_labels]


def collect_data(ticker: str, api_base: str) -> dict:
    stock = api_get(api_base, f"/api/stocks/{ticker}")
    fin = api_get(api_base, f"/api/stocks/{ticker}/financials")
    dcf_list = api_get(api_base, f"/api/stocks/{ticker}/dcf")
    analyses = api_get(api_base, f"/api/stocks/{ticker}/analyses")

    annual_cols = [c for c in fin["columns"] if c["kind"] == "annual"]
    annual_labels = [c["label"] for c in sorted(annual_cols, key=lambda x: x["label"], reverse=True)]
    quarter_cols = [c for c in fin["columns"] if c["kind"] == "quarter"]
    quarter_labels = [c["label"] for c in sorted(quarter_cols, key=lambda x: x["label"], reverse=True)]

    # 最新年报分析（用于 CFO/商誉/资本支出等原始百万值）
    annual_analyses = [a for a in analyses if a.get("quarter") is None]
    latest_annual_analysis = max(annual_analyses, key=lambda a: a.get("fiscal_year", 0)) if annual_analyses else None
    am = latest_annual_analysis.get("metrics", {}) if latest_annual_analysis else {}

    # 近 5 年年报 ROIC（近似）序列，按财年倒序与 annual_labels 对齐
    roic_by_year = {a.get("fiscal_year"): roic_approx(a) for a in annual_analyses}
    roic_series = [(lbl, roic_by_year.get(int(lbl[2:])) if lbl.startswith("FY") else None) for lbl in annual_labels]
    avg_roic = avg([v for _, v in roic_series])

    # TTM owner earnings（百万）
    ttm = fin.get("ttm") or {}
    owner_earnings_musd = ttm.get("owner_earnings")
    owner_earnings_yi = yi(owner_earnings_musd)

    # 财务指标序列
    revenue_series = annual_series(fin["rows"], annual_labels, "revenue")
    net_income_series = annual_series(fin["rows"], annual_labels, "net_income")
    gross_margin_series = annual_series(fin["rows"], annual_labels, "gross_margin")
    roe_series = annual_series(fin["rows"], annual_labels, "roe")
    debt_ratio_series = annual_series(fin["rows"], annual_labels, "debt_ratio")
    fcf_series = annual_series(fin["rows"], annual_labels, "fcf")

    # 最新年报/季度
    latest_annual_label = annual_labels[0] if annual_labels else None
    latest_quarter_label = quarter_labels[0] if quarter_labels else None
    total_liabilities_yi = row_by_key(fin["rows"], "total_liabilities")["values"].get(latest_annual_label) if latest_annual_label else None
    total_assets_yi = row_by_key(fin["rows"], "total_assets")["values"].get(latest_annual_label) if latest_annual_label else None

    # 护城河定量
    avg_roe = avg([v for _, v in roe_series])
    gm_values = [v for _, v in gross_margin_series if v is not None]
    avg_gm = avg(gm_values)
    gm_trend = "稳定"
    if len(gm_values) >= 2:
        if gm_values[0] > gm_values[-1] + 1.5:
            gm_trend = "扩张"
        elif gm_values[0] < gm_values[-1] - 1.5:
            gm_trend = "下滑"

    # 近 4 个季度营收/净利润（已按 label 倒序）
    recent_quarters = quarter_labels[:4]
    q_rev = row_by_key(fin["rows"], "revenue")
    q_ni = row_by_key(fin["rows"], "net_income")
    quarterly_snap = []
    for lbl in recent_quarters:
        col = next((c for c in quarter_cols if c["label"] == lbl), None)
        quarterly_snap.append({
            "label": lbl,
            "derived": col.get("derived", False) if col else False,
            "revenue": q_rev["values"].get(lbl) if q_rev else None,
            "net_income": q_ni["values"].get(lbl) if q_ni else None,
        })

    # DCF 估值：取最新一条有估值的报告
    latest_dcf = None
    for d in sorted(dcf_list, key=lambda x: x.get("generated_at", ""), reverse=True):
        if d.get("valuation") and d["valuation"].get("intrinsic_value_per_share") is not None:
            latest_dcf = d
            break

    dcf_val = latest_dcf["valuation"] if latest_dcf else {}
    intrinsic_per_share = dcf_val.get("intrinsic_value_per_share")
    current_price = fin.get("price") or dcf_val.get("price")
    safety_25 = dcf_val.get("safety_25_price")
    safety_50 = dcf_val.get("safety_50_price")

    # 安全边际状态
    if current_price and intrinsic_per_share:
        premium = (current_price / intrinsic_per_share - 1) * 100
        if premium > 5:
            valuation_state = "明显高估"
        elif premium < -10:
            valuation_state = "严重低估"
        else:
            valuation_state = "估值合理"
    else:
        valuation_state = "无法判断"
        premium = None

    # 管理/资本分配：从最新年报现金流量表推断
    cfo_musd = am.get("经营现金流")
    capex_musd = am.get("资本支出")
    financing_musd = am.get("筹资现金流")
    fcf_musd = am.get("自由现金流")

    return {
        "ticker": ticker,
        "name_cn": stock.get("name_cn", ""),
        "name_en": stock.get("name_en", ""),
        "market": stock.get("market", ""),
        "annual_labels": annual_labels,
        "quarterly_snap": quarterly_snap,
        "revenue_series": revenue_series,
        "net_income_series": net_income_series,
        "gross_margin_series": gross_margin_series,
        "roe_series": roe_series,
        "debt_ratio_series": debt_ratio_series,
        "fcf_series": fcf_series,
        "roic_series": roic_series,
        "avg_roic": avg_roic,
        "avg_roe": avg_roe,
        "avg_gm": avg_gm,
        "gm_trend": gm_trend,
        "owner_earnings_musd": owner_earnings_musd,
        "owner_earnings_yi": owner_earnings_yi,
        "total_liabilities_yi": total_liabilities_yi,
        "total_assets_yi": total_assets_yi,
        "goodwill_musd": am.get("商誉"),
        "latest_annual_fy": latest_annual_analysis.get("fiscal_year") if latest_annual_analysis else None,
        "cfo_musd": cfo_musd,
        "capex_musd": capex_musd,
        "financing_musd": financing_musd,
        "fcf_musd": fcf_musd,
        "latest_dcf_at": latest_dcf.get("generated_at") if latest_dcf else None,
        "intrinsic_per_share": intrinsic_per_share,
        "current_price": current_price,
        "safety_25_price": safety_25,
        "safety_50_price": safety_50,
        "valuation_state": valuation_state,
        "premium_pct": premium,
    }


def render_markdown(d: dict) -> str:
    lines = []
    lines.append(f"# {d['name_cn'] or d['ticker']} ({d['ticker']}) 伯克希尔式价值投资数据包")
    lines.append("")
    lines.append(f"- **生成日期**：{date.today().isoformat()}")
    lines.append(f"- **市场**：{d['market']}")
    lines.append(f"- **当前股价**：${fmt(d['current_price'])}")
    lines.append("")

    lines.append("## 1. 能力圈与业务模式（数据侧输入）")
    lines.append("")
    lines.append("- **主营业务**：需结合年报 Item 1/7 定性补充；定量上需观察收入结构、毛利率与现金流来源。")
    lines.append("- **业务可预测性**：需评估收入驱动因素（产品周期、客户集中度、地域与监管）在未来 5-10 年的稳定性；数据不足时标记为 **Too Hard**。")
    lines.append("")

    lines.append("## 2. 经济护城河定量验证")
    lines.append("")
    lines.append("| 财年 | 营收（亿$） | 净利润（亿$） | 毛利率（%） | ROIC（%） | ROE（%） | 资产负债率（%） | FCF（亿$） |")
    lines.append("|------|------------|--------------|------------|----------|---------|----------------|------------|")
    for i, lbl in enumerate(d["annual_labels"]):
        rev = d["revenue_series"][i][1] if i < len(d["revenue_series"]) else None
        ni = d["net_income_series"][i][1] if i < len(d["net_income_series"]) else None
        gm = d["gross_margin_series"][i][1] if i < len(d["gross_margin_series"]) else None
        roic = d["roic_series"][i][1] if i < len(d["roic_series"]) else None
        roe = d["roe_series"][i][1] if i < len(d["roe_series"]) else None
        dr = d["debt_ratio_series"][i][1] if i < len(d["debt_ratio_series"]) else None
        fcf = d["fcf_series"][i][1] if i < len(d["fcf_series"]) else None
        lines.append(f"| {lbl} | {fmt(rev)} | {fmt(ni)} | {fmt(gm)} | {fmt(roic)} | {fmt(roe)} | {fmt(dr)} | {fmt(fcf)} |")
    lines.append("")
    lines.append(f"- **近 5 年平均 ROIC（近似）**：{fmt(d['avg_roic'])}%（门槛 >15%）")
    lines.append(f"- **近 5 年平均 ROE**：{fmt(d['avg_roe'])}%（门槛 >15%）")
    lines.append(f"- **近 5 年平均毛利率**：{fmt(d['avg_gm'])}%")
    lines.append(f"- **毛利率趋势**：{d['gm_trend']}")
    lines.append("")

    lines.append("## 3. 所有者收益与财务健康度")
    lines.append("")
    lines.append(f"- **最新 TTM 所有者收益（Owner Earnings）**：{fmt(d['owner_earnings_yi'])} 亿$（约 {fmt(d['owner_earnings_musd'])} 百万美元）")
    lines.append(f"- **最新年报总负债**：{fmt(d['total_liabilities_yi'])} 亿$")
    if d["owner_earnings_yi"] and d["total_liabilities_yi"]:
        years = round(d["total_liabilities_yi"] / d["owner_earnings_yi"], 2)
        lines.append(f"- **负债清偿能力（总负债 / 所有者收益）**：约 {years} 年（<3-4 年为健康）")
    else:
        lines.append("- **负债清偿能力**：数据不足")
    if d["goodwill_musd"] is not None and d["total_assets_yi"]:
        gw_ratio = round(d["goodwill_musd"] / (d["total_assets_yi"] * 100) * 100, 2) if d["total_assets_yi"] else None
        lines.append(f"- **商誉/总资产**：{fmt(gw_ratio)}%（最新年报商誉 {fmt(d['goodwill_musd'])} 百万美元）")
    else:
        lines.append("- **商誉/总资产**：数据不足")
    lines.append("")

    lines.append("## 4. 资本分配记录（最新年报现金流量表）")
    lines.append("")
    lines.append(f"- **经营现金流（CFO）**：{fmt(d['cfo_musd'])} 百万美元")
    lines.append(f"- **资本支出（CapEx）**：{fmt(d['capex_musd'])} 百万美元")
    lines.append(f"- **自由现金流（FCF）**：{fmt(d['fcf_musd'])} 百万美元")
    lines.append(f"- **筹资现金流**：{fmt(d['financing_musd'])} 百万美元（通常为分红/回购净流出）")
    lines.append("- **回购质量**：需结合历史股价与内在价值判断；当股价高于内在价值时大额回购会毁灭价值。")
    lines.append("- **维护性 vs 增长性 CapEx**：SEC 提取规则无法直接拆分维护性资本支出，当前用总 CapEx 作为保守替代；如可获取管理层的维护性 CapEx 口径，应优先使用。")
    lines.append("")

    lines.append("## 5. 内在价值与安全边际")
    lines.append("")
    lines.append(f"- **DCF 估算每股内在价值**：${fmt(d['intrinsic_per_share'])}")
    lines.append(f"- **当前股价**：${fmt(d['current_price'])}")
    lines.append(f"- **估值状态**：{d['valuation_state']}（相对内在价值溢价 {fmt(d['premium_pct'])}%）")
    lines.append(f"- **25% 安全边际买入价**：${fmt(d['safety_25_price'])}")
    lines.append(f"- **50% 安全边际买入价**：${fmt(d['safety_50_price'])}")
    lines.append("- **安全边际建议**：宽护城河/高确定性公司可接受 15-20% 折扣；窄护城河/周期性公司建议 30-40% 折扣。")
    lines.append("")

    lines.append("## 6. 近季度收入/净利润快照")
    lines.append("")
    lines.append("| 季度 | 营收（亿$） | 净利润（亿$） | 备注 |")
    lines.append("|------|------------|--------------|------|")
    for q in d["quarterly_snap"]:
        note = "推算 Q4" if q["derived"] else ""
        lines.append(f"| {q['label']}{'*' if q['derived'] else ''} | {fmt(q['revenue'])} | {fmt(q['net_income'])} | {note} |")
    lines.append("")

    lines.append("## 7. 芒格逆向思考（需结合定性补充）")
    lines.append("")
    lines.append("1. **大客户/关键合同风险**：核心客户自研或转向竞争对手。")
    lines.append("2. **监管与诉讼风险**：专利授权模式面临全球反垄断与费率诉讼。")
    lines.append("3. **技术路线/周期风险**：移动通信标准迭代或半导体下行周期冲击收入与利润率。")
    lines.append("")

    lines.append("## 8. 伯克希尔式最终裁定（由分析师填写）")
    lines.append("")
    lines.append("- **决策建议**：[ 强烈建议买入 / 放入观察清单 / 一票否决 ]")
    lines.append("- **核心逻辑**：[2-3 句话总结]")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成巴菲特/芒格式价值投资分析数据包")
    parser.add_argument("ticker", help="股票代码，如 US.QCOM 或 QCOM")
    parser.add_argument("--api-base", default=DEFAULT_API, help=f"webapp API 地址（默认 {DEFAULT_API}）")
    parser.add_argument("-o", "--output", help="输出文件路径（默认 stdout）")
    args = parser.parse_args()

    ticker = norm_ticker(args.ticker)
    data = collect_data(ticker, args.api_base)
    md = render_markdown(data)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"已生成：{args.output}")
    else:
        print(md)


if __name__ == "__main__":
    main()
