#!/usr/bin/env python3
"""生成器版本常量 —— 唯一定义点（单点定义，两侧共享，禁止在他处另写版本号）。

scripts/ 与 webapp/backend 是两套运行环境（脚本侧不能 import webapp.backend），
版本号由本文件单点定义：
  - 脚本侧（sec_analysis/report.py、dcf.py）：直接 import dataversion
  - 后端侧（webapp/backend/services/dataversion.py）：importlib 按路径加载本文件

版本含义：
  ANALYSIS_VERSION  sec_analysis 提取器版本（v2 = 10-Q 利润表单季口径加固后的版本）
  DCF_VERSION       dcf.py 估值器版本

产物落盘格式（md 头部，提取文件在"提取时间"行下方、DCF 报告在"生成时间"行下方）：
  分析提取文件: 生成器版本: analysis-v2
  DCF 报告:     生成器版本: dcf-v1
无该行的产物视为 legacy 旧数据（数据库 generator_version 列为 NULL）。
"""

ANALYSIS_VERSION = "v2"
DCF_VERSION = "v1"
