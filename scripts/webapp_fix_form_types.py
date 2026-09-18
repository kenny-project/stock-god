#!/usr/bin/env python3
"""一次性修复脚本：webapp 库中 form_type='UNKNOWN' 的存量财报，用 SEC submissions API
按 reportDate 回填 10-K/10-Q/20-F/6-K。

背景：EDGAR 主文档文件名（如 qcom-20201227.htm）不含 form token，
register._form_of 匹配不到，125 条存量记录落入 UNKNOWN。
核心逻辑在 webapp/backend/services/form_fix.py（含匹配规则说明与单测）。

用法（仓库根目录运行）:
    python3 scripts/webapp_fix_form_types.py             # 默认 dry-run 预览不写库
    python3 scripts/webapp_fix_form_types.py --apply     # 实际写库
    python3 scripts/webapp_fix_form_types.py --db data/stock_god.db
幂等：修过的行不再是 UNKNOWN，重复运行无副作用；映射不到的保持 UNKNOWN 并列出，不臆造。
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
    os.environ["STOCKGOD_DB"] = args.db  # 必须在 import db 前设置
    from db import make_engine, make_session_factory
    from services.form_fix import fix_unknown_filings

    engine = make_engine(args.db)
    with make_session_factory(engine)() as session:
        stats = fix_unknown_filings(session, dry_run=not args.apply)

    mode = "（dry-run 预览，未写库）" if not args.apply else ""
    print(f"UNKNOWN 总数 {stats['total']}，{'已修复' if args.apply else '待修复'} "
          f"{stats['fixed']} 条 {mode}")
    for ticker, form, period in stats["skipped_conflict"]:
        print(f"  跳过(唯一约束冲突): {ticker} {form} {period}")
    for ticker, period, reason in stats["unmatched"]:
        print(f"  保持 UNKNOWN: {ticker} {period} ({reason})")
    for ticker in sorted(set(stats["fetch_failed"])):
        print(f"  拉取失败(整股票未动): {ticker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
