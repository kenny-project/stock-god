#!/usr/bin/env python3
"""
手续费统计报告

功能：查询 Futu 账户的交易手续费（股票+债券+基金），按月/年/总汇总
用法：
    python3 scripts/fee_report.py              # 查询 2026 年
    python3 scripts/fee_report.py --year 2025  # 查询 2025 年
    python3 scripts/fee_report.py --start 2026-01-01 --end 2026-06-30

流程：
    1. history_order_list_query 获取股票/期权历史订单 → order_fee_query 查手续费
    2. get_acc_cash_flow 逐日查询债券托管费、基金交易费
    3. 按月汇总所有费用
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

# 加载 futuapi 公共模块
FUTUAPI_SCRIPTS = os.path.expanduser("~/.openclaw/skills/futuapi/scripts")
sys.path.insert(0, FUTUAPI_SCRIPTS)
from common import (
    create_trade_context,
    parse_trd_env,
    parse_security_firm,
    get_default_acc_id,
    get_default_trd_env,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")


def get_history_orders(start, end, market=None, security_firm=None):
    """获取历史订单列表"""
    from futu import TrdEnv

    acc_id = get_default_acc_id()
    trd_env = TrdEnv.REAL

    ctx = None
    try:
        ctx = create_trade_context(market, security_firm=parse_security_firm(security_firm))
        kwargs = {
            "trd_env": trd_env,
            "acc_id": acc_id,
            "start": start,
            "end": end,
        }
        ret, data = ctx.history_order_list_query(**kwargs)
        check_ret(ret, data, ctx, "获取历史订单")
        if is_empty(data):
            return []
        return df_to_records(data)
    finally:
        safe_close(ctx)


def get_order_fees(order_ids, market=None, security_firm=None):
    """批量查询订单手续费（每次最多 20 个）"""
    from futu import TrdEnv

    acc_id = get_default_acc_id()
    trd_env = TrdEnv.REAL

    ctx = None
    try:
        ctx = create_trade_context(market, security_firm=parse_security_firm(security_firm))
        all_fees = []

        batch_size = 20
        for i in range(0, len(order_ids), batch_size):
            batch = order_ids[i:i + batch_size]
            ret, data = ctx.order_fee_query(order_id_list=batch, trd_env=trd_env, acc_id=acc_id)
            if ret == 0 and not is_empty(data):
                all_fees.extend(df_to_records(data))

            if i + batch_size < len(order_ids):
                time.sleep(3.5)

        return all_fees
    finally:
        safe_close(ctx)


def get_cash_flow_records(start_date, end_date, acc_ids=None):
    """逐日查询 get_acc_cash_flow，收集债券托管费和基金交易费"""
    from futu import TrdEnv

    if acc_ids is None:
        # 默认查所有已知真实账户
        acc_ids = [get_default_acc_id()]

    all_records = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    total_days = (end - start).days + 1

    for acc_id in acc_ids:
        ctx = None
        try:
            ctx = create_trade_context()
            current = start
            day_idx = 0
            while current <= end:
                d = current.strftime("%Y-%m-%d")
                ret, data = ctx.get_acc_cash_flow(
                    clearing_date=d, trd_env=TrdEnv.REAL, acc_id=acc_id
                )
                if ret == 0 and hasattr(data, "shape") and data.shape[0] > 0:
                    for _, row in data.iterrows():
                        ctype = str(row.get("cashflow_type", ""))
                        remark = str(row.get("cashflow_remark", ""))
                        amount = float(row.get("cashflow_amount", 0))
                        # 筛选费用类记录（托管费、交易费等），排除纯资金转入转出
                        is_fee = (
                            "Custodian" in ctype
                            or "Fee" in ctype
                            or "fee" in remark.lower()
                        )
                        is_fund = "Fund" in ctype
                        is_bond = "Bond" in remark or "Treasury" in remark

                        if is_fee or is_fund or is_bond:
                            all_records.append({
                                "date": d,
                                "cashflow_type": ctype,
                                "cashflow_direction": str(row.get("cashflow_direction", "")),
                                "cashflow_amount": amount,
                                "currency": str(row.get("currency", "")),
                                "cashflow_remark": remark,
                                "acc_id": acc_id,
                            })
                current += timedelta(days=1)
                day_idx += 1
                # 频率控制：每 3 秒最多 10 次
                if day_idx % 8 == 0:
                    time.sleep(3)
                else:
                    time.sleep(0.4)
        finally:
            safe_close(ctx)

    return all_records


def group_by_month(records, time_field="create_time"):
    """按月分组统计股票手续费"""
    monthly = defaultdict(float)
    for r in records:
        ct = r.get(time_field, "")
        if isinstance(ct, str) and len(ct) >= 7:
            month = ct[:7]
        else:
            month = "未知"
        fee_info = r.get("fee_info", {})
        fee = fee_info.get("fee_amount", fee_info.get("total_fee", 0))
        monthly[month] += abs(fee)
    return dict(sorted(monthly.items()))


def group_cashflow_by_month(records):
    """按月分组统计债券/基金费用"""
    monthly = defaultdict(lambda: {"bond_fee": 0.0, "fund_in": 0.0, "fund_out": 0.0})
    for r in records:
        d = r.get("date", "")
        month = d[:7] if len(d) >= 7 else "未知"
        ctype = r.get("cashflow_type", "")
        amount = r.get("cashflow_amount", 0)
        direction = r.get("cashflow_direction", "")

        if "Custodian" in ctype or "Fee" in ctype:
            monthly[month]["bond_fee"] += abs(amount)
        elif "Fund" in ctype and direction == "IN":
            monthly[month]["fund_in"] += amount
        elif "Fund" in ctype and direction == "OUT":
            monthly[month]["fund_out"] += abs(amount)

    return dict(sorted(monthly.items()))


def generate_report(orders, fees, cashflow_records, year, start_date, end_date):
    """生成 Markdown 报告"""
    # === 股票手续费 ===
    fee_map = {}
    for f in fees:
        oid = f.get("order_id", "")
        if oid:
            fee_map[oid] = f

    fee_orders = []
    for o in orders:
        oid = o.get("order_id", "")
        if oid in fee_map:
            o["fee_info"] = fee_map[oid]
            fee_orders.append(o)

    stock_monthly = group_by_month(fee_orders, "create_time") if fee_orders else {}
    stock_total = sum(stock_monthly.values())

    # === 债券/基金费用 ===
    cf_monthly = group_cashflow_by_month(cashflow_records) if cashflow_records else {}
    bond_total = sum(v["bond_fee"] for v in cf_monthly.values())
    fund_in_total = sum(v["fund_in"] for v in cf_monthly.values())
    fund_out_total = sum(v["fund_out"] for v in cf_monthly.values())

    # === 综合月度数据 ===
    all_months = sorted(set(list(stock_monthly.keys()) + list(cf_monthly.keys())))
    combined_monthly = {}
    for m in all_months:
        stock_amt = stock_monthly.get(m, 0)
        bond_amt = cf_monthly.get(m, {}).get("bond_fee", 0)
        combined_monthly[m] = stock_amt + bond_amt

    grand_total = stock_total + bond_total

    currency = "USD"

    lines = [
        f"# {year} 年手续费统计报告",
        "",
        f"**查询期间**: {start_date} ~ {end_date}",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
        "## 费用总览",
        "",
        "| 费用类型 | 金额 |",
        "|:---|---:|",
        f"| 股票交易手续费 | {stock_total:,.2f} {currency} |",
        f"| 债券托管费 | {bond_total:,.2f} {currency} |",
        f"| **合计费用** | **{grand_total:,.2f} {currency}** |",
    ]

    if stock_monthly:
        stock_order_count = len(fee_orders)
        lines.append(f"| 股票订单数 | {stock_order_count} 笔 |")

    if cashflow_records:
        bond_count = sum(1 for r in cashflow_records if "Custodian" in r.get("cashflow_type", "") or "Fee" in r.get("cashflow_type", ""))
        fund_count = sum(1 for r in cashflow_records if "Fund" in r.get("cashflow_type", ""))
        lines.append(f"| 债券托管费记录 | {bond_count} 笔 |")
        lines.append(f"| 基金交易记录 | {fund_count} 笔 |")

    # === 月度明细 ===
    if combined_monthly:
        lines.extend([
            "",
            "---",
            "",
            "## 月度费用明细",
            "",
            "| 月份 | 股票手续费 | 债券托管费 | 合计 | 占比 |",
            "|:---|---:|---:|---:|---:|",
        ])
        for m in all_months:
            s = stock_monthly.get(m, 0)
            b = cf_monthly.get(m, {}).get("bond_fee", 0)
            t = s + b
            pct = (t / grand_total * 100) if grand_total > 0 else 0
            lines.append(f"| {m} | {s:,.2f} | {b:,.2f} | {t:,.2f} {currency} | {pct:.1f}% |")

    # === 汇总 ===
    if grand_total > 0:
        lines.extend([
            "",
            "---",
            "",
            "## 汇总",
            "",
            "| 统计项 | 金额 |",
            "|:---|---:|",
            f"| **年度总费用** | **{grand_total:,.2f} {currency}** |",
        ])
        if all_months:
            lines.append(f"| 月均费用 | {grand_total / len(all_months):,.2f} {currency} |")
            lines.append(f"| 最高月份 | {max(combined_monthly.values()):,.2f} {currency} ({max(combined_monthly, key=combined_monthly.get)}) |")
            lines.append(f"| 最低月份 | {min(combined_monthly.values()):,.2f} {currency} ({min(combined_monthly, key=combined_monthly.get)}) |")

    # === 基金交易记录 ===
    fund_records = [r for r in cashflow_records if "Fund" in r.get("cashflow_type", "")]
    if fund_records:
        lines.extend([
            "",
            "---",
            "",
            "## 基金交易记录",
            "",
            "| 日期 | 类型 | 方向 | 金额 | 备注 |",
            "|:---|:---|:---|---:|:---|",
        ])
        for r in fund_records:
            direction = "赎回" if r["cashflow_direction"] == "IN" else "申购"
            lines.append(
                f"| {r['date']} | {r['cashflow_type']} | {direction} "
                f"| {r['cashflow_amount']:,.2f} {r['currency']} | {r['cashflow_remark']} |"
            )

    # === 债券托管费明细 ===
    bond_records = [r for r in cashflow_records if "Custodian" in r.get("cashflow_type", "") or "Fee" in r.get("cashflow_type", "")]
    if bond_records:
        lines.extend([
            "",
            "---",
            "",
            "## 债券托管费明细",
            "",
            "| 日期 | 类型 | 金额 | 备注 |",
            "|:---|:---|---:|:---|",
        ])
        for r in bond_records:
            lines.append(
                f"| {r['date']} | {r['cashflow_type']} "
                f"| {r['cashflow_amount']:,.2f} {r['currency']} | {r['cashflow_remark']} |"
            )

    # === 股票费用明细（最近 20 笔）===
    if fee_orders:
        lines.extend([
            "",
            "---",
            "",
            "## 股票费用明细（最近 20 笔）",
            "",
            "| 时间 | 股票 | 方向 | 金额 | 手续费 |",
            "|:---|:---|:---|---:|---:|",
        ])
        for o in fee_orders[:20]:
            t = o.get("create_time", "")
            code = o.get("code", "")
            side = "买入" if o.get("trd_side", "") == "BUY" else "卖出"
            dealt_avg = o.get("dealt_avg_price", 0)
            dealt_qty = o.get("dealt_qty", 0)
            amount = float(dealt_avg) * float(dealt_qty) if dealt_avg and dealt_qty else 0
            fee_info = o.get("fee_info", {})
            total_fee = fee_info.get("fee_amount", fee_info.get("total_fee", 0))
            lines.append(f"| {t} | {code} | {side} | {amount:,.2f} | {total_fee:,.2f} |")

        if len(fee_orders) > 20:
            lines.append(f"\n> 共 {len(fee_orders)} 笔有手续费的订单，仅显示最近 20 笔")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="手续费统计报告")
    parser.add_argument("--year", type=int, default=datetime.now().year,
                        help="查询年份 (default: 当前年份)")
    parser.add_argument("--start", type=str, default=None,
                        help="起始日期 yyyy-MM-dd (覆盖 --year)")
    parser.add_argument("--end", type=str, default=None,
                        help="结束日期 yyyy-MM-dd (覆盖 --year)")
    parser.add_argument("--market", choices=["US", "HK", "HKCC", "CN", "SG"],
                        default=None, help="交易市场")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG", "FUTUAU", "FUTUCA", "FUTUJP", "FUTUMY"],
                        default=None, help="券商标识")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    # 确定日期范围
    if args.start and args.end:
        start_date = args.start
        end_date = args.end
        year = args.year
    else:
        year = args.year
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

    print(f"查询 {start_date} ~ {end_date} 的费用记录...")

    try:
        # 1. 股票历史订单 + 手续费
        print("  [1/2] 查询股票订单...")
        orders = get_history_orders(start_date, end_date, args.market, args.security_firm)
        print(f"    找到 {len(orders)} 个股票订单")

        fees = []
        if orders:
            order_ids = [o.get("order_id", "") for o in orders if o.get("order_id")]
            print(f"    查询 {len(order_ids)} 个订单的手续费...")
            fees = get_order_fees(order_ids, args.market, args.security_firm)
            print(f"    获取到 {len(fees)} 条手续费记录")

        # 2. 债券/基金现金流
        print("  [2/2] 查询债券/基金费用（逐日查询，需要几分钟）...")
        cashflow_records = get_cash_flow_records(start_date, end_date)
        bond_count = sum(1 for r in cashflow_records if "Custodian" in r.get("cashflow_type", "") or "Fee" in r.get("cashflow_type", ""))
        fund_count = sum(1 for r in cashflow_records if "Fund" in r.get("cashflow_type", ""))
        print(f"    债券托管费: {bond_count} 条，基金交易: {fund_count} 条")

    except Exception as e:
        print(f"查询失败: {e}")
        sys.exit(1)

    if args.json:
        stock_monthly = group_by_month(fees, "create_time") if fees else {}
        cf_monthly = group_cashflow_by_month(cashflow_records) if cashflow_records else {}
        stock_total = sum(stock_monthly.values())
        bond_total = sum(v["bond_fee"] for v in cf_monthly.values())
        print(json.dumps({
            "period": f"{start_date} ~ {end_date}",
            "stock_orders": len(orders),
            "stock_fee_records": len(fees),
            "stock_monthly": stock_monthly,
            "stock_total": stock_total,
            "cashflow_records": len(cashflow_records),
            "bond_total": bond_total,
            "grand_total": stock_total + bond_total,
        }, ensure_ascii=False, indent=2))
        return

    # 生成报告
    report = generate_report(orders, fees, cashflow_records, year, start_date, end_date)
    if not report:
        print("无费用记录")
        sys.exit(0)

    # 保存报告
    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"fee_report_{year}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n报告已保存: {report_path}")
    print(report)


if __name__ == "__main__":
    main()
