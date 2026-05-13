#!/usr/bin/env python3
"""
stock-god help - 显示所有可用命令及用法
"""

HELP_TEXT = """
╔══════════════════════════════════════════════════════════════════╗
║                  📈 StockGod 股神 — 命令手册                    ║
╚══════════════════════════════════════════════════════════════════╝

用法: /stock-god <command> [options]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📊 report <symbol>          个股深度分析报告
     示例: /stock-god report US.NKE
           /stock-god report HK.00700

  🔍 screener                 价值股筛选，生成精选池
     示例: /stock-god screener
           /stock-god screener --limit 10 --top 5

  📑 list <symbol>            列出 SEC 所有财报链接
     示例: /stock-god list US.NKE
           /stock-god list 英特尔 --form 10-K,10-Q
           /stock-god list 高通 --limit 20

  ⬇️  download <symbol>        下载 SEC 财报原始文件（默认近5年 10-K + 10-Q）
     示例: /stock-god download US.NKE
           /stock-god download 苹果
           /stock-god download 高通 --fy 2024
           /stock-god download 高通 --form 10-K
           /stock-god download 高通 --years 3    ← 下载近3年
           /stock-god download --url <SEC_URL>
           /stock-god download 高通 --force    ← 强制重新下载

  📄 edgar <symbol>           生成 10-K 年报 PDF（英文+中文）
     示例: /stock-god edgar US.NKE
           /stock-god edgar NKE --fiscal-year 2024

  🔬 analyze <symbol>         分析 SEC 财报，提取关键章节
     示例: /stock-god analyze QCOM              ← 分析最新财报
           /stock-god analyze QCOM --all        ← 分析所有财报
           /stock-god analyze QCOM --all --form 10-K  ← 只分析 10-K
           /stock-god analyze QCOM --file qcom-20240929.htm  ← 分析指定文件

  😱 vix                      VIX 恐慌指数查询

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🌐 支持中文名/别名
     高通 → QCOM | 苹果 → AAPL | 英伟达 → NVDA | 腾讯 → 0700
     耐克 → NKE | 特斯拉 → TSLA | 台积电 → TSM | 谷歌 → GOOGL
     完整映射见 scripts/sec_filings.py CN_NAME_MAP

  📂 报告输出路径
     ~/.openclaw/reports/       ← 报告和 PDF
     ~/.openclaw/reports/sec_filings/  ← SEC 原始文件

  🔑 数据源
     Futu OpenD   实时行情/K线（主力）
     FMP          财务数据（主力）
     Finviz       新闻/评级/持仓（辅助）
     腾讯财经     备用行情
     Alpha Vantage  EPS/Earnings（每日25次）

  ⚠️  限制
     - 不支持 A 股、欧洲市场、加密货币
     - 不提供任何投资建议
     - Alpha Vantage 免费 Key 每日 25 次
     - FMP 中国 ADR 需 Premium
"""


def main():
    print(HELP_TEXT.strip())


if __name__ == "__main__":
    main()
