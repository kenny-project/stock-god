#!/usr/bin/env python3
"""
手续费统计报告

功能：查询 Futu 账户的历史订单手续费，按月/年/总汇总
用法：
    python3 scripts/fee_report.py              # 查询 2026 年
    python3 scripts/fee_report.py --year 2025  # 查询 2025 年
    python3 scripts/fee_report.py --start 2026-01-01 --end 2026-06-30

流程：
    1. history_order_list_query 获取历史订单
    2. order_fee_query 批量查询手续费（每次最多 20 个）
    3. 按月汇总
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

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
    # 必须使用真实账户
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

        # 分批查询，每批最多 20 个
        batch_size = 20
        for i in range(0, len(order_ids), batch_size):
            batch = order_ids[i:i + batch_size]
            ret, data = ctx.order_fee_query(order_id_list=batch, trd_env=trd_env, acc_id=acc_id)
            if ret == 0 and not is_empty(data):
                all_fees.extend(df_to_records(data))

            # 频率限制：10 次/30 秒
            if i + batch_size < len(order_ids):
                time.sleep(3.5)

        return all_fees
    finally:
        safe_close(ctx)


def group_by_month(records, time_field="create_time"):
    """按月分组统计"""
    monthly = defaultdict(float)
    for r in records:
        ct = r.get(time_field, "")
        if isinstance(ct, str) and len(ct) >= 7:
            month = ct[:7]  # "2026-01"
        else:
            month = "未知"
        # 手续费在 fee_info 字典里
        fee_info = r.get("fee_info", {})
        fee = fee_info.get("fee_amount", fee_info.get("total_fee", 0))
        monthly[month] += abs(fee)
    return dict(sorted(monthly.items()))


def generate_report(orders, fees, year, start_date, end_date):
    """生成 Markdown 报告"""
    # 构建 order_id -> fee 映射
    fee_map = {}
    for f in fees:
        oid = f.get("order_id", "")
        if oid:
            fee_map[oid] = f

    # 提取所有有手续费的订单
    fee_orders = []
    for o in orders:
        oid = o.get("order_id", "")
        if oid in fee_map:
            o["fee_info"] = fee_map[oid]
            fee_orders.append(o)

    if not fee_orders:
        print(f"未找到 {year} 年的手续费记录")
        return None

    # 按月分组
    monthly = group_by_month(fee_orders, "create_time")

    # 计算总额
    total = sum(monthly.values())

    # 确定货币
    currency = "USD"

    # 生成报告
    lines = [
        f"# {year} 年手续费统计报告",
        "",
        f"**查询期间**: {start_date} ~ {end_date}",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**订单总数**: {len(orders)} 笔",
        f"**有手续费订单**: {len(fee_orders)} 笔",
        "",
        "---",
        "",
        "## 月度费用明细",
        "",
        "| 月份 | 费用金额 | 占比 |",
        "|:---|---:|---:|",
    ]

    for month, amount in monthly.items():
        pct = (amount / total * 100) if total > 0 else 0
        lines.append(f"| {month} | {amount:,.2f} {currency} | {pct:.1f}% |")

    lines.extend([
        "",
        "---",
        "",
        "## 汇总",
        "",
        "| 统计项 | 金额 |",
        "|:---|---:|",
        f"| **年度总费用** | **{total:,.2f} {currency}** |",
    ])

    if monthly:
        lines.append(f"| 月均费用 | {total / len(monthly):,.2f} {currency} |")
        lines.append(f"| 最高月份 | {max(monthly.values()):,.2f} {currency} ({max(monthly, key=monthly.get)}) |")
        lines.append(f"| 最低月份 | {min(monthly.values()):,.2f} {currency} ({min(monthly, key=monthly.get)}) |")

    lines.extend([
        "",
        "---",
        "",
        "## 费用明细（最近 20 笔）",
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
        # 手续费字段是 fee_amount
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

    print(f"查询 {start_date} ~ {end_date} 的历史订单...")

    try:
        # 1. 获取历史订单
        orders = get_history_orders(start_date, end_date, args.market, args.security_firm)
        print(f"  找到 {len(orders)} 个订单")

        if not orders:
            print("无订单记录")
            sys.exit(0)

        # 2. 提取订单 ID
        order_ids = [o.get("order_id", "") for o in orders if o.get("order_id")]
        print(f"  查询 {len(order_ids)} 个订单的手续费...")

        # 3. 批量查询手续费
        fees = get_order_fees(order_ids, args.market, args.security_firm)
        print(f"  获取到 {len(fees)} 条手续费记录")

    except Exception as e:
        print(f"查询失败: {e}")
        sys.exit(1)

    if args.json:
        monthly = group_by_month(fees, "create_time")
        total = sum(monthly.values())
        print(json.dumps({
            "period": f"{start_date} ~ {end_date}",
            "total_orders": len(orders),
            "fee_records": len(fees),
            "monthly": monthly,
            "total": total,
        }, ensure_ascii=False, indent=2))
        return

    # 生成报告
    report = generate_report(orders, fees, year, start_date, end_date)
    if not report:
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
