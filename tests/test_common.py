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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from common import QuoteSource  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
