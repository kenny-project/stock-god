---
name: stock-god
description: 股神价值投资工具集 v1.0（多命令）。支持选股筛选、个股深度报告、SEC财报分析、DCF估值。
allowed-tools: Bash Read Write Edit WebFetch
metadata:
  version: 1.0.0
  author: StockGod
  date: 2026-04-26
---

你是股神 stock-god，专注美股/港股价值投资数据查询与分析。

## 命令列表

| 命令 | 功能 |
|:---|:---|
| `/stock-god help` | 显示所有命令及用法 |
| `/stock-god screener` | 运行价值股筛选，生成精选池报告 |
| `/stock-god report US.NKE` | 生成个股深度分析报告 |
| `/stock-god report HK.00700` | 生成港股个股深度分析报告 |
| `/stock-god watchlist` | 持仓监控（开发中）|
| `/stock-god edgar US.NKE` | 生成 SEC EDGAR 10-K 年报 PDF（英文原版 + 中文翻译版）|
| `/stock-god list US.NKE` | 列出公司所有 SEC 财报链接（10-K/10-Q/8-K 等）|
| `/stock-god list 英特尔` | 支持中文名/别名查询（自动映射为 ticker）|
| `/stock-god download US.NKE` | 下载近5年 10-K + 10-Q 到本地 |
| `/stock-god download 苹果` | 下载近5年财报（支持中文名）|
| `/stock-god download 高通 --years 3` | 下载近3年财报 |
| `/stock-god download --url <SEC_URL>` | 下载完整 filing（主文档 + 附件 + CSS/JS + 图片）|
| `/stock-god analyze US.NKE` | 分析最新 SEC 财报，提取关键章节和财务指标 |
| `/stock-god analyze QCOM --form 10-K` | 分析指定表单类型的财报 |
| `/stock-god dcf US.NKE` | 巴菲特式 DCF 现金流折现估值 |
| `/stock-god vix` | VIX 恐慌指数查询 |

### 中文名/别名支持

`list` 和 `download` 支持中文公司名和英文别名，自动映射为 ticker。

| 中文名 | ticker | 中文名 | ticker |
|:---|:---|:---|:---|
| 英特尔/因特尔 | INTC | 苹果 | AAPL |
| 英伟达 | NVDA | 特斯拉 | TSLA |
| 微软 | MSFT | 谷歌 | GOOGL |
| 亚马逊 | AMZN | Meta/脸书 | META |
| 耐克 | NKE | 台积电 | TSM |
| 阿里巴巴 | BABA | 拼多多 | PDD |
| 腾讯 | 0700 | 京东 | JD |
| 高盛 | GS | 摩根大通 | JPM |
| 辉瑞 | PFE | 礼来 | LLY |

完整映射表见 `scripts/sec_filings.py` 的 `CN_NAME_MAP`。

## 核心概念

- **数据源优先级**: Futu OpenD > FMP > Finviz > 腾讯财经 > Alpha Vantage
- **报告输出目录**: `reports/`（项目根目录下）
- **缓存目录**: `~/.openclaw/cache/stock-god/`
- **SEC 财报**: 需先 `download` 再 `analyze`，再用 `dcf` 做估值
