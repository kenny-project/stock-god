#!/usr/bin/env python3
"""一次性修复脚本：修正 dcf_report 表中"文件已被重跑覆盖、行仍停留旧值"的存量记录。

背景：dcf.py 曾输出固定名 {ticker}_DCF.md，重跑直接覆盖同一文件；
register_dcf 按 local_path 去重，路径未变即跳过，于是 generated_at/valuation
停留旧值，与文件实际内容不一致。dcf.py 已改为带秒级时间戳的文件名
（每次估值新建记录），本脚本只修存量：扫描 dcf_report 全表，
凡文件 mtime 晚于 generated_at 的行，用 parse_dcf_valuation 重解析 valuation，
并把 generated_at 更新为文件 mtime。

用法（仓库根目录运行）:
    python3 scripts/webapp_fix_stale_dcf.py             # 默认 dry-run 预览不写库
    python3 scripts/webapp_fix_stale_dcf.py --apply     # 实际写库
幂等：写库后 generated_at == 文件 mtime，重复运行无行可更新。
文件缺失的行跳过并告警；解析不出字段的行保留原 valuation，不臆造。
"""
import argparse
import os
import sys
from datetime import datetime

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
            mtime = datetime.fromtimestamp(os.path.getmtime(full))
            if d.generated_at is not None and mtime <= d.generated_at:
                unchanged += 1
                continue
            with open(full, encoding="utf-8") as f:
                valuation = parse_dcf_valuation(f.read())
            tag = "[apply] " if args.apply else "[dry-run] "
            if valuation:
                print(f"{tag}{stock.ticker} {os.path.basename(d.local_path)}: "
                      f"generated_at {d.generated_at} -> {mtime}，重解析估值")
            else:
                # 解析不出字段：保留原 valuation 并告警，仅校正 generated_at（文件确实更新过）
                print(f"{tag}{stock.ticker} {os.path.basename(d.local_path)}: "
                      f"generated_at {d.generated_at} -> {mtime}，"
                      f"但估值解析为空，保留原值", file=sys.stderr)
            if args.apply:
                d.generated_at = mtime
                if valuation:
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
