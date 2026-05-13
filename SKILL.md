---
name: stock-god
description: 股神价值投资工具集 v1.0（多命令）。支持选股筛选、个股深度报告、持仓监控。
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

## 数据源（5个）

| 优先级 | 数据源 | 用途 |
|:---:|:---|:---|
| 1 | Futu OpenD | 实时行情、K线、snapshot |
| 2 | FMP | 财务数据（营收/净利润/Key Metrics/分红历史）|
| 3 | Finviz | 新闻（50条）、分析师评级、机构持仓、做空、Beta |
| 4 | 腾讯财经 | 备用实时行情 |
| 5 | Alpha Vantage | EPS surprise、EARNINGS（含 reportTime）|

## 缓存策略

| 数据源 | TTL |
|:---|---:|
| VIX | 5 分钟 |
| FMP / Finviz / AV | 1 小时 |
| 富途行情/K线 | 不缓存 |

## 报告章节（14个）

基本信息 → VIX → 季度EPS vs 预测 → 季度财务 → 年度财务 → K线
→ F-Score → 核心指标 → 分析师评级 → 机构持仓 → **Finviz新闻** →
**分红历史** → 综合评估 → 结论

## 代码结构

```
stock-god/scripts/
├── help.py              ← /stock-god help（命令手册）
├── report.py            ← /stock-god report（主报告生成）
├── finviz.py            ← Finviz 数据获取（新闻+评级+快照）
├── common.py            ← 共享数据获取（FMP/AV/腾讯/Futu）
├── screener.py          ← /stock-god screener（选股筛选）✅
├── sec_filings.py       ← /stock-god list + download（SEC 财报列出与下载）✅
├── download_filing.py   ← 下载完整 filing（主文档 + 附件 + CSS/JS + 图片）✅
├── edgar_10k.py         ← /stock-god edgar（10-K PDF 生成）
├── sec_analysis.py      ← /stock-god analyze（向后兼容入口）
├── sec_analysis_cmd.py  ← /stock-god analyze（独立可执行脚本）
├── sec_analysis/        ← /stock-god analyze（模块化实现）
│   ├── __init__.py
│   ├── __main__.py
│   ├── base.py          # 基础抽象类
│   ├── extraction.py    # 共用提取逻辑
│   ├── sec_10k.py       # 10-K 特定模式
│   ├── sec_10q.py       # 10-Q 特定模式
│   ├── report.py        # 报告生成
│   └── main.py          # CLI 入口
├── dcf.py               ← /stock-god dcf（巴菲特式 DCF 估值）✅
└── monitor.py           ← /stock-god watchlist（占位）
```

## screener.py 已知限制

| 限制 | 说明 | 处理方式 |
|:---|:---|:---|
| Futu MARKET_VAL 筛选 | 返回错误码 2159，范围筛选不可用 | 回退到 cached 数据 |
| Futu PE_TTM 筛选 | 可用，但可能不返回市值字段 | Python 层二次验证 |
| FMP API 429 限流 | 免费账号频繁触发 | 指数退避重试（1s/2s/4s）+ Finviz ROE 备选 |
| Finviz 404 | 部分 ADR/外国股票查不到 | 跳过该数据源 |

**运行示例：**
```bash
python3 ~/.openclaw/skills/stock-god/scripts/screener.py --limit 10 --top 5
```

**报告输出：** `~/.openclaw/reports/value_screener_YYYYMMDD_HHMMSS.md`

## edgar 10-K PDF 生成

### 功能
从 SEC EDGAR 下载公司最新 10-K 年报，转换为 PDF（英文原版 + 中文翻译版）。

### 输出文件
| 文件 | 路径 |
|:---|:---|
| 英文 PDF | `~/.openclaw/reports/{TICKER}_10K_FY{YYYY}_{YYYY-MM-DD}.pdf` |
| 中文 PDF | `~/.openclaw/reports/{TICKER}_10K_FY{YYYY}_{YYYY-MM-DD}_zh.pdf` |

示例：`~/.openclaw/reports/NKE_10K_FY2025_2026-04-27.pdf`

### 技术方案
| 步骤 | 技术 |
|:---|:---|
| CIK 查询 | SEC company_tickers.json API |
| 10-K 信息 | SEC submissions API |
| HTML 下载 | `/tmp/sec_download.sh` |
| 图片加载 | 本地 HTTP 服务器（reports/tmp/） |
| 英文 PDF | Chrome CDP + sec_to_pdf.js |
| 中文 PDF | Google Translate 页面 → Chrome CDP |

### 运行示例
```bash
python3 ~/.openclaw/skills/stock-god/scripts/edgar_10k.py US.NKE
python3 ~/.openclaw/skills/stock-god/scripts/edgar_10k.py NKE --fiscal-year 2024
```

### 已知限制
- 中文 PDF 依赖 Google Translate 对 SEC 页面的翻译效果
- SEC 限制了部分图片的跨域访问，本地 HTTP 服务器解决相对路径问题

## SEC 财报列出与下载

### 功能
列出公司在 SEC EDGAR 的所有财报链接，或下载指定财报原始文件到本地。

### 运行示例
```bash
# 列出所有财报
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py list US.NKE

# 只列出 10-K 和 10-Q
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py list US.NKE --form 10-K,10-Q

# 限制显示条数
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py list US.NKE --limit 20

# 下载最新 10-K
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py download US.NKE

# 下载指定财年的 10-K
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py download US.NKE --fy 2024

# 下载最新 10-Q
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py download US.NKE --form 10-Q

# 下载 8-K
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py download US.NKE --form 8-K

# 下载完整 filing（主文档 + 附件 + CSS/JS + 图片）
python3 ~/.openclaw/skills/stock-god/scripts/sec_filings.py download --url "https://www.sec.gov/Archives/edgar/data/804328/000080432826000061/qcom-20260329.htm"
```

### 输出文件
| 文件 | 路径 |
|:---|:---|
| 财报原始文件 | `~/.openclaw/reports/sec_filings/{TICKER}/{TICKER}_{FORM}_{DATE}.htm` |
| 索引页（含附件列表）| `~/.openclaw/reports/sec_filings/{TICKER}/{TICKER}_{FORM}_{DATE}_index.html` |

### 支持的表单类型
10-K, 10-Q, 8-K, 20-F, 6-K, DEF 14A, S-1, SC 13G, SD 等

## SEC 财报分析

### 功能
分析下载的 SEC 财报（10-K/10-Q），提取关键章节和财务指标，生成结构化分析报告。

支持 10-K 和 10-Q 的差异化处理：
- 10-K 年报：检查 7 个核心章节（business, risk_factors, mda, financials 等）
- 10-Q 季报：检查 6 个核心章节（financial_statements, mda, controls_procedures 等）

### 运行示例

```bash
# 方式1: 直接调用脚本（推荐）
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT

# 方式2: 使用独立脚本
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis_cmd.py MSFT --all

# 方式3: 从 scripts 目录运行模块
cd ~/.openclaw/skills/stock-god/scripts && python3 -m sec_analysis MSFT --all

# 分析所有已下载的财报
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT --all

# 只分析 10-K 年报
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT --all --form 10-K

# 只分析 10-Q 季报
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT --all --form 10-Q

# 分析指定的单个文件
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT --file msft-10q_20220930.htm

# 强制重新提取（覆盖已有文件）
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT --all --force
```

> ⚠️ **注意**：`python3 -m` 后面跟的是模块名（`sec_analysis`），不是文件路径。

### 参数说明
| 参数 | 说明 | 默认值 |
|:---|:---|:---|
| `ticker` | 股票代码（必填） | - |
| `--form` | 表单类型过滤（10-K, 10-Q） | 不过滤 |
| `--force` | 强制重新提取已存在的文件 | false |
| `--latest` | 只分析最新的财报 | **默认行为** |
| `--all` | 分析所有已下载的财报 | false |
| `--file FILE` | 分析指定的单个文件 | - |

> `--latest`、`--all`、`--file` 三个参数互斥，只能使用其中一个。

### 目录结构

输出目录：
```
~/.openclaw/reports/sec_analysis/
├── MSFT/                              # 提取的财报数据（中间产物，可复用）
│   ├── 10-K_FY2022.md                 # 年报
│   ├── 10-Q_2022Q3.md                 # 季报
│   └── ...
├── MSFT_analysis_20260511.md          # 整合后的最终分析文档
└── NKE/
    └── ...
```

代码结构：
```
sec_analysis/
├── __init__.py      # 包入口
├── __main__.py      # 支持 python -m sec_analysis
├── base.py          # 基础抽象类 (FilingAnalyzer)
├── extraction.py    # 共用提取逻辑（财务指标、现金流）
├── sec_10k.py       # 10-K 特定模式和章节
├── sec_10q.py       # 10-Q 特定模式和章节
├── report.py        # 报告生成逻辑
└── main.py          # CLI 入口
```

### 提取文件内容
每个提取文件（.md）包含：
- 财务数据表格
  - 营收、毛利润、毛利率
  - 营业利润、营业利润率
  - 净利润、净利率、EPS
  - 总资产、总负债、股东权益
- 现金流明细
  - 经营现金流、投资现金流、筹资现金流
  - 自由现金流（经营现金流 - 资本支出）
- 公司概况（Item 1 原文）
- 风险因素（Item 1A 原文）
- 管理层展望（Item 7 MD&A 原文）
- 财务报表摘要
- 每股收益

### 最终分析文档
整合所有提取文件，生成：
- 年度趋势表格（多期对比 + 3年CAGR）
  - 营收、毛利润、毛利率、营业利润、营业利润率
  - 净利润、净利率、EPS
  - 总资产、总负债、股东权益
- 季度趋势表格（多期对比）
  - 营收、毛利润、毛利率、营业利润、营业利润率
  - 净利润、净利率、EPS
- 现金流趋势表格（年度对比）
  - 经营现金流、投资现金流、筹资现金流、自由现金流
- 公司概况汇总（已翻译为中文）
- 风险因素汇总（已翻译为中文）
- 管理层展望汇总（已翻译为中文）

### 增量更新
- 已存在的提取文件会自动跳过
- 只提取新增的财报
- 最终分析文档每次重新生成

### 执行流程

```bash
# 运行分析
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py MSFT
```

#### 流程图

```
main() [sec_analysis/main.py]
  │
  ├─ 1. find_filing_files(MSFT)
  │     └─ 从 ~/.openclaw/reports/sec_filings/MSFT/ 查找 .htm 文件
  │     └─ 排除 _index.html，按文件名排序
  │
  ├─ 2. 遍历每个文件 → analyze_single_filing()
  │     │
  │     ├─ 2.1 读取 HTML 文件
  │     │
  │     ├─ 2.2 detect_filing_type()
  │     │     └─ 从文件名或 XBRL 标签检测
  │     │     └─ 10k/10-K → 10-K，10q/10-Q → 10-Q
  │     │
  │     ├─ 2.3 extract_fiscal_period()
  │     │     └─ 从文件名提取日期，推断财年/季度
  │     │
  │     ├─ 2.4 获取对应的分析器
  │     │     └─ 10-K → Sec10KAnalyzer
  │     │     └─ 10-Q → Sec10QAnalyzer
  │     │
  │     ├─ 2.5 analyzer.analyze(html_content)
  │     │     ├─ extract_text_from_html()
  │     │     │     └─ HTMLParser 提取纯文本，跳过 script/style/head
  │     │     ├─ find_body_start()
  │     │     │     └─ 跳过目录，找到正文开始位置
  │     │     └─ extract_sections()
  │     │           └─ 根据 EXPECTED_SECTIONS 提取章节
  │     │           └─ 10-K: business, risk_factors, mda, financials 等
  │     │           └─ 10-Q: financial_statements, mda, controls_procedures 等
  │     │
  │     ├─ 2.6 extract_key_metrics()
  │     │     └─ 正则提取：营收、成本、毛利润、营业利润、净利润、EPS
  │     │     └─ 计算：毛利率、营业利润率、净利率
  │     │     └─ 提取：总资产、总负债，计算股东权益
  │     │
  │     ├─ 2.7 extract_cash_flows()
  │     │     └─ 正则提取：经营/投资/筹资现金流
  │     │     └─ 提取：资本支出
  │     │     └─ 计算：自由现金流 = 经营现金流 - 资本支出
  │     │
  │     └─ 2.8 generate_extraction_md()
  │           └─ 生成 ~/.openclaw/reports/sec_analysis/MSFT/{TYPE}_{PERIOD}.md
  │
  └─ 3. generate_analysis_report()
        │
        ├─ 3.1 读取所有提取文件
        │     └─ 解析财务数据、现金流数据
        │
        ├─ 3.2 生成年度趋势表格
        │     └─ 营收/毛利润/毛利率/营业利润/营业利润率/净利润/净利率/EPS
        │     └─ 总资产/总负债/股东权益
        │     └─ 计算 3年 CAGR
        │
        ├─ 3.3 生成季度趋势表格
        │     └─ 营收/毛利润/毛利率/营业利润/营业利润率/净利润/净利率/EPS
        │
        ├─ 3.4 生成现金流趋势表格
        │     └─ 经营现金流/投资现金流/筹资现金流/自由现金流
        │
        ├─ 3.5 翻译文本内容
        │     └─ 公司概况、风险因素、管理层展望 → 中文
        │
        └─ 3.6 输出最终报告
              └─ ~/.openclaw/reports/sec_analysis/QCOM_analysis_YYYYMMDD.md
```

#### 提取的财务指标

| 类别 | 指标 | 来源 |
|:---|:---|:---|
| 利润表 | 营收 (Revenue) | 正则匹配 "Total revenues" |
| 利润表 | 成本 (Cost of revenues) | 正则匹配 "Cost of revenues" |
| 利润表 | 毛利润 (Gross Profit) | 营收 - 成本 |
| 利润表 | 毛利率 (Gross Margin) | 毛利润 / 营收 |
| 利润表 | 营业利润 (Operating Income) | 正则匹配 "Operating income" |
| 利润表 | 营业利润率 (Operating Margin) | 营业利润 / 营收 |
| 利润表 | 净利润 (Net Income) | 正则匹配 "Net income" |
| 利润表 | 净利率 (Net Margin) | 净利润 / 营收 |
| 利润表 | EPS | 正则匹配 "earnings per share" |
| 资产负债表 | 总资产 (Total Assets) | 正则匹配 "Total assets" |
| 资产负债表 | 总负债 (Total Liabilities) | 正则匹配 "Total liabilities" |
| 资产负债表 | 股东权益 (Stockholders' Equity) | 总资产 - 总负债 |
| 现金流 | 经营现金流 | 正则匹配 "Net cash provided by operating activities" |
| 现金流 | 投资现金流 | 正则匹配 "Net cash (used) provided by investing activities" |
| 现金流 | 筹资现金流 | 正则匹配 "Net cash used by financing activities" |
| 现金流 | 资本支出 (CapEx) | 正则匹配 "Capital expenditures" |
| 现金流 | 自由现金流 (FCF) | 经营现金流 - 资本支出 |

> 详细的字段说明文档见 [docs/sec_analysis_fields.md](docs/sec_analysis_fields.md)

#### 10-K vs 10-Q 差异化处理

| 章节 | 10-K 年报 | 10-Q 季报 |
|:---|:---|:---|
| business | ✅ Item 1 | ❌ |
| risk_factors | ✅ Item 1A | ✅ Part II Item 1A |
| properties | ✅ Item 2 | ❌ |
| legal_proceedings | ✅ Item 3 | ❌ |
| mda | ✅ Item 7 | ✅ Item 2 |
| market_risk | ✅ Item 7A | ✅ Item 3 |
| financials | ✅ Item 8 | ✅ Item 1 |
| earnings | ✅ | ✅ |
| financial_statements_notes | ✅ | ✅ |
| controls_procedures | ✅ Item 9A | ✅ Item 4 |
| exhibits | ✅ Item 15 | ✅ Item 6 |

**改进效果**：
- 10-K：检查 7 个核心章节
- 10-Q：检查 6 个核心章节（减少 11 个不必要的警告）

## DCF 现金流折现估值

### 功能
基于巴菲特的 Owner Earnings 理念，计算企业内在价值。

### 核心公式
```
Owner Earnings = 净利润 + 折旧/摊销 - 维护性资本支出
内在价值 = Σ(OE_t / (1+r)^t) + 终值 / (1+r)^n
```

### 运行示例
```bash
# 基本用法（自动获取数据）
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py US.NKE

# 指定参数
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py AAPL --growth 8 --discount 10

# 5 年预测期 + 30% 安全边际
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py QCOM --years 5 --safety 0.30
```

### 参数说明
| 参数 | 默认值 | 说明 |
|:---|:---:|:---|
| `ticker` | 必填 | 股票代码 |
| `--growth` | 自动估算 | 预测增长率（%） |
| `--discount` | 10.0 | 折现率（%） |
| `--terminal-growth` | 3.0 | 永续增长率（%） |
| `--years` | 10 | 预测年数 |
| `--safety` | 0.25 | 安全边际（0-1） |

### 输出报告
`~/.openclaw/reports/dcf/{TICKER}_DCF_{YYYYMMDD}.md`

报告包含：Owner Earnings 计算表、预测现金流、估值结果、敏感性分析、巴菲特检查清单。

### 数据来源
| 优先级 | 数据源 | 用途 |
|:---:|:---|:---|
| 1 | SEC 分析数据 | 营收、净利润、D&A、CapEx、现金流（主数据源） |
| 2 | FMP API | 补充 SEC 缺失字段、实时行情（fallback） |

> 详细的实现文档见 [docs/dcf.md](docs/dcf.md)

## API Keys

- **Alpha Vantage**: `UQ3XI876M9S3PKND`（每日25次免费）
- **FMP**: `OeyYwkTOzQUfkywmNu8p0NFIP1pTSv6x`（无频率限制）

## 注意事项

- Alpha Vantage 免费 Key 每日25次配额，批量查询需分多天
- Finviz 新闻延迟15分钟，不影响基本面分析
- FMP 中国 ADR 财报需 Premium（腾讯音乐、百度等）
