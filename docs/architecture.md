# 架构与实现

## 报告章节（14个）

基本信息 → VIX → 季度EPS vs 预测 → 季度财务 → 年度财务 → K线
→ F-Score → 核心指标 → 分析师评级 → 机构持仓 → Finviz新闻 →
分红历史 → 综合评估 → 结论

## SEC 财报分析

### 分析流程

```
main() [sec_analysis/main.py]
  │
  ├─ 1. find_filing_files(TICKER)
  │     └─ 从 reports/sec_filings/TICKER/ 查找 .htm 文件
  │     └─ 排除 _index.html，按文件名排序
  │
  ├─ 2. 遍历每个文件 → analyze_single_filing()
  │     │
  │     ├─ 2.1 读取 HTML 文件
  │     ├─ 2.2 detect_filing_type() — 从文件名或 XBRL 标签检测
  │     ├─ 2.3 extract_fiscal_period() — 从文件名提取日期
  │     ├─ 2.4 获取分析器 — 10-K → Sec10KAnalyzer, 10-Q → Sec10QAnalyzer
  │     ├─ 2.5 analyzer.analyze(html_content)
  │     │     ├─ extract_text_from_html() — HTMLParser 提取纯文本
  │     │     ├─ find_body_start() — 跳过目录
  │     │     └─ extract_sections() — 按 EXPECTED_SECTIONS 提取章节
  │     ├─ 2.6 extract_key_metrics() — 正则提取财务指标
  │     ├─ 2.7 extract_cash_flows() — 正则提取现金流
  │     └─ 2.8 generate_extraction_md() — 生成中间 .md 文件
  │
  └─ 3. generate_analysis_report()
        ├─ 3.1 读取所有提取文件
        ├─ 3.2 生成年度趋势表格 + 3年CAGR
        ├─ 3.3 生成季度趋势表格
        ├─ 3.4 生成现金流趋势表格
        ├─ 3.5 翻译文本内容 → 中文
        └─ 3.6 输出最终报告
```

### 10-K vs 10-Q 差异化处理

| 章节 | 10-K 年报 | 10-Q 季报 |
|:---|:---|:---|
| business | Item 1 | - |
| risk_factors | Item 1A | Part II Item 1A |
| properties | Item 2 | - |
| legal_proceedings | Item 3 | - |
| mda | Item 7 | Item 2 |
| market_risk | Item 7A | Item 3 |
| financials | Item 8 | Item 1 |
| earnings | ✓ | ✓ |
| financial_statements_notes | ✓ | ✓ |
| controls_procedures | Item 9A | Item 4 |
| exhibits | Item 15 | Item 6 |

### 提取的财务指标

| 类别 | 指标 | 正则来源 |
|:---|:---|:---|
| 利润表 | 营收 | "Total revenues" |
| 利润表 | 成本 | "Cost of revenues" |
| 利润表 | 毛利润 | 营收 - 成本 |
| 利润表 | 营业利润 | "Operating income" |
| 利润表 | 净利润 | "Net income" |
| 利润表 | EPS | "earnings per share" |
| 资产负债表 | 总资产 | "Total assets" |
| 资产负债表 | 总负债 | "Total liabilities" |
| 资产负债表 | 股东权益 | 总资产 - 总负债 |
| 现金流 | 经营现金流 | "Net cash provided by operating activities" |
| 现金流 | 投资现金流 | "Net cash (used) provided by investing activities" |
| 现金流 | 筹资现金流 | "Net cash used by financing activities" |
| 现金流 | 资本支出 | "Capital expenditures" |
| 现金流 | 自由现金流 | 经营现金流 - 资本支出 |

> 详细字段说明见 [sec_analysis_fields.md](sec_analysis_fields.md)

### 输出目录结构

```
reports/sec_analysis/
├── TICKER/
│   ├── 10-K_FY2022.md       # 年报提取
│   ├── 10-Q_2022Q3.md       # 季报提取
│   └── ...
├── TICKER_analysis_YYYYMMDD.md  # 整合分析文档
```

## DCF 估值模型

### 核心公式

```
Owner Earnings = 净利润 + 折旧/摊销 - 维护性资本支出
内在价值 = Σ(OE_t / (1+r)^t) + 终值 / (1+r)^n
终值 = OE_n × (1+g) / (r - g)
```

### 参数

| 参数 | 默认值 | 说明 |
|:---|:---:|:---|
| `--growth` | 自动估算 | 预测增长率（%） |
| `--discount` | 10.0 | 折现率/WACC（%） |
| `--terminal-growth` | 3.0 | 永续增长率（%） |
| `--years` | 10 | 预测年数 |
| `--safety` | 0.25 | 安全边际（0-1） |

### 数据来源

| 优先级 | 数据源 | 用途 |
|:---:|:---|:---|
| 1 | SEC 分析数据 | 营收、净利润、D&A、CapEx、现金流 |
| 2 | FMP API | 补充缺失字段、实时行情（fallback） |

> 详细实现见 [dcf.md](dcf.md)

## edgar 10-K PDF 生成

| 步骤 | 技术 |
|:---|:---|
| CIK 查询 | SEC company_tickers.json API |
| 10-K 信息 | SEC submissions API |
| HTML 下载 | `/tmp/sec_download.sh` |
| 图片加载 | 本地 HTTP 服务器（reports/tmp/） |
| 英文 PDF | Chrome CDP + sec_to_pdf.js |
| 中文 PDF | Google Translate 页面 → Chrome CDP |

## 缓存策略

| 数据源 | TTL |
|:---|---:|
| VIX | 5 分钟 |
| FMP / Finviz / AV | 1 小时 |
| 富途行情/K线 | 不缓存 |

缓存目录: `~/.openclaw/cache/stock-god/`
格式: JSON，包含 `_ts`（时间戳）和 `_ttl`（过期秒数）字段。
