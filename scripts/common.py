#!/usr/bin/env python3
"""
共享数据获取模块 —— 统一行情接口

设计（适配器模式）:
  - QuoteProvider: 单个数据源的适配器，实现 get_snapshot(ticker)
  - QuoteSource:   对调用方的统一入口，按序尝试多个数据源直到拿到有效行情

输入/输出契约（所有数据源一致，替换数据源不影响调用方）:
  输入: ticker（如 "US.NKE"、"HK.00700"）
  输出: dict（缺字段填 0）或 None（全部数据源失败）
    {
      "name":              str,
      "price":             float,  # 现价，<=0 视为无效
      "marketCap":         float,  # 总市值（元/美元）；腾讯源美股实测可取，港股填 0
      "sharesOutstanding": float,  # 流通股数（股）；腾讯源美股实测可取（总股本），港股填 0
      "pe":                float,
      "pb":                float,
      "source":            str,    # 命中的数据源标识（futu/tencent/...）
    }
"""

import importlib.util
import os
import re
import sys
import urllib.request


class FutuQuoteProvider:
    """富途 OpenD 行情源（主数据源，需本机 OpenD 运行）"""

    name = "futu"

    def get_snapshot(self, ticker):
        futuapi_path = os.path.expanduser("~/.openclaw/skills/futuapi/scripts/common.py")
        spec = importlib.util.spec_from_file_location("_fc", futuapi_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_fc"] = mod
        spec.loader.exec_module(mod)

        ctx = mod.create_quote_context()
        try:
            symbol = ticker if ticker.startswith(("US.", "HK.")) else f"US.{ticker}"
            ret, data = ctx.get_market_snapshot([symbol])
            if ret != 0 or data is None or data.empty:
                return None
            row = data.iloc[0]
            return {
                "name": str(row.get("name", "")),
                "price": float(row.get("last_price", 0) or 0),
                "marketCap": float(row.get("total_market_val", 0) or 0),
                "sharesOutstanding": float(row.get("outstanding_shares", 0) or 0),
                "pe": float(row.get("pe_ttm_ratio", 0) or 0),
                "pb": float(row.get("pb_ratio", 0) or 0),
                "source": self.name,
            }
        finally:
            mod.safe_close(ctx)


class TencentQuoteProvider:
    """腾讯财经行情源（备用）。

    美股字段实测（2026-09-18，GOOGL/AAPL/NVDA 三只交叉验证）：
      [45] = 总市值（亿美元） = [62]总股本(股) × 现价，逐只吻合
      [44] = 流通市值（亿美元） = [63]流通股本(股) × 现价
      [62] = 总股本（股，原始计数）  [63] = 流通股本（股）
      注意：SKILL.md 速查表把美股 [44]/[45] 标反了，本实现以实测为准；
      [46] 为英文公司名（非 PB），与 CLAUDE.md 已知限制一致。
    港股仍仅现价（未实测到可靠市值字段），契约缺字段填 0。
    """

    name = "tencent"

    def get_snapshot(self, ticker):
        mkt, _, code = ticker.partition(".")
        mkt = mkt.lower()
        if mkt not in ("us", "hk"):
            return None
        qq_url = f"https://qt.gtimg.cn/q={mkt}{code.upper() if mkt == 'us' else code}"
        req = urllib.request.Request(qq_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            text = r.read().decode("gbk", errors="replace")
        m = re.search(r'="([^"]+)"', text)
        if not m:
            return None
        # 格式（美股/港股统一，~ 分隔）:
        # v_usAAPL="200~苹果~AAPL.OQ~326.57~315.34~316.67~成交量~..."
        #  0=市场占位 1=名称 2=代码 3=现价 4=昨收 5=今开
        fields = m.group(1).split("~")
        if len(fields) < 10:
            return None
        try:
            price = float(fields[3])
        except ValueError:
            return None
        snap = {
            "name": fields[1],
            "price": price,
            "marketCap": 0,
            "sharesOutstanding": 0,
            "pe": 0,
            "pb": 0,
            "source": self.name,
        }
        if mkt == "us" and len(fields) > 62:
            try:
                # 契约单位为元/美元（与 Futu total_market_val 一致）：亿美元 × 1e8
                snap["marketCap"] = float(fields[45]) * 1e8
            except ValueError:
                pass  # 字段缺失/非数字 → 保持 0，宁缺勿错
            try:
                # [62] 总股本（股）
                snap["sharesOutstanding"] = float(fields[62])
            except ValueError:
                pass
        return snap


class QuoteSource:
    """统一行情入口：按序尝试数据源，返回第一份有效行情（price>0）"""

    def __init__(self, providers=None):
        self._providers = providers if providers is not None \
            else [FutuQuoteProvider(), TencentQuoteProvider()]

    def get_snapshot(self, ticker):
        for provider in self._providers:
            try:
                snap = provider.get_snapshot(ticker)
            except Exception as e:
                print(f"  ⚠️  {provider.name} 行情失败: {e}")
                continue
            if snap and snap.get("price", 0) > 0:
                return snap
        return None
