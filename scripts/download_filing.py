#!/usr/bin/env python3
"""
下载 SEC filing 的所有关联文件（主文档 + 附件 + CSS/JS + 图片）
确保本地打开 HTML 时正常显示

目录结构:
  ticker_dir/
  ├── filing.htm              ← 主文档（最外层）
  ├── filing_report.htm       ← 子目录中实际报告的副本（如有）
  └── filing/                 ← 附件子目录
      ├── filing.xsd
      ├── exhibit1.htm
      ├── image1.jpg
      └── ...
"""
import os
import re
import sys
import urllib.request

HEADERS = {"User-Agent": "PersonalResearch/1.0 (personal@email.com)"}
SEC_BASE = "https://www.sec.gov"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")


def download_file(url, out_path, force=False, retries=3):
    """下载单个文件。force=False 时跳过已存在的文件。失败时自动重试。"""
    import time
    if not force and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(f"  SKIP (已存在): {os.path.basename(out_path)}")
        return -1  # 表示跳过
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = resp.read()
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(content)
            return len(content)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  FAIL ({retries}次重试后): {os.path.basename(url)} ({e})")
            return 0


def find_all_references(html_content):
    """从 HTML 中提取所有本地引用（href, src, url()）"""
    refs = set()
    # href="..." 和 src="..."
    for m in re.finditer(r'(?:href|src)=["\']([^"\'#]+)["\']', html_content):
        ref = m.group(1)
        if not ref.startswith(('http://', 'https://', 'javascript:', 'mailto:')):
            refs.add(ref)
    # url('...')
    for m in re.finditer(r'url\(["\']?([^"\')]+)["\']?\)', html_content):
        ref = m.group(1)
        if not ref.startswith(('http://', 'https://')):
            refs.add(ref)
    return refs


def download_filing(base_url, ticker_dir, force=False):
    """
    下载 SEC filing 的所有关联文件
    base_url: 如 https://www.sec.gov/Archives/edgar/data/804328/000080432826000061/qcom-20260329.htm
    ticker_dir: 如 reports/sec_filings/QCOM/
    force: 是否强制重新下载已存在的文件
    
    目录结构:
      ticker_dir/filing.htm      ← 主文档
      ticker_dir/filing/         ← 附件子目录
    """
    filing_dir_url = base_url.rsplit('/', 1)[0] + '/'
    filing_name = base_url.rsplit('/', 1)[1]
    filing_stem = os.path.splitext(filing_name)[0]
    
    # 主文档放最外层，附件放子目录
    sub_dir = os.path.join(ticker_dir, filing_stem)
    os.makedirs(ticker_dir, exist_ok=True)
    
    print(f"\n=== Downloading complete SEC filing ===")
    print(f"URL: {base_url}")
    print(f"Output: {ticker_dir}\n")
    
    # 1. 下载主文档 → 放最外层
    print(f"[1] Main document: {filing_name}")
    main_path = os.path.join(ticker_dir, filing_name)
    size = download_file(base_url, main_path, force=force)
    if size == -1:
        print(f"  -> {main_path} (跳过，已存在)")
        print(f"\n=== Done (文件已存在，跳过) ===")
        print(f"Open {main_path} in browser to view.")
        return True
    if size == 0:
        print(f"\n=== FAILED: 下载主文档失败，可能是网络超时 ===")
        print(f"URL: {base_url}")
        print(f"提示: 可尝试手动下载或稍后重试")
        return False
    print(f"  -> {main_path} ({size/1024:.1f} KB)")
    
    # 2. 读取主文档，提取所有引用
    with open(main_path, "r", encoding="utf-8", errors="ignore") as f:
        main_html = f.read()
    
    refs = find_all_references(main_html)
    print(f"\n[2] Found {len(refs)} local references")
    
    # 3. 下载所有引用的文件 → 放子目录
    downloaded = {filing_name}
    for ref in sorted(refs):
        if ref in downloaded:
            continue
        
        if ref.startswith('/'):
            full_url = SEC_BASE + ref
        else:
            full_url = filing_dir_url + ref
        
        out_path = os.path.join(sub_dir, ref)
        
        size = download_file(full_url, out_path, force=force)
        if size > 0:
            downloaded.add(ref)
            print(f"  OK: {ref} ({size/1024:.1f} KB)")
            
            ext = os.path.splitext(ref)[1].lower()
            if ext in ('.htm', '.html', '.css', '.js'):
                try:
                    with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    sub_refs = find_all_references(content)
                    for sub_ref in sub_refs:
                        if sub_ref not in downloaded and not sub_ref.startswith(('http://', 'https://')):
                            refs.add(sub_ref)
                except:
                    pass
    
    # 4. 扫描子目录中的 HTML，下载图片
    print(f"\n[3] Scanning for images...")
    image_count = 0
    if os.path.isdir(sub_dir):
        for fname in os.listdir(sub_dir):
            if fname.endswith(('.htm', '.html')):
                fpath = os.path.join(sub_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    for m in re.finditer(r'(?:src|href)=["\']([^"\']*\.(jpg|jpeg|png|gif|svg|webp))["\']', content, re.I):
                        img_ref = m.group(1)
                        if img_ref in downloaded:
                            continue
                        if img_ref.startswith(('http://', 'https://')):
                            img_url = img_ref
                        elif img_ref.startswith('/'):
                            img_url = SEC_BASE + img_ref
                        else:
                            img_url = filing_dir_url + img_ref
                        img_path = os.path.join(sub_dir, os.path.basename(img_ref))
                        size = download_file(img_url, img_path, force=force)
                        if size > 0:
                            downloaded.add(img_ref)
                            image_count += 1
                            print(f"  IMG: {os.path.basename(img_ref)} ({size/1024:.1f} KB)")
                except:
                    pass
    
    print(f"\n[4] Downloaded {image_count} images")

    # 5. 如果主文档是索引页（<500KB），复制子目录中的实际报告到外层目录
    #    复制时重写相对路径，加上子目录前缀，确保图片等资源能正确加载
    copied_reports = []
    main_size = os.path.getsize(main_path) if os.path.exists(main_path) else 0
    if main_size < 500 * 1024 and os.path.isdir(sub_dir):
        htm_files = [f for f in os.listdir(sub_dir)
                     if f.endswith(('.htm', '.html')) and f != filing_name]
        if htm_files:
            # 优先选匹配 filing_stem 的文件（如 nvo-20251231_d2.htm）
            best = None
            for f in htm_files:
                if f.startswith(filing_stem):
                    best = f
                    break
            # 没有匹配的，选最大的
            if not best:
                best = max(htm_files, key=lambda f: os.path.getsize(os.path.join(sub_dir, f)))

            src = os.path.join(sub_dir, best)
            dst_name = f"{filing_stem}_report{os.path.splitext(best)[1]}"
            dst = os.path.join(ticker_dir, dst_name)
            if not os.path.exists(dst):
                try:
                    with open(src, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    # 重写相对路径：加上子目录前缀
                    def _rewrite_href(m):
                        attr = m.group(1)   # href 或 src
                        eq = m.group(2)     # = 或 ==
                        quote = m.group(3)  # ' 或 "
                        ref = m.group(4)
                        if ref.startswith(('http://', 'https://', 'javascript:', 'mailto:', '/')):
                            return m.group(0)
                        if ref.startswith(filing_stem + '/'):
                            return m.group(0)
                        return f'{attr}{eq}{quote}{filing_stem}/{ref}{quote}'

                    def _rewrite_url(m):
                        prefix = m.group(1)  # url(
                        quote = m.group(2)   # 可选引号
                        ref = m.group(3)
                        suffix = m.group(4)  # 可选引号 + )
                        if ref.startswith(('http://', 'https://', '/')):
                            return m.group(0)
                        if ref.startswith(filing_stem + '/'):
                            return m.group(0)
                        return f'{prefix}{quote}{filing_stem}/{ref}{suffix}'

                    content = re.sub(
                        r'((?:href|src))(=+)([\'"])([^\'"#]+)([\'"])',
                        _rewrite_href, content)
                    content = re.sub(
                        r'(url\()(["\']?)([^"\')]+)(["\']?\))',
                        _rewrite_url, content)
                    with open(dst, "w", encoding="utf-8") as f:
                        f.write(content)
                    copied_reports.append(dst_name)
                    print(f"  Copied: {best} → {dst_name} (paths rewritten)")
                except Exception as e:
                    print(f"  WARN: failed to copy {best}: {e}")

    # 6. 列出最终文件
    print(f"\n=== Final structure ===")
    print(f"  {filing_name} (main document)")
    if os.path.isdir(sub_dir):
        for f in sorted(os.listdir(sub_dir)):
            fpath = os.path.join(sub_dir, f)
            size = os.path.getsize(fpath)
            print(f"  {filing_stem}/{f} ({size/1024:.1f} KB)")
    for r in copied_reports:
        print(f"  {r} (copied report)")

    print(f"\n=== Done ===")
    if copied_reports:
        print(f"Open {copied_reports[0]} in browser to view the full report.")
    else:
        print(f"Open {filing_name} in browser to view.")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 download_filing.py <SEC_URL> [TICKER]")
        print("Example: python3 download_filing.py https://www.sec.gov/Archives/edgar/data/804328/000080432826000061/qcom-20260329.htm QCOM")
        sys.exit(1)
    
    url = sys.argv[1]
    ticker = sys.argv[2] if len(sys.argv) > 2 else "UNKNOWN"
    force = len(sys.argv) > 3 and sys.argv[3] == "--force"
    ticker_dir = os.path.join(REPORTS_DIR, "sec_filings", ticker)
    
    download_filing(url, ticker_dir, force=force)
