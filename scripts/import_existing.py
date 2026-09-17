#!/usr/bin/env python3
"""把 reports/ 下已有产物一次性导入数据库。复用 webapp 后端的登记逻辑，坏数据跳过并输出告警清单。"""
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp", "backend"))
sys.path.insert(0, BACKEND)

from db import make_engine, make_session_factory, Base  # noqa: E402
import models  # noqa: E402, F401
from models import Stock  # noqa: E402
from services.register import register_download, register_analysis, register_dcf  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main():
    engine = make_engine()
    Base.metadata.create_all(engine)
    Factory = make_session_factory(engine)
    reports_root = os.path.abspath(os.path.join(BACKEND, "..", "..", "reports", "sec_filings"))
    if not os.path.isdir(reports_root):
        print(f"错误：未找到财报目录 {reports_root}，请先运行 sec_filings 下载。", file=sys.stderr)
        sys.exit(1)
    tickers = sorted(d for d in os.listdir(reports_root)
                     if os.path.isdir(os.path.join(reports_root, d)))
    warnings = []
    with Factory() as s:
        for t in tickers:
            st = s.scalar(select(Stock).where(Stock.ticker == t))
            if not st:
                st = Stock(ticker=t, market="US", name_en=t)
                s.add(st)
                s.commit()
            try:
                n1 = register_download(s, st)
                n2 = register_analysis(s, st)
                n3 = register_dcf(s, st)
                print(f"{t}: filings+{n1} analyses+{n2} dcf+{n3}")
            except Exception as e:
                s.rollback()
                warnings.append(f"{t}: {e}")
    if warnings:
        print("\n=== 告警（已跳过） ===")
        for w in warnings:
            print("  -", w)


if __name__ == "__main__":
    main()
