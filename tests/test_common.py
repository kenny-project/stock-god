#!/usr/bin/env python3
"""
common.py 统一行情接口测试

QuoteSource 适配器契约:
  - 输入: ticker（如 "US.NKE"）
  - 输出: 统一 dict（name/price/marketCap/sharesOutstanding/pe/pb/source）或 None
  - 多数据源按序 fallback，第一个有有效价格的生效
  - 数据源异常不向上抛，调用方无感知
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import common  # noqa: E402
from common import QuoteSource, TencentQuoteProvider  # noqa: E402


class _FakeProvider:
    """测试用假数据源，可注入 QuoteSource 验证适配逻辑"""

    name = "fake"

    def __init__(self, snapshot=None, error=False):
        self._snapshot = snapshot
        self._error = error
        self.calls = []

    def get_snapshot(self, ticker):
        self.calls.append(ticker)
        if self._error:
            raise RuntimeError("provider down")
        return self._snapshot


def _full_snap(price=100.0):
    return {"name": "TEST", "price": price, "marketCap": 1e11,
            "sharesOutstanding": 1e9, "pe": 20.0, "pb": 5.0, "source": "fake"}


class TestQuoteSource(unittest.TestCase):
    def test_uses_first_working_provider(self):
        first, second = _FakeProvider(_full_snap()), _FakeProvider(_full_snap(200))
        snap = QuoteSource([first, second]).get_snapshot("US.TEST")
        self.assertEqual(snap["price"], 100.0)
        self.assertEqual(first.calls, ["US.TEST"])
        self.assertEqual(second.calls, [])  # 第一源成功，不再请求第二源

    def test_falls_back_to_next_provider(self):
        broken = _FakeProvider(None)               # 返回 None
        down = _FakeProvider(error=True)           # 抛异常
        ok = _FakeProvider(_full_snap(300))
        snap = QuoteSource([broken, down, ok]).get_snapshot("US.TEST")
        self.assertEqual(snap["price"], 300.0)
        self.assertEqual(broken.calls, ["US.TEST"])
        self.assertEqual(down.calls, ["US.TEST"])

    def test_zero_price_is_invalid(self):
        """price=0 视为无效数据，继续 fallback（不拿 0 冒充行情）"""
        zero = _FakeProvider({"name": "T", "price": 0, "source": "zero"})
        ok = _FakeProvider(_full_snap(50))
        snap = QuoteSource([zero, ok]).get_snapshot("US.TEST")
        self.assertEqual(snap["price"], 50.0)

    def test_returns_none_when_all_fail(self):
        source = QuoteSource([_FakeProvider(None), _FakeProvider(error=True)])
        self.assertIsNone(source.get_snapshot("US.TEST"))


class TestTencentQuoteProvider(unittest.TestCase):
    """腾讯美股源市值/股数字段映射。

    实测（2026-09-18，GOOGL/AAPL/NVDA 交叉验证）：
      [45] = 总市值（亿美元） = [62]总股本(股) × 现价，逐只吻合
      [44] = 流通市值（亿美元）、[63] = 流通股本；SKILL.md 速查表把 [44]/[45] 标反了
    """

    def _fetch(self, fields, ticker="US.GOOGL"):
        body = ('v_test="' + "~".join(fields) + '";').encode("gbk")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return body

        with mock.patch.object(common.urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp()):
            return TencentQuoteProvider().get_snapshot(ticker)

    def test_us_market_cap_and_shares(self):
        # 2026-09-18 实测 GOOGL：price=349.54，[45]=42748.51421（亿美元），[62]=12229934831（股）
        fields = ["0"] * 63
        fields[1], fields[2], fields[3] = "谷歌-A", "GOOGL.OQ", "349.54"
        fields[45], fields[62] = "42748.51421", "12229934831"
        snap = self._fetch(fields)
        self.assertEqual(snap["source"], "tencent")
        self.assertEqual(snap["marketCap"], 42748.51421 * 1e8)   # 契约单位：美元
        self.assertEqual(snap["sharesOutstanding"], 12229934831.0)
        # 实测吻合关系：市值 = 总股本 × 现价
        self.assertAlmostEqual(snap["marketCap"] / snap["sharesOutstanding"], 349.54, places=2)

    def test_us_bad_numeric_fields_stay_zero(self):
        """字段缺失/非数字 → 契约填 0（宁缺勿错，绝不编造）"""
        fields = ["0"] * 63
        fields[1], fields[3] = "TEST", "10.0"
        fields[45], fields[62] = "N/A", ""
        snap = self._fetch(fields)
        self.assertEqual(snap["marketCap"], 0)
        self.assertEqual(snap["sharesOutstanding"], 0)

    def test_short_response_stays_zero(self):
        """字段不足 63 个（如部分港股返回）→ 市值/股数保持 0，不影响现价"""
        fields = ["0"] * 30
        fields[1], fields[3] = "腾讯控股", "700.0"
        snap = self._fetch(fields, ticker="HK.00700")
        self.assertEqual(snap["marketCap"], 0)
        self.assertEqual(snap["sharesOutstanding"], 0)
        self.assertEqual(snap["price"], 700.0)


if __name__ == "__main__":
    unittest.main()
