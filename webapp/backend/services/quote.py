"""现价获取（后端侧）。

复用 scripts/common.py 的 TencentQuoteProvider（腾讯财经备用行情源），
用 importlib 按路径加载（与 services/dataversion.py 同一做法，避免跨目录
import；scripts/common.py 模块级只有类定义，无副作用，可安全加载）。
行情仅作展示：任何异常都降级为 None，不得阻塞调用方接口。
"""
import importlib.util
import os
import threading

# webapp/backend/services/ → 上三级到项目根 → scripts/common.py
_COMMON_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, os.pardir, "scripts", "common.py"))

_lock = threading.Lock()
_provider = None  # 懒加载缓存，避免每次请求都 exec_module


def _get_provider():
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                spec = importlib.util.spec_from_file_location("stockgod_common", _COMMON_PATH)
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"行情模块缺失: {_COMMON_PATH}")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _provider = mod.TencentQuoteProvider
    return _provider


def get_price(symbol: str) -> float | None:
    """取现价（symbol 如 "US.NKE"）；行情失败/缺失一律返回 None。"""
    try:
        snap = _get_provider()().get_snapshot(symbol)
    except Exception:
        return None
    if not snap:
        return None
    price = snap.get("price") or 0
    return price if price > 0 else None
