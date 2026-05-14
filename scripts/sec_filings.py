#!/usr/bin/env python3
"""
SEC EDGAR Filings - list & download
- list: 列出公司所有 SEC 财报链接
- download: 下载指定财报原始文件到本地
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

SEC_API_BASE = "https://data.sec.gov/submissions"
SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
HEADERS = {"User-Agent": "PersonalResearch/1.0 (personal@email.com)", "Accept": "application/json"}

# 常见财报表单类型说明
FORM_DESCRIPTIONS = {
    "10-K": "年度报告 (Annual Report)",
    "10-K/A": "年度报告修正 (Annual Report Amendment)",
    "10-Q": "季度报告 (Quarterly Report)",
    "10-Q/A": "季度报告修正 (Quarterly Report Amendment)",
    "8-K": "重大事件报告 (Current Report)",
    "8-K/A": "重大事件报告修正 (Current Report Amendment)",
    "20-F": "外国公司年报 (Foreign Private Issuer Annual Report)",
    "6-K": "外国公司中期报告 (Foreign Private Issuer Interim Report)",
    "DEF 14A": "委托投票声明 (Definitive Proxy Statement)",
    "S-1": "IPO 注册声明 (Registration Statement)",
    "SC 13G": "持股 5% 以上声明 (Beneficial Ownership)",
    "SC 13G/A": "持股 5% 以上声明修正",
    "SC 13D": "主动持股声明 (Activist Ownership)",
    "4": "内部人交易报告 (Insider Trading)",
    "SD": "冲突矿产报告 (Specialized Disclosure)",
    "ARS": "年度报告摘要 (Annual Report Summary)",
    "DEFA14A": "补充委托投票声明",
    "UPLOAD": "SEC 上传文件",
}


# ── 中文名/别名 → ticker 映射 ─────────────────────────
CN_NAME_MAP = {
    # 科技
    "英特尔": "INTC", "因特尔": "INTC", "Intel": "INTC",
    "苹果": "AAPL", "Apple": "AAPL",
    "微软": "MSFT", "Microsoft": "MSFT",
    "英伟达": "NVDA", "Nvidia": "NVDA", "NVIDIA": "NVDA",
    "谷歌": "GOOGL", "Google": "GOOGL", "Alphabet": "GOOGL",
    "亚马逊": "AMZN", "Amazon": "AMZN",
    "脸书": "META", "Facebook": "META", "Meta": "META",
    "特斯拉": "TSLA", "Tesla": "TSLA",
    "台积电": "TSM", "TSMC": "TSM",
    "高通": "QCOM", "Qualcomm": "QCOM",
    "博通": "AVGO", "Broadcom": "AVGO",
    "超微半导体": "AMD", "AMD": "AMD",
    "应用材料": "AMAT", "Applied Materials": "AMAT",
    "阿斯麦": "ASML", "ASML": "ASML",
    # 消费
    "耐克": "NKE", "Nike": "NKE",
    "星巴克": "SBUX", "Starbucks": "SBUX",
    "麦当劳": "MCD", "McDonald": "MCD",
    "可口可乐": "KO", "Coca-Cola": "KO",
    "百事可乐": "PEP", "Pepsi": "PEP",
    "宝洁": "PG", "Procter": "PG",
    "联合利华": "UL", "Unilever": "UL",
    # 金融
    "摩根大通": "JPM", "JPMorgan": "JPM",
    "高盛": "GS", "Goldman": "GS",
    "花旗": "C", "Citigroup": "C",
    "伯克希尔": "BRK.B", "Berkshire": "BRK.B",
    # 医疗
    "强生": "JNJ", "Johnson": "JNJ",
    "辉瑞": "PFE", "Pfizer": "PFE",
    "礼来": "LLY", "Eli Lilly": "LLY",
    "诺和诺德": "NVO", "Novo Nordisk": "NVO",
    # 能源
    "埃克森美孚": "XOM", "Exxon": "XOM",
    "雪佛龙": "CVX", "Chevron": "CVX",
    # 中概股
    "阿里巴巴": "BABA", "Alibaba": "BABA",
    "腾讯": "0700", "Tencent": "0700",
    "拼多多": "PDD", "Pinduoduo": "PDD",
    "京东": "JD", "JD.com": "JD",
    "百度": "BIDU", "Baidu": "BIDU",
    "网易": "NTES", "NetEase": "NTES",
    "蔚来": "NIO", "NIO": "NIO",
    "理想汽车": "LI", "Li Auto": "LI",
    "小鹏汽车": "XPEV", "XPeng": "XPEV",
    # ETF
    "标普500": "SPY", "S&P500": "SPY",
    "纳斯达克": "QQQ", "Nasdaq": "QQQ",
}


def normalize_ticker(symbol):
    # 1. 去市场前缀
    for prefix in ("US.", "HK.", "A."):
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):]
            break
    # 2. 中文名/别名映射
    if symbol in CN_NAME_MAP:
        return CN_NAME_MAP[symbol]
    # 3. 直接返回
    return symbol


def lookup_cik(ticker):
    ticker = ticker.upper()
    req = urllib.request.Request(SEC_COMPANY_TICKERS, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker:
                return str(entry["cik_str"]).zfill(10), entry.get("title", ticker)
    return None, None


def fetch_submissions(cik):
    url = f"{SEC_API_BASE}/CIK{cik}.json"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def build_filing_url(cik_short, accession_flat, filename):
    return f"{SEC_ARCHIVES}/{cik_short}/{accession_flat}/{filename}"


def build_index_url(cik_short, accession_flat):
    return f"{SEC_ARCHIVES}/{cik_short}/{accession_flat}/"


def cmd_list(symbol, form_filter=None, limit=50):
    ticker = normalize_ticker(symbol).upper()
    cik, name = lookup_cik(ticker)
    if not cik:
        print(f"ERROR: CIK not found for {ticker}")
        return False

    print(f"\n=== SEC EDGAR Filings: {name} ({ticker}) ===")
    print(f"CIK: {cik}\n")

    data = fetch_submissions(cik)
    recent = data["filings"]["recent"]

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    cik_short = str(int(cik))
    count = 0

    # 打印表头
    print(f"{'#':<4} {'日期':<12} {'表单类型':<14} {'说明':<30} {'文件链接'}")
    print("-" * 120)

    for i in range(len(forms)):
        form = forms[i]
        date = dates[i] if i < len(dates) else ""

        # 表单类型过滤
        if form_filter:
            filters = [f.strip().upper() for f in form_filter.split(",")]
            if not any(f in form.upper() for f in filters):
                continue

        acc = accessions[i] if i < len(accessions) else ""
        doc = primary_docs[i] if i < len(primary_docs) else ""
        desc = descriptions[i] if i < len(descriptions) else ""

        acc_flat = acc.replace("-", "")
        filing_url = build_filing_url(cik_short, acc_flat, doc)
        form_desc = FORM_DESCRIPTIONS.get(form, desc[:30] if desc else "")

        count += 1
        if count > limit:
            print(f"\n... 共 {len(forms)} 条记录，已显示前 {limit} 条。使用 --limit 调整。")
            break

        print(f"{count:<4} {date:<12} {form:<14} {form_desc:<30} {filing_url}")

    print(f"\n共 {min(count, limit)}/{len(forms)} 条记录")
    return True


def cmd_download(symbol, form_type=None, index=0, fiscal_year=None, force=False, years=5):
    """下载指定财报。默认下载近5年 10-K + 10-Q。"""
    import subprocess
    ticker = normalize_ticker(symbol).upper()
    cik, name = lookup_cik(ticker)
    if not cik:
        print(f"ERROR: CIK not found for {ticker}")
        return False

    print(f"\n=== Download SEC Filing: {name} ({ticker}) ===")
    print(f"Form: {form_type or '10-K + 10-Q'} | Years: {years}")

    data = fetch_submissions(cik)
    recent = data["filings"]["recent"]

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    # 确定目标表单类型
    if form_type:
        target_forms = [f.strip().upper() for f in form_type.split(",")]
    else:
        # 自动检测外国公司：检查是否有 20-F / 6-K 提交记录
        recent_forms = set(forms[:30])  # 看最近30条
        has_foreign = bool(recent_forms & {"20-F", "20-F/A", "6-K", "6-K/A"})
        if has_foreign:
            target_forms = ["20-F", "6-K"]
            print(f"  (检测到外国公司，自动使用 20-F + 6-K)")
        else:
            target_forms = ["10-K", "10-Q"]

    # 找到所有匹配的表单
    matches = [i for i in range(len(forms)) if forms[i] in target_forms]
    if not matches:
        print(f"ERROR: No {'/'.join(target_forms)} filings found for {ticker}")
        return False

    # 按年份分组
    if fiscal_year:
        year_range = [fiscal_year]
    else:
        current_year = datetime.now().year
        year_range = range(current_year - years + 1, current_year + 1)
    cik_short = str(int(cik))
    script = os.path.join(os.path.dirname(__file__), "download_filing.py")
    downloaded_count = 0
    skipped_count = 0

    for year in year_range:
        year_str = str(year)
        year_matches = [i for i in matches if dates[i][:4] == year_str]

        if not year_matches:
            print(f"\n--- {year}: 无匹配财报 ---")
            continue

        print(f"\n--- {year}: 找到 {len(year_matches)} 份财报 ---")

        for idx in year_matches:
            acc = accessions[idx]
            doc = primary_docs[idx]
            date = dates[idx]
            form = forms[idx]
            acc_flat = acc.replace("-", "")

            filing_url = build_filing_url(cik_short, acc_flat, doc)
            print(f"  [{form}] {date} - {doc}")
            print(f"    URL: {filing_url}")

            # 检查是否已下载
            filing_name = doc
            ticker_dir = os.path.join(REPORTS_DIR, "sec_filings", ticker)
            existing_path = os.path.join(ticker_dir, filing_name)
            if not force and os.path.exists(existing_path) and os.path.getsize(existing_path) > 0:
                print(f"    SKIP (已存在): {filing_name}")
                skipped_count += 1
                continue

            # 下载
            cmd = [sys.executable, script, filing_url, ticker]
            if force:
                cmd.append("--force")
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                downloaded_count += 1
                print(f"    ✅ 下载完成")
            else:
                err = result.stderr.decode(errors='ignore')[:100] if result.stderr else 'unknown error'
                print(f"    ❌ 下载失败: {err}")

    print(f"\n=== 完成: 下载 {downloaded_count} 份, 跳过 {skipped_count} 份 ===")
    return downloaded_count > 0 or skipped_count > 0


def download_images(html_path, cik_short, accession_flat):
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    img_pattern = re.compile(r'src="([a-z0-9_\-]+\.(?:jpg|png|gif|jpeg))"', re.IGNORECASE)
    images = set(img_pattern.findall(content))
    if not images:
        return
    img_dir = os.path.dirname(html_path)
    count = 0
    for img in images:
        img_path = os.path.join(img_dir, img)
        if os.path.exists(img_path):
            continue
        img_url = f"{SEC_ARCHIVES}/{cik_short}/{accession_flat}/{img}"
        try:
            req = urllib.request.Request(img_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(img_path, "wb") as f:
                    f.write(resp.read())
            count += 1
        except Exception:
            pass
    if count:
        print(f"  Downloaded {count} images")


def cmd_download_url(url, symbol=None, force=False):
    """直接按 SEC URL 下载完整 filing（主文档 + 附件 + CSS/JS + 图片）"""
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "download_filing.py")
    print(f"\n=== Downloading complete SEC filing ===")
    print(f"URL: {url}")

    # 确定 ticker
    ticker = None
    if symbol:
        ticker = normalize_ticker(symbol).upper()
    else:
        # 从 URL 提取 CIK，查询 ticker
        m = re.search(r'/edgar/data/(\d+)/', url)
        if m:
            cik = m.group(1)
            try:
                cik_url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
                req = urllib.request.Request(cik_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                ticker = data.get("tickers", [None])[0]
            except Exception:
                pass
    if not ticker:
        ticker = "UNKNOWN"
    print(f"Ticker: {ticker}")

    cmd = [sys.executable, script, url, ticker]
    if force:
        cmd.append("--force")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="SEC EDGAR Filings - list & download")
    sub = parser.add_subparsers(dest="command")

    # list 子命令
    p_list = sub.add_parser("list", help="列出公司所有 SEC 财报")
    p_list.add_argument("symbol", help="股票代码, e.g. US.NKE, NKE, HK.00700")
    p_list.add_argument("--form", type=str, default=None, help="过滤表单类型, e.g. 10-K,10-Q,8-K")
    p_list.add_argument("--limit", type=int, default=50, help="最大显示条数 (default: 50)")

    # download 子命令
    p_dl = sub.add_parser("download", help="下载指定财报")
    p_dl.add_argument("symbol", nargs="?", help="股票代码, e.g. US.NKE")
    p_dl.add_argument("--form", type=str, default=None, help="表单类型 (default: 最新任意类型, e.g. 10-K,10-Q)")
    p_dl.add_argument("--index", type=int, default=0, help="第几份, 0=最新 (default: 0)")
    p_dl.add_argument("--fy", type=int, help="指定财年, e.g. 2024")
    p_dl.add_argument("--years", type=int, default=5, help="下载近N年的财报 (default: 5)")
    p_dl.add_argument("--url", type=str, help="直接按 SEC URL 下载")
    p_dl.add_argument("--force", action="store_true", help="强制重新下载已存在的文件")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "download" and args.url:
        success = cmd_download_url(args.url, force=getattr(args, 'force', False))
    elif args.command == "list":
        sym = args.symbol.upper()
        if not any(sym.startswith(p) for p in ("US.", "HK.", "A.")):
            sym = "US." + sym
        success = cmd_list(sym, form_filter=args.form, limit=args.limit)
    elif args.command == "download":
        if args.url:
            success = cmd_download_url(args.url, symbol=args.symbol, force=getattr(args, 'force', False))
        elif args.symbol:
            sym = args.symbol.upper()
            if not any(sym.startswith(p) for p in ("US.", "HK.", "A.")):
                sym = "US." + sym
            success = cmd_download(sym, form_type=args.form, index=args.index, fiscal_year=args.fy, force=getattr(args, 'force', False), years=args.years)
        else:
            print("ERROR: 请提供股票代码或 --url")
            sys.exit(1)
    else:
        parser.print_help()
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
