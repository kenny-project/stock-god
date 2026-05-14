#!/usr/bin/env python3
"""
SEC EDGAR 10-K PDF Generator
- CIK 查询: SEC API (data.sec.gov)
- HTML 下载: Chrome CDP（绕过 SEC 反爬虫限制）
- PDF 生成: Chrome CDP → 本地 HTTP server → 打印 PDF
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import socket
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
REPORTS_TMP = os.path.join(REPORTS_DIR, "tmp")
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(REPORTS_TMP, exist_ok=True)

SEC_API_BASE = "https://data.sec.gov/submissions"
SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
HEADERS = {"User-Agent": "PersonalResearch/1.0 (personal@email.com)", "Accept": "application/json"}
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def normalize_ticker(symbol):
    for prefix in ("US.", "HK.", "A."):
        if symbol.startswith(prefix):
            return symbol[len(prefix):]
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

def get_latest_10k_info(cik):
    url = f"{SEC_API_BASE}/CIK{cik}.json"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
        recent = data["filings"]["recent"]
        for i, form in enumerate(recent.get("form", [])):
            if form == "10-K":
                acc = recent["accessionNumber"][i]
                return acc.replace("-", ""), recent["primaryDocument"][i], recent["filingDate"][i]
    return None, None, None

def download_html_via_chrome(url, output_path, wait_seconds=15):
    """Use Chrome CDP to navigate to URL and save rendered HTML."""
    tmp_dir = f"/tmp/chrome-download-{os.getuid()}"
    os.makedirs(tmp_dir, exist_ok=True)
    debug_port = get_free_port()
    user_data_dir = f"{tmp_dir}/chrome-userdata"
    os.makedirs(user_data_dir, exist_ok=True)

    subprocess.run(f"lsof -ti:{debug_port} | xargs kill -9 2>/dev/null || true",
                   shell=True, capture_output=True)

    chrome_proc = subprocess.Popen([
        CHROME,
        f"--remote-debugging-port={debug_port}",
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        f"--user-data-dir={user_data_dir}",
        "--disable-dev-shm-usage",
        "--disable-extensions",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # Wait for Chrome ready
        for _ in range(20):
            try:
                r = subprocess.run(
                    ["curl", "-s", f"http://127.0.0.1:{debug_port}/json/version"],
                    capture_output=True, timeout=2, check=True
                )
                if r.stdout:
                    break
            except Exception:
                pass
            subprocess.run(["sleep", "0.5"], capture_output=True)
        else:
            print("  Chrome failed to start")
            return False

        # Create new tab
        r = subprocess.run(
            ["curl", "-s", "-X", "PUT", f"http://127.0.0.1:{debug_port}/json/new"],
            capture_output=True, timeout=5, check=True
        )
        new_tab = json.loads(r.stdout.decode())
        ws_url = new_tab["webSocketDebuggerUrl"]

        # Build ws script as a separate file to avoid quoting issues
        ws_script_path = f"{tmp_dir}/ws_download.js"
        wait_ms = wait_seconds * 1000
        timeout_ms = wait_ms + 15000

        ws_js = f"""
const WebSocket = require('/tmp/sec_pdf_work/node_modules/ws');
const ws = new WebSocket('{ws_url}');
let msgId = 1;
const pending = {{}};
ws.on('message', data => {{
    const msg = JSON.parse(data.toString());
    if (msg.id && pending[msg.id]) {{ pending[msg.id](msg); delete pending[msg.id]; }}
}});
ws.on('open', async () => {{
    const send = (m, p) => new Promise(r => {{ pending[msgId] = r; ws.send(JSON.stringify({{id: msgId++, method: m, params: p}})); }});
    await send('Page.enable', {{}});
    await send('Runtime.enable', {{}});
    await send('Page.enable', {});
    await send('Runtime.enable', {});
    await send('Page.navigate', {url: '{url}'});
    // Wait for network idle (XBRL content loaded via JS)
    await send('Page.setLifecycleEventsEnabled', {{}});
    await new Promise(r => setTimeout(r, {wait_ms}));
    // Get rendered HTML after JS has executed
    const readyState = await send('Runtime.evaluate', {{
        expression: 'document.readyState',
        returnByValue: true,
    }});
    // Get full HTML via document.body.innerHTML for JS-rendered content
    const bodyHtml = await send('Runtime.evaluate', {{
        expression: 'document.body ? document.body.innerHTML : document.documentElement.innerHTML',
        returnByValue: true,
    }});
    const html = bodyHtml.result.value || '';
    process.stdout.write(html);
    ws.close();
    ws.close();
    process.exit(0);
}});
ws.on('error', e => {{ console.error('WS error:', e.message); process.exit(1); }});
setTimeout(() => {{ console.error('Timeout'); process.exit(1); }}, {timeout_ms});
"""
        with open(ws_script_path, "w") as f:
            f.write(ws_js)

        result = subprocess.run(
            ["node", ws_script_path],
            capture_output=True, text=True, timeout=wait_seconds + 25
        )

        if result.returncode == 0 and result.stdout:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result.stdout)
            size = os.path.getsize(output_path)
            print(f"  Downloaded via Chrome: {output_path} ({(size/1024/1024):.2f} MB)")
            return True
        else:
            print(f"  Chrome download failed")
            return False

    finally:
        chrome_proc.kill()
        chrome_proc.wait()
        subprocess.run(f"lsof -ti:{debug_port} | xargs kill -9 2>/dev/null || true",
                       shell=True, capture_output=True)

def download_sec_images(html_path, cik_short, accession_flat):
    """Download all referenced images from HTML."""
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    img_pattern = re.compile(r'src="([a-z0-9_\-]+\.(?:jpg|png|gif|jpeg))"', re.IGNORECASE)
    images = set(img_pattern.findall(content))
    count = 0
    for img in images:
        img_path = os.path.join(os.path.dirname(html_path), img)
        if os.path.exists(img_path):
            continue
        img_url = f"https://www.sec.gov/Archives/edgar/data/{cik_short}/{accession_flat}/{img}"
        try:
            req = urllib.request.Request(img_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(img_path, "wb") as f:
                    f.write(resp.read())
            count += 1
        except Exception:
            pass
    print(f"  Downloaded {count} images")
    return count

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

def start_server(directory):
    port = get_free_port()
    class S(HTTPServer):
        allow_reuse_address = True
    server = S(("localhost", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  HTTP server on port {port}")
    return server, port

def stop_server(server):
    if server:
        server.shutdown()
        server.server_close()
    print("  HTTP server stopped")

def make_translate_url(sec_url):
    import urllib.parse
    return f"https://translate.google.com/translate?sl=en&tl=zh-CN&u={urllib.parse.quote(sec_url, safe='')}"

def generate_pdfs(symbol, fiscal_year=None):
    ticker = normalize_ticker(symbol).upper()
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n=== SEC EDGAR 10-K PDF ===\nSymbol: {ticker}")

    cik, name = lookup_cik(ticker)
    if not cik:
        print("ERROR: CIK not found"); return False
    print(f"CIK: {cik} | {name}")

    accession_flat, filename, filing_date = get_latest_10k_info(cik)
    if not accession_flat:
        print("ERROR: No 10-K found"); return False
    print(f"File: {filename} | Filed: {filing_date}")

    fy_match = re.search(r"(\d{4})", filename)
    fy_end = str(fiscal_year) if fiscal_year else (fy_match.group(1) if fy_match else filing_date[:4])
    cik_short = str(int(cik))

    sec_url = f"https://www.sec.gov/Archives/edgar/data/{cik_short}/{accession_flat}/{filename}"
    sec_dir = os.path.join(REPORTS_TMP, cik_short, accession_flat)
    os.makedirs(sec_dir, exist_ok=True)
    html_path = os.path.join(sec_dir, filename)

    if os.path.exists(html_path) and os.path.getsize(html_path) > 1000:
        print(f"  HTML cached: {html_path}")
    else:
        print(f"  Downloading HTML via Chrome...")
        if not download_html_via_chrome(sec_url, html_path, wait_seconds=15):
            return False

    download_sec_images(html_path, cik_short, accession_flat)

    server, port = start_server(REPORTS_TMP)

    out_en = os.path.join(REPORTS_DIR, f"{ticker}_10K_FY{fy_end}_{today}.pdf")
    out_zh = os.path.join(REPORTS_DIR, f"{ticker}_10K_FY{fy_end}_{today}_zh.pdf")

    try:
        en_url = f"http://localhost:{port}/{cik_short}/{accession_flat}/{filename}"
        print(f"\n[1/2] Generating English PDF...")
        r = subprocess.run(
            ["node", "/tmp/sec_to_pdf.js", en_url, out_en, "15"],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0:
            print(f"  -> {out_en} ({(os.path.getsize(out_en)/1024/1024):.2f} MB)")
        else:
            print(f"  Failed: {r.stderr[-200:]}")

        print(f"\n[2/2] Generating Chinese PDF...")
        zh_url = make_translate_url(sec_url)
        r = subprocess.run(
            ["node", "/tmp/sec_to_pdf.js", zh_url, out_zh, "18"],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0:
            print(f"  -> {out_zh} ({(os.path.getsize(out_zh)/1024/1024):.2f} MB)")
        else:
            print(f"  Failed: {r.stderr[-200:]}")

    finally:
        stop_server(server)

    print(f"\n=== Done ===")
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", help="e.g. US.NKE")
    parser.add_argument("--fy", type=int, help="Fiscal year override")
    args = parser.parse_args()
    sym = args.symbol.upper()
    if not any(sym.startswith(p) for p in ("US.", "HK.", "A.")):
        sym = "US." + sym
    success = generate_pdfs(sym, args.fy)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()