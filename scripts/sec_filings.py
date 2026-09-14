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
import urllib.error
import urllib.request
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

SEC_API_BASE = "https://data.sec.gov/submissions"
SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
HEADERS = {"User-Agent": "PersonalResearch/1.0 (personal@email.com)", "Accept": "application/json"}

# 年报/季报表单类型（财年分组与完整性校验用）
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

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
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"ERROR: 访问 SEC EDGAR 超时或网络异常: {e}")
        return None, None
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker:
            return str(entry["cik_str"]).zfill(10), entry.get("title", ticker)
    return None, None


def fetch_submissions(cik):
    url = f"{SEC_API_BASE}/CIK{cik}.json"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"ERROR: 获取 SEC 提交记录超时或网络异常: {e}")
        return None


def merge_submissions_pages(data, pages):
    """合并 submissions 主响应的 recent 与更早的分页记录，返回合并后的扁平记录。

    recent 最多 1000 条；申报频繁的公司（如 GOOGL，内部人 Form 4 很多）的
    更早记录存放在 data["filings"]["files"] 分页中，忽略会导致目标年份
    "无匹配财报"。分页记录更早，追加在 recent 之后保持最新在前。"""
    recent = data["filings"]["recent"]
    for page in pages:
        for key, values in page.items():
            if key in recent and isinstance(recent[key], list) and isinstance(values, list):
                recent[key].extend(values)
    return recent


def _fetch_all_submissions(cik, max_pages=5):
    """获取 submissions 全部记录：主响应 + 按需加载更早的分页（覆盖 5 年窗口）"""
    data = fetch_submissions(cik)
    if not data:
        return None
    files = data.get("filings", {}).get("files", []) or []
    pages = []
    for f in files[:max_pages]:
        url = f"{SEC_API_BASE}/{f['name']}"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                pages.append(json.loads(resp.read()))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"WARNING: 获取提交记录分页失败({f['name']}): {e}")
            break
    return merge_submissions_pages(data, pages)


def group_filings_by_fiscal_year(forms, filing_dates, report_dates, years=5, include_forms=None):
    """按财年分组财报，返回 {财年: [记录索引]}（索引按最新在前排序）

    - 锚定最新一份年报的 reportDate 财年，向前推 years-1 年（如最新 10-K 为
      FY2026，则窗口为 FY2022..FY2026），保证每个财年恰有 1 份年报 + 3 份季报
    - 财年归属由 reportDate（报告期截止日）+ 财年截止月决定，与申报日无关：
      财年 Q1 常在上一日历年申报（如 MSFT FY2022 Q1 于 2021-10 申报），
      按申报年分组会把它划出窗口，还会把上一年度的 10-K 划入窗口
    - 仅统计 include_forms（默认年报+季报），Form 4/8-K/6-K 等噪音不参与
    """
    if include_forms is None:
        include_forms = ANNUAL_FORMS | QUARTERLY_FORMS
    else:
        include_forms = set(include_forms)

    annual_idx = [i for i, f in enumerate(forms)
                  if f in ANNUAL_FORMS and i < len(report_dates) and report_dates[i]]
    if annual_idx:
        latest = max(annual_idx, key=lambda i: report_dates[i])
    else:
        # 无年报（新上市公司只有季报，如刚 IPO 的 SPCX）：锚定最新一份季报财年
        quarterly_idx = [i for i, f in enumerate(forms)
                         if f in QUARTERLY_FORMS and i < len(report_dates) and report_dates[i]]
        if not quarterly_idx:
            return {}
        latest = max(quarterly_idx, key=lambda i: report_dates[i])
    fy_end_month = int(report_dates[latest][5:7])
    latest_fy = int(report_dates[latest][:4])
    target_years = set(range(latest_fy - years + 1, latest_fy + 1))

    def _fy(report_date):
        y, m = int(report_date[:4]), int(report_date[5:7])
        return y if m <= fy_end_month else y + 1

    groups = {}
    for i, form in enumerate(forms):
        if form not in include_forms or i >= len(report_dates) or not report_dates[i]:
            continue
        fy = _fy(report_dates[i])
        if fy in target_years:
            groups.setdefault(fy, []).append(i)
    return groups


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
    if not data:
        return False
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


def translate_html_to_chinese(html_path, sec_url=None):
    """将 HTML 翻译成中文，保存为 {name}_zh-cn.html

    批量收集文本 → 一次性翻译 → 替换回去，保留 HTML 结构。
    """
    import re

    name_stem = os.path.splitext(html_path)[0]
    zh_path = f"{name_stem}_zh-cn.html"

    if os.path.exists(zh_path) and os.path.getsize(zh_path) > 1000:
        print(f"  中文版已存在: {os.path.basename(zh_path)}")
        return zh_path

    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print(f"  ❌ 翻译失败: 需要安装 deep_translator (pip install deep-translator)")
        return None

    print(f"  翻译中... (deep_translator)")

    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        html_content = f.read()

    translator = GoogleTranslator(source='en', target='zh-CN')

    # 保护不应该翻译的区域
    protected = []
    def protect_match(m):
        protected.append(m.group(0))
        return f'__PROTECTED_{len(protected)-1}__'
    def protect_str(s):
        protected.append(s)
        return f'__PROTECTED_{len(protected)-1}__'

    # 1. 保护 XML 声明
    html_content = re.sub(r'<\?xml[^?]*\?>', protect_match, html_content)
    # 2. 保护 HTML 注释
    html_content = re.sub(r'<!--.*?-->', protect_match, html_content)
    # 3. 保护 script/style/code/pre 标签内容
    html_content = re.sub(r'<(script|style|code|pre)[^>]*>.*?</\1>', protect_match, html_content, flags=re.DOTALL | re.IGNORECASE)
    # 4. 保护 meta/title 标签（不翻译）
    html_content = re.sub(r'<(meta|title)[^>]*>.*?</\1>', protect_match, html_content, flags=re.DOTALL | re.IGNORECASE)
    # 5. 保护 XBRL 标签及其内容（ix:, xbrli:, link:, xlink: 等）
    html_content = re.sub(r'<(ix:[^ >]+|xbrli:[^ >]+|link:[^ >]+|xlink:[^ >]+)[^>]*>.*?</\1>', protect_match, html_content, flags=re.DOTALL | re.IGNORECASE)
    # 6. 保护自闭合的 XBRL 标签
    html_content = re.sub(r'<(ix:[^ >]+|xbrli:[^ >]+|link:[^ >]+|xlink:[^ >]+)[^>]*/>', protect_match, html_content, flags=re.IGNORECASE)
    # 7. 保护标签属性中的值
    html_content = re.sub(r'(\w+)="([^"]*)"', lambda m: m.group(1) + '="' + protect_str(m.group(2)) + '"', html_content)

    # 收集所有需要翻译的文本片段（> 和 < 之间的内容）
    # 只翻译 body 中的可见文本，跳过占位符
    text_segments = []
    for m in re.finditer(r'>([^<]+)<', html_content):
        text = m.group(1)
        if text.strip() and re.search(r'[a-zA-Z]{3,}', text):
            # 跳过包含占位符的片段
            if '__PROTECTED_' not in text:
                text_segments.append(text)

    print(f"  找到 {len(text_segments)} 个文本片段需要翻译")

    # 批量翻译（合并成大块，减少 API 调用）
    # 使用不会被翻译的特殊字符作为分隔符
    SEP = "\n§§§\n"
    MAX_CHUNK = 4500
    translations = {}

    current_batch = []
    current_size = 0
    batch_texts = []

    for i, text in enumerate(text_segments):
        if current_size + len(text) + len(SEP) > MAX_CHUNK and current_batch:
            merged = SEP.join(current_batch)
            try:
                translated = translator.translate(merged)
                parts = translated.split("§§§")
                for orig, trans in zip(batch_texts, parts):
                    translations[orig] = trans.strip()
            except Exception:
                pass
            current_batch = []
            batch_texts = []
            current_size = 0

        current_batch.append(text)
        batch_texts.append(text)
        current_size += len(text) + len(SEP)

    if current_batch:
        merged = SEP.join(current_batch)
        try:
            translated = translator.translate(merged)
            parts = translated.split("§§§")
            for orig, trans in zip(batch_texts, parts):
                translations[orig] = trans.strip()
        except Exception:
            pass

    print(f"  翻译完成，替换文本...")

    def replace_text(m):
        text = m.group(1)
        return '>' + translations.get(text, text) + '<'

    html_content = re.sub(r'>([^<]+)<', replace_text, html_content)

    # 恢复 protected 区域
    for i, orig in enumerate(protected):
        html_content = html_content.replace(f'__PROTECTED_{i}__', orig)

    # 修复编码声明：将 ASCII 改为 UTF-8
    html_content = re.sub(r"encoding='ASCII'", "encoding='UTF-8'", html_content)
    html_content = re.sub(r'encoding="ASCII"', 'encoding="UTF-8"', html_content)

    with open(zh_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    size = os.path.getsize(zh_path)
    print(f"  ✅ 翻译完成: {os.path.basename(zh_path)} ({size/1024:.1f} KB)")
    return zh_path


def cmd_download(symbol, form_type=None, index=0, fiscal_year=None, force=False, years=5, translate=False):
    """下载指定财报。默认下载近5年 10-K + 10-Q。"""
    import subprocess
    ticker = normalize_ticker(symbol).upper()
    cik, name = lookup_cik(ticker)
    if not cik:
        print(f"ERROR: CIK not found for {ticker}")
        return False

    print(f"\n=== Download SEC Filing: {name} ({ticker}) ===")
    print(f"Form: {form_type or '10-K + 10-Q'} | Years: {years}")

    recent = _fetch_all_submissions(cik)
    if not recent:
        return False

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
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

    cik_short = str(int(cik))
    script = os.path.join(os.path.dirname(__file__), "download_filing.py")
    downloaded_count = 0
    skipped_count = 0

    # 按财年分组（锚定最新年报财年，保证每财年 1 份年报 + 3 份季报）
    groups = group_filings_by_fiscal_year(forms, dates, report_dates, years=years,
                                          include_forms=target_forms)
    if fiscal_year:
        groups = {fy: idx for fy, idx in groups.items() if fy == fiscal_year}
    if not groups:
        print(f"ERROR: 近 {years} 年（财年）未找到 {'/'.join(target_forms)} 财报")
        return False

    for year in sorted(groups):
        year_matches = groups[year]

        print(f"\n--- FY{year}: 找到 {len(year_matches)} 份财报 ---")

        for idx in year_matches:
            acc = accessions[idx]
            doc = primary_docs[idx]
            date = dates[idx]
            form = forms[idx]
            acc_flat = acc.replace("-", "")

            filing_url = build_filing_url(cik_short, acc_flat, doc)
            ticker_dir = os.path.join(REPORTS_DIR, "sec_filings", ticker)

            # 对于6-K，只下载附件中的季度报告，不下载封面页
            if form in ("6-K", "6-K/A"):
                _download_6k_quarterly_report(filing_url, ticker_dir, date, force)
                downloaded_count += 1
                continue

            print(f"  [{form}] {date} - {doc}")
            print(f"    URL: {filing_url}")

            # 检查是否已下载
            filing_name = doc
            existing_path = os.path.join(ticker_dir, filing_name)
            if not force and os.path.exists(existing_path) and os.path.getsize(existing_path) > 0:
                print(f"    SKIP (已存在): {filing_name}")
                # 即使跳过下载，如果指定了 --zh 且中文版不存在，也要翻译
                if translate:
                    zh_name = f"{os.path.splitext(filing_name)[0]}_zh-cn.html"
                    zh_path = os.path.join(ticker_dir, zh_name)
                    if not os.path.exists(zh_path) or os.path.getsize(zh_path) < 1000:
                        translate_html_to_chinese(existing_path, sec_url=filing_url)
                skipped_count += 1
                continue

            # 下载主文档（10-K/10-Q/20-F）
            cmd = [sys.executable, script, filing_url, ticker]
            if force:
                cmd.append("--force")
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                downloaded_count += 1
                print(f"    ✅ 下载完成")
                # 翻译成中文
                if translate:
                    html_path = os.path.join(ticker_dir, doc)
                    if os.path.exists(html_path):
                        translate_html_to_chinese(html_path, sec_url=filing_url)
            else:
                err = result.stderr.decode(errors='ignore')[:100] if result.stderr else 'unknown error'
                print(f"    ❌ 下载失败: {err}")

    print(f"\n=== 完成: 下载 {downloaded_count} 份, 跳过 {skipped_count} 份 ===")
    return downloaded_count > 0 or skipped_count > 0


def _download_6k_quarterly_report(filing_url, ticker_dir, date, force=False):
    """从6-K中提取并保存季度报告（只保存附件，不保存封面页）"""
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "download_filing.py")

    # 从主文档URL获取附件目录
    base_dir = filing_url.rsplit('/', 1)[0] + '/'

    # 下载主文档内容，解析附件链接
    try:
        req = urllib.request.Request(filing_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"    ⚠️ 无法解析6-K主文档: {e}")
        return

    # 提取所有附件链接和描述
    exhibit_pattern = re.compile(
        r'href="([^"]*_ex\d+[-_]\d+[^"]*\.htm[l]?)"[^>]*>([^<]*)',
        re.IGNORECASE
    )
    exhibits = exhibit_pattern.findall(content)

    if not exhibits:
        simple_pattern = re.compile(r'href="([^"]*_ex\d+[-_]\d+[^"]*\.htm[l]?)"', re.IGNORECASE)
        exhibits = [(m, '') for m in simple_pattern.findall(content)]

    for exhibit, description in exhibits:
        exhibit_lower = exhibit.lower()
        desc_lower = description.lower()

        # 只处理 exhibit 99-1
        if not any(p in exhibit_lower for p in ['ex99-1', 'ex99_1', 'ex99.1']):
            continue

        exhibit_url = base_dir + exhibit
        ticker = os.path.basename(ticker_dir)
        output_name = f"{ticker}_6K_{date}.htm"
        output_path = os.path.join(ticker_dir, output_name)

        if not force and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"  [6-K] {date} - SKIP (已存在): {output_name}")
            return

        # 先下载附件，检查内容是否是季度报告
        try:
            req = urllib.request.Request(exhibit_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                file_content = resp.read()

            # 检查文件大小，太小的跳过（季度报告通常>50KB）
            if len(file_content) < 50000:
                print(f"  [6-K] {date} - 跳过小文件 ({len(file_content)/1024:.1f}KB): {exhibit}")
                return

            # 解析内容，只检查前2000字符的标题部分
            text_head = file_content[:10000].decode('utf-8', errors='ignore').lower()

            # 季度报告标题关键词（在标题中出现）
            quarterly_title_keywords = ['quarter', 'result', 'earnings', 'financial', 'revenue']

            # 检查标题是否包含季度报告关键词
            is_quarterly = any(kw in text_head for kw in quarterly_title_keywords)

            if not is_quarterly:
                print(f"  [6-K] {date} - 跳过非季度报告: {exhibit}")
                return

            # 是季度报告，保存文件
            os.makedirs(ticker_dir, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(file_content)
            print(f"  [6-K] {date} - ✅ 下载完成: {output_name}")

        except Exception as e:
            print(f"  [6-K] {date} - ❌ 下载失败: {e}")
        return


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
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"WARNING: 查询 CIK 信息超时: {e}")
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
    p_dl.add_argument("--fy", "--year", type=int, help="指定财年, e.g. 2024")
    p_dl.add_argument("--years", type=int, default=5, help="下载近N年的财报 (default: 5)")
    p_dl.add_argument("--url", type=str, help="直接按 SEC URL 下载")
    p_dl.add_argument("--force", action="store_true", help="强制重新下载已存在的文件")
    p_dl.add_argument("--zh", action="store_true", help="下载后翻译成中文 HTML (Google Translate)")

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
            success = cmd_download(sym, form_type=args.form, index=args.index, fiscal_year=args.fy, force=getattr(args, 'force', False), years=args.years, translate=getattr(args, 'zh', False))
        else:
            print("ERROR: 请提供股票代码或 --url")
            sys.exit(1)
    else:
        parser.print_help()
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
