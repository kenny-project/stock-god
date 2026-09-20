#!/usr/bin/env python3
"""一次性修复脚本：dcf_report 表存量记录按 local_path 重新 parse_dcf_valuation，
补齐"### 每股估值"每股口径字段（intrinsic_value_per_share / safety_25_price /
safety_50_price）。旧字段（price / intrinsic_value_musd / owner_earnings_by_year）保留。

背景：parse_dcf_valuation 早期只抓总市值口径 intrinsic_value_musd（$M），
DcfChart 把它与每股股价画一起量纲差数千倍。解析器补齐每股字段后，
存量 valuation JSON 需重解析。

用法（仓库根目录运行）:
    python3 scripts/webapp_reparse_dcf.py             # 默认 dry-run 预览不写库
    python3 scripts/webapp_reparse_dcf.py --apply     # 实际写库
    python3 scripts/webapp_reparse_dcf.py --db data/stock_god.db
幂等：重复运行重算出相同 JSON 无副作用；报告文件缺失的跳过并警告，不臆造。
"""
import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "webapp", "backend")
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "stock_god.db")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="实际写库（默认仅预览，不写库）")
    ap.add_argument("--db", default=os.environ.get("STOCKGOD_DB", DEFAULT_DB))
    args = ap.parse_args()

    sys.path.insert(0, BACKEND)
    from db import ROOT, make_engine, make_session_factory
    from models import DcfReport, Stock
    from services.metrics import parse_dcf_valuation
    from sqlalchemy import select

    engine = make_engine(args.db)
    with make_session_factory(engine)() as session:
        rows = session.scalars(select(DcfReport)).all()
        updated, missing, unchanged = 0, [], 0
        skipped = 0
        for d in rows:
            stock = session.get(Stock, d.stock_id) if d.stock_id else None
            if stock is None:
                # stock 行缺失（孤儿数据）：告警跳过，不臆造 ticker 也不崩溃
                print(f"警告: dcf_report#{d.id} stock_id={d.stock_id} 对应 stock 缺失，跳过",
                      file=sys.stderr)
                skipped += 1
                continue
            full = os.path.join(ROOT, d.local_path)
            if not os.path.isfile(full):
                missing.append(d.local_path)
                continue
            with open(full, encoding="utf-8") as f:
                valuation = parse_dcf_valuation(f.read())
            if not valuation:
                unchanged += 1  # 解析不出任何字段，保留原值，不臆造
                continue
            if valuation == d.valuation:
                unchanged += 1
                continue
            ticker = stock.ticker
            per_share = valuation.get("intrinsic_value_per_share")
            print(f"{'[apply] ' if args.apply else '[dry-run] '}"
                  f"{ticker} {os.path.basename(d.local_path)}: "
                  f"per_share={per_share} safety25={valuation.get('safety_25_price')} "
                  f"safety50={valuation.get('safety_50_price')}")
            if args.apply:
                d.valuation = valuation
            updated += 1
        if args.apply:
            session.commit()

    mode = "（dry-run 预览，未写库）" if not args.apply else ""
    extra = f"，跳过 {skipped} 条（stock 缺失）" if skipped else ""
    print(f"dcf_report 总数 {len(rows)}，{'已更新' if args.apply else '待更新'} "
          f"{updated} 条，无变化 {unchanged} 条{extra} {mode}")
    for p in missing:
        print(f"  警告: 报告文件缺失，跳过: {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
