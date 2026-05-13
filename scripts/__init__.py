"""stock-god skill v1.0.0 - 统一入口"""
import os

VERSION = "1.0.0"
CACHE_DIR = os.path.expanduser("~/.openclaw/cache/stock-god/")
os.makedirs(CACHE_DIR, exist_ok=True)
