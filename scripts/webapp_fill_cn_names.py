#!/usr/bin/env python3
"""一次性脚本：从 scripts/sec_filings.py 的 CN_NAME_MAP 反推 ticker→中文名，
回填 webapp 数据库 stock.name_cn。

用法（仓库根目录运行）:
    python3 scripts/webapp_fill_cn_names.py            # 预览不写库
    python3 scripts/webapp_fill_cn_names.py --apply    # 实际写库
"""
import argparse
import importlib.util
import os
import re
import sqlite3
import sys

# 路径解析：以本文件位置为基准，不依赖运行目录
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC_FILINGS = os.path.join(REPO_ROOT, "scripts", "sec_filings.py")
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "stock_god.db")

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# CN_NAME_MAP 用点号风格（BRK.B），EDGAR/库内是连字符风格（BRK-B）
TICKER_ALIAS = {"BRK.B": "BRK-B"}


def load_cn_name_map():
    spec = importlib.util.spec_from_file_location("sec_filings_map", SEC_FILINGS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CN_NAME_MAP


def reverse_map(cn_map: dict) -> dict:
    """ticker → 首个含中文的名称（按映射文件中出现顺序取第一个）。"""
    rev = {}
    for name, ticker in cn_map.items():
        if CJK_RE.search(name):
            rev.setdefault(TICKER_ALIAS.get(ticker, ticker), name)
    return rev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写库（默认仅预览）")
    ap.add_argument("--db", default=os.environ.get("STOCKGOD_DB", DEFAULT_DB))
    args = ap.parse_args()

    rev = reverse_map(load_cn_name_map())
    print(f"CN_NAME_MAP 反查得到 {len(rev)} 个 ticker 的中文名")

    conn = sqlite3.connect(args.db)
    try:
        cur = conn.cursor()
        updated, missing = [], []
        for ticker, name in rev.items():
            cur.execute("SELECT id, name_cn FROM stock WHERE ticker = ?", (ticker,))
            row = cur.fetchone()
            if row is None:
                missing.append(ticker)
                continue
            if args.apply:
                cur.execute("UPDATE stock SET name_cn = ? WHERE id = ?", (name, row[0]))
            updated.append((ticker, name))
        if args.apply:
            conn.commit()
        print(f"{'已更新' if args.apply else '待更新'} {len(updated)} 行:")
        for t, n in updated:
            print(f"  {t:<8} {n}")
        if missing:
            print(f"库中无此 ticker（跳过 {len(missing)} 个）: {', '.join(missing)}")
        if not args.apply:
            print("（预览模式，加 --apply 写库）")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
