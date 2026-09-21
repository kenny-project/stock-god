#!/usr/bin/env python3
"""一次性清理脚本：filing 表中与已知类型行同 (stock_id, period) 的
form_type='UNKNOWN' 重复行删除；剩余 UNKNOWN 行用 SEC submissions 回查修正。

背景：register_download 曾在申报期目录遍历时用目录名提取 form 类型，而目录名
（如 pfe-20260329）不含 form token → 落入 UNKNOWN，且查重键 (UNKNOWN, period)
拦不住 → 与同期主文档已知类型行成对重复。register.py 已根治（form 改从代表
文件名提取 + 已知期兜底查重），本脚本只修存量。

用法（仓库根目录运行）:
    python3 scripts/webapp_dedupe_filings.py             # 默认 dry-run 预览不写库
    python3 scripts/webapp_dedupe_filings.py --apply     # 实际写库
    python3 scripts/webapp_dedupe_filings.py --db data/stock_god.db

两步：
1. 删除与已知类型（非 UNKNOWN）行同 (stock_id, period) 的 UNKNOWN 行；
   --apply 时被删行备份到 /tmp/dedupe_filings_backup.json。
2. 剩余 UNKNOWN 行复用 services/form_fix.py 的 SEC submissions 回查逻辑
   修正 form_type（不重复实现）；仍修不出的保留并打印清单，绝不臆造。
幂等：重复运行第一步无重复可删、第二步已修行不再是 UNKNOWN。
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "webapp", "backend")
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "stock_god.db")
BACKUP_PATH = "/tmp/dedupe_filings_backup.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="实际写库（默认仅预览，不写库）")
    ap.add_argument("--db", default=os.environ.get("STOCKGOD_DB", DEFAULT_DB))
    args = ap.parse_args()

    sys.path.insert(0, BACKEND)
    os.environ["STOCKGOD_DB"] = args.db  # 必须在 import db 前设置
    from sqlalchemy import select
    from db import make_engine, make_session_factory
    from models import Filing, Stock
    from services.form_fix import fix_unknown_filings

    engine = make_engine(args.db)
    with make_session_factory(engine)() as session:
        # ---- 第一步：删除与已知类型行同 (stock_id, period) 的 UNKNOWN 行 ----
        unknown_rows = session.scalars(
            select(Filing).where(Filing.form_type == "UNKNOWN")).all()
        known_fp = {(f.stock_id, f.period) for f in session.scalars(select(Filing)).all()
                    if f.period and f.form_type != "UNKNOWN"}
        stocks = {s.id: s for s in session.scalars(select(Stock)).all()}
        tag = "[apply] " if args.apply else "[dry-run] "
        dupes = [f for f in unknown_rows if f.period and (f.stock_id, f.period) in known_fp]
        for f in dupes:
            ticker = stocks[f.stock_id].ticker if f.stock_id in stocks else f"stock_id={f.stock_id}"
            print(f"{tag}删除重复: {ticker} UNKNOWN {f.period} -> {f.local_path}")
        backup = [{"id": f.id, "ticker": stocks[f.stock_id].ticker if f.stock_id in stocks else None,
                   "form_type": f.form_type, "period": f.period, "local_path": f.local_path}
                  for f in dupes]
        if args.apply:
            for f in dupes:
                session.delete(f)
            session.flush()  # 让第二步的查询看到删除后的状态
            with open(BACKUP_PATH, "w", encoding="utf-8") as fp:
                json.dump(backup, fp, ensure_ascii=False, indent=2)

        # ---- 第二步：剩余 UNKNOWN 复用 form_fix 的 SEC submissions 回查 ----
        stats = fix_unknown_filings(session, dry_run=not args.apply)

    mode = "（dry-run 预览，未写库）" if not args.apply else ""
    print(f"\n第一步：UNKNOWN 总数 {len(unknown_rows)}，"
          f"{'已删除' if args.apply else '待删除'} {len(dupes)} 条重复行 "
          f"{'（备份: ' + BACKUP_PATH + '）' if args.apply else ''}")
    print(f"第二步：剩余 UNKNOWN {stats['total']} 条，{'已修正' if args.apply else '待修正'} "
          f"{stats['fixed']} 条 {mode}")
    for ticker, form, period in stats["skipped_conflict"]:
        print(f"  跳过(唯一约束冲突): {ticker} {form} {period}")
    for ticker, period, reason in stats["unmatched"]:
        print(f"  保留 UNKNOWN: {ticker} {period} ({reason})")
    for ticker in sorted(set(stats["fetch_failed"])):
        print(f"  拉取失败(整股票未动): {ticker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
