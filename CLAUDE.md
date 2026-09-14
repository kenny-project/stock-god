# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

stock-god 是一个美股/港股价值投资数据查询与分析工具集，以 OpenClaw skill 形式运行（`SKILL.md` 定义 skill 入口与命令路由）。核心功能：选股筛选、个股深度报告、SEC 财报分析、DCF 估值。

## Running Scripts

所有脚本通过 `python3` 直接运行，无构建步骤、无测试。工作目录为项目根目录。

```bash
# 个股报告
python3 scripts/report.py US.NKE
python3 scripts/report.py HK.00700
python3 scripts/report.py US.NKE --no-cache      # 强制刷新
python3 scripts/report.py US.NKE -o reports/x.txt --cache-ttl 1800

# 选股筛选
python3 scripts/screener.py --limit 50 --top 10

# SEC 财报列出/下载（list/download 支持中文名，如 "英特尔"）
python3 scripts/sec_filings.py list US.NKE --form 10-K,10-Q
python3 scripts/sec_filings.py download US.NKE --years 5
python3 scripts/sec_filings.py download US.NKE --zh          # 下载后 Google Translate 生成中文 HTML
python3 scripts/sec_filings.py download --url "https://www.sec.gov/Archives/edgar/data/..."
python3 scripts/batch_download.sh -f tickers.txt             # 批量下载（tickers.txt 为关注列表）

# SEC 财报分析（需先 download）
python3 scripts/sec_analysis.py MSFT --all
python3 scripts/sec_analysis.py MSFT --all --form 10-K --force

# DCF 估值（需先 sec_analysis 生成数据）
python3 scripts/dcf.py US.NKE
python3 scripts/dcf.py QCOM --growth 8 --discount 10 --years 5 --safety 0.30

# 10-K PDF（英文原版 + 中文翻译版，需 Chrome CDP）
python3 scripts/edgar_10k.py US.NKE
python3 scripts/edgar_10k.py NKE --fy 2024

# 手续费统计
python3 scripts/fee_report.py              # 查询当前年份
python3 scripts/fee_report.py --year 2025  # 查询指定年份
```

## Dependencies

- **futuapi**: 通过 `importlib.util` 动态加载 `~/.openclaw/skills/futuapi/scripts/common.py`（expanduser，不在本项目内；fee_report.py 用 sys.path 方式加载同一模块）
- **标准库**: urllib, json, re, argparse, subprocess — 无第三方 pip 依赖
- **API Keys**: 硬编码在脚本中 — FMP (`OeyYwkTOzQUfkywmNu8p0NFIP1pTSv6x`)、Alpha Vantage (`UQ3XI876M9S3PKND`)

## Architecture

### Data Flow

```
S&P500+Nasdaq100 成分股 (739只)
  → Futu SimpleFilter 粗筛 (PE 5-35, 市值>50亿)
  → FMP 精筛 (ROE≥15%, 负债率<60%)
  → Finviz 补充 (评级/持仓/做空)
  → 综合评分 → Markdown 报告
```

SEC 分析流水线: `sec_filings.py download` → `sec_analysis.py`（提取章节+财务指标）→ `dcf.py`（估值）。

### Data Sources (5个，优先级递减)

| 数据源 | 用途 | 缓存 |
|:---|:---|:---|
| Futu OpenD | 实时行情、K线、snapshot | 不缓存 |
| FMP API | 财务数据（营收/净利润/Key Metrics/分红）| 1小时 |
| Finviz | 新闻、分析师评级、机构持仓、做空 | 1小时 |
| 腾讯财经 | 备用实时行情（A/美/港股通用，GBK 编码，字段索引速查表见 SKILL.md）| 不缓存 |
| Alpha Vantage | EPS surprise、EARNINGS | 1小时 |

### Key Modules

- [report.py](scripts/report.py) — 主报告生成器（14个章节：基本信息→VIX→EPS→财务→K线→F-Score→评级→持仓→新闻→分红→评估）
- [screener.py](scripts/screener.py) — 价值股筛选器（Futu粗筛→FMP精筛→Finviz补充→综合评分）
- [sec_filings.py](scripts/sec_filings.py) — SEC EDGAR 财报列出与下载，含中文名→ticker映射（`CN_NAME_MAP`）；自动检测外国私人发行人（有 20-F/6-K 记录则改用 20-F+6-K 而非 10-K/10-Q）
- [dcf.py](scripts/dcf.py) — 巴菲特式 DCF 估值（Owner Earnings = 净利润 + 折旧 - 维护性CapEx）
- [sec_analysis.py](scripts/sec_analysis.py) — 向后兼容入口，实际实现在 [sec_analysis/](scripts/sec_analysis/) 包（base/extraction/sec_10k/sec_10q/report/main），支持 `python -m sec_analysis`；`sec_analysis_cmd.py` 是同一入口的独立副本
- [finviz.py](scripts/finviz.py) — Finviz 数据获取（新闻50条+快照+评级+持仓）
- [edgar_10k.py](scripts/edgar_10k.py) — SEC 10-K PDF 生成（Chrome CDP + Google Translate 中文版）
- [batch_download.sh](scripts/batch_download.sh) — 批量下载，从 `tickers.txt` 或参数读取，内置 FOREIGN_ISSUERS 列表自动切换 20-F/6-K
- monitor.py — 持仓监控占位（开发中）；common.py — 共享缓存工具

### Report Output

所有报告输出到 `reports/` 目录：
- 个股报告: `reports/{SYMBOL}_{timestamp}.txt`
- 选股报告: `reports/value_screener_{timestamp}.md`
- SEC 财报原文: `reports/sec_filings/{TICKER}/`
- SEC 分析: `reports/sec_analysis/{TICKER}/`
- DCF 报告: `reports/dcf/{TICKER}_DCF_{date}.md`
- 10-K PDF: `reports/{TICKER}_10K_FY{YYYY}.pdf`

### Cache

缓存目录: `~/.openclaw/cache/stock-god/`（screener/finviz）和 `~/.openclaw/cache/stock-analysis/`（report）。screener 粗筛数据另存于 `~/.openclaw/workspace-stock_god/value-screener/data/`。

格式: JSON 文件，包含 `_ts`（时间戳）和 `_ttl`（过期秒数）字段。

## Known Limitations

- Futu `get_stock_filter` 的 `MARKET_VAL` 范围筛选返回错误码 2159，需回退到 cached 数据
- FMP 免费账号有 429 限流，脚本中有指数退避重试（1s/2s/4s）
- Alpha Vantage 免费 Key 每日 25 次配额
- Finviz 新闻延迟 15 分钟
- SEC 财报解析使用正则匹配，非 XBRL 结构化解析
- 腾讯财经 PB 字段（索引46）仅 A 股有效，美股/港股该位置是英文公司名

## Documentation

- [README.md](README.md) — 项目概览、运行示例、代码结构
- [SKILL.md](SKILL.md) — OpenClaw skill 命令路由、腾讯财经 API 字段索引速查表
- [docs/architecture.md](docs/architecture.md) — SEC 分析流程、DCF 公式、提取指标
- [docs/dcf.md](docs/dcf.md) — DCF 估值模型详细实现
- [docs/sec_analysis_fields.md](docs/sec_analysis_fields.md) — SEC 分析字段说明
