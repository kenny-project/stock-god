# stock-god

美股/港股价值投资数据查询与分析工具集，以 OpenClaw skill 形式运行。

## 功能

| 命令 | 功能 |
|:---|:---|
| `screener` | 价值股筛选（Futu粗筛→FMP精筛→Finviz补充→综合评分） |
| `report` | 个股深度分析报告（14个章节） |
| `edgar` | SEC 10-K 年报 PDF（英文+中文翻译版） |
| `list` / `download` | SEC 财报列出与下载（支持中文名） |
| `analyze` | SEC 财报分析（10-K/10-Q 差异化处理） |
| `dcf` | 巴菲特式 DCF 现金流折现估值 |
| `vix` | VIX 恐慌指数查询 |

## 运行

所有脚本通过 `python3` 直接运行，工作目录为项目根目录。

### report — 个股深度报告

```bash
# 美股
python3 scripts/report.py US.NKE
# 港股
python3 scripts/report.py HK.00700
# 禁用缓存，强制刷新
python3 scripts/report.py US.NKE --no-cache
# 指定输出路径
python3 scripts/report.py US.NKE -o reports/nke_report.txt
# 自定义缓存时间（秒）
python3 scripts/report.py US.NKE --cache-ttl 1800
```

### screener — 价值股筛选

```bash
# 默认：输出前20只，FMP精筛上限100
python3 scripts/screener.py
# 精筛前50只，输出前10
python3 scripts/screener.py --limit 50 --top 10
# 强制刷新缓存
python3 scripts/screener.py --no-cache
```

### sec_filings — SEC 财报列出与下载

```bash
# 列出所有财报
python3 scripts/sec_filings.py list US.NKE
# 只列出 10-K 和 10-Q
python3 scripts/sec_filings.py list US.NKE --form 10-K,10-Q
# 限制显示条数
python3 scripts/sec_filings.py list US.NKE --limit 20
# 支持中文名
python3 scripts/sec_filings.py list 英特尔

# 下载最新 10-K
python3 scripts/sec_filings.py download US.NKE
# 下载指定财年
python3 scripts/sec_filings.py download US.NKE --fy 2024
# 下载近3年财报
python3 scripts/sec_filings.py download US.NKE --years 3
# 下载最新 10-Q
python3 scripts/sec_filings.py download US.NKE --form 10-Q
# 下载 8-K
python3 scripts/sec_filings.py download US.NKE --form 8-K
# 按 SEC URL 直接下载（含附件+CSS/JS+图片）
python3 scripts/sec_filings.py download --url "https://www.sec.gov/Archives/edgar/data/..."
# 强制重新下载
python3 scripts/sec_filings.py download US.NKE --force
```

### analyze — SEC 财报分析

需先用 `sec_filings.py download` 下载财报到本地。

```bash
# 分析最新财报（默认）
python3 scripts/sec_analysis.py MSFT
# 分析所有已下载的财报
python3 scripts/sec_analysis.py MSFT --all
# 只分析 10-K 年报
python3 scripts/sec_analysis.py MSFT --all --form 10-K
# 只分析 10-Q 季报
python3 scripts/sec_analysis.py MSFT --all --form 10-Q
# 分析指定文件
python3 scripts/sec_analysis.py MSFT --file msft-10q_20220930.htm
# 强制重新提取（覆盖已有）
python3 scripts/sec_analysis.py MSFT --all --force
```

### dcf — DCF 现金流折现估值

需先用 `sec_analysis.py` 生成分析数据。

```bash
# 基本用法（自动估算增长率）
python3 scripts/dcf.py US.NKE
# 指定增长率和折现率
python3 scripts/dcf.py AAPL --growth 8 --discount 10
# 5年预测期 + 30%安全边际
python3 scripts/dcf.py QCOM --years 5 --safety 0.30
# 调整永续增长率
python3 scripts/dcf.py MSFT --terminal-growth 2.5
```

### edgar — 10-K PDF 生成

```bash
# 英文原版 + 中文翻译版 PDF
python3 scripts/edgar_10k.py US.NKE
# 指定财年
python3 scripts/edgar_10k.py NKE --fiscal-year 2024
```

## 数据源

| 优先级 | 数据源 | 用途 | 缓存 |
|:---:|:---|:---|:---|
| 1 | Futu OpenD | 实时行情、K线、snapshot | 不缓存 |
| 2 | FMP API | 财务数据（营收/净利润/Key Metrics/分红）| 1小时 |
| 3 | Finviz | 新闻、分析师评级、机构持仓、做空 | 1小时 |
| 4 | 腾讯财经 | 备用实时行情 | 不缓存 |
| 5 | Alpha Vantage | EPS surprise、EARNINGS | 1小时 |

## 代码结构

```
scripts/
├── report.py            ← 个股深度报告（14章节）
├── screener.py          ← 价值股筛选器
├── sec_filings.py       ← SEC 财报列出与下载
├── sec_analysis/        ← SEC 财报分析（模块化）
│   ├── base.py          # 基础抽象类
│   ├── extraction.py    # 共用提取逻辑
│   ├── sec_10k.py       # 10-K 特定模式
│   ├── sec_10q.py       # 10-Q 特定模式
│   ├── report.py        # 报告生成
│   └── main.py          # CLI 入口
├── dcf.py               ← 巴菲特式 DCF 估值
├── finviz.py            ← Finviz 数据获取
├── edgar_10k.py         ← 10-K PDF 生成（Chrome CDP）
├── download_filing.py   ← 完整 filing 下载
├── help.py              ← 命令手册
├── common.py            ← 共享数据获取（预留）
└── monitor.py           ← 持仓监控（开发中）
```

## 报告输出

所有报告输出到 `reports/` 目录：

| 类型 | 路径 |
|:---|:---|
| 个股报告 | `reports/{SYMBOL}_{timestamp}.txt` |
| 选股报告 | `reports/value_screener_{timestamp}.md` |
| SEC 分析 | `reports/sec_analysis/{TICKER}/` |
| DCF 报告 | `reports/dcf/{TICKER}_DCF_{date}.md` |
| 10-K PDF | `reports/{TICKER}_10K_FY{YYYY}.pdf` |

## 已知限制

- Futu `get_stock_filter` 的 `MARKET_VAL` 范围筛选返回错误码 2159，需回退到 cached 数据
- FMP 免费账号有 429 限流，脚本中有指数退避重试（1s/2s/4s）
- Alpha Vantage 免费 Key 每日 25 次配额
- Finviz 新闻延迟 15 分钟
- SEC 财报解析使用正则匹配，非 XBRL 结构化解析

## 深度文档

- [架构与实现](docs/architecture.md) — SEC 分析流程、DCF 公式、提取指标详解
- [DCF 估值模型](docs/dcf.md) — Owner Earnings 计算、敏感性分析
- [SEC 分析字段](docs/sec_analysis_fields.md) — 提取字段说明
