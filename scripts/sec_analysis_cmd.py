#!/usr/bin/env python3
"""
SEC Filing Analysis - 独立可执行脚本
可以从任何位置运行
"""

import sys
import os

# 将 scripts 目录添加到 Python 路径
scripts_dir = os.path.dirname(os.path.abspath(__file__))
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from sec_analysis.main import main

if __name__ == "__main__":
    main()
