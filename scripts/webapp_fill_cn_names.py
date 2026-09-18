#!/usr/bin/env python3
"""一次性脚本：从 scripts/sec_filings.py 的 CN_NAME_MAP 反推 ticker→中文名，
回填 webapp 数据库 stock.name_cn；并把英文名（及未入选的中文变体）并入
stock.aliases（合并去重，不覆盖已有别名），供 GET /api/stocks 搜索命中。

依赖：stock.aliases 列已存在（先 `alembic upgrade head`）。

用法（仓库根目录运行）:
    python3 scripts/webapp_fill_cn_names.py            # 预览不写库
    python3 scripts/webapp_fill_cn_names.py --apply    # 实际写库
"""
import argparse
import importlib.util
import json
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


def collect_aliases(cn_map: dict) -> dict:
    """ticker → 需并入 aliases 的名称列表：全部英文键名 + 未入选 name_cn 的中文变体。

    跳过与 ticker 本身相同的名字（如 "AMD"→AMD）；大小写不敏感去重。
    """
    rev = reverse_map(cn_map)
    out = {}
    for name, raw_ticker in cn_map.items():
        ticker = TICKER_ALIAS.get(raw_ticker, raw_ticker)
        if CJK_RE.search(name) and rev.get(ticker) == name:
            continue  # 已作为 name_cn 保存，无需重复进别名
        if name.upper() == ticker.upper():
            continue
        out.setdefault(ticker, []).append(name)
    for ticker, names in out.items():  # 去重保序
        seen, uniq = set(), []
        for n in names:
            if n.casefold() not in seen:
                seen.add(n.casefold())
                uniq.append(n)
        out[ticker] = uniq
    return out


def merge_aliases(existing, extra: list) -> list:
    """已存别名在前、新别名在后，大小写不敏感去重，不覆盖已有。"""
    merged, seen = [], set()
    for a in list(existing or []) + extra:
        key = a.casefold()
        if key not in seen:
            seen.add(key)
            merged.append(a)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写库（默认仅预览）")
    ap.add_argument("--db", default=os.environ.get("STOCKGOD_DB", DEFAULT_DB))
    args = ap.parse_args()

    cn_map = load_cn_name_map()
    rev = reverse_map(cn_map)
    print(f"CN_NAME_MAP 反查得到 {len(rev)} 个 ticker 的中文名")
    alias_map = collect_aliases(cn_map)
    print(f"CN_NAME_MAP 得到 {len(alias_map)} 个 ticker 的候选别名")

    conn = sqlite3.connect(args.db)
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT aliases FROM stock LIMIT 1")
        except sqlite3.OperationalError as e:
            sys.exit(f"stock 表缺 aliases 列，请先执行 alembic upgrade head：{e}")

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
        print(f"{'已更新' if args.apply else '待更新'} name_cn {len(updated)} 行:")
        for t, n in updated:
            print(f"  {t:<8} {n}")

        alias_updated, alias_missing = [], []
        for ticker, extra in alias_map.items():
            cur.execute("SELECT id, aliases FROM stock WHERE ticker = ?", (ticker,))
            row = cur.fetchone()
            if row is None:
                alias_missing.append(ticker)
                continue
            try:
                existing = json.loads(row[1]) if row[1] else []
            except (json.JSONDecodeError, TypeError):
                existing = []
            merged = merge_aliases(existing, extra)
            if merged == (existing or []):
                continue  # 无新增，不动
            if args.apply:
                cur.execute("UPDATE stock SET aliases = ? WHERE id = ?",
                            (json.dumps(merged, ensure_ascii=False), row[0]))
            alias_updated.append((ticker, merged))
        if args.apply:
            conn.commit()
        print(f"{'已更新' if args.apply else '待更新'} aliases {len(alias_updated)} 行:")
        for t, a in alias_updated:
            print(f"  {t:<8} {a}")
        if missing or alias_missing:
            print(f"库中无此 ticker（跳过 {len(set(missing) | set(alias_missing))} 个）: "
                  f"{', '.join(sorted(set(missing) | set(alias_missing)))}")
        if not args.apply:
            print("（预览模式，加 --apply 写库）")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
