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
| `/stock-god fees` | 手续费统计报告（按月/年/总汇总）|
| `/stock-god fees --year 2025` | 查询指定年份的手续费 |

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



### 腾讯财经 API — PE/PB/市值/换手率/涨跌停

HTTP GET，GBK 编码，`~` 分隔 88 个字段，不封IP。

```python
import urllib.request

def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """
    批量拉取腾讯财经实时行情。
    支持 A 股 / 美股 / 港股，自动识别前缀。

    codes 格式:
      A 股: ["688017", "SH688017", "SZ000001", "BJ832000"]
      美股: ["AAPL", "usAAPL", "TSLA", "NVDA"]
      港股: ["00700", "hk00700"]

    返回: {code: {name, price, market, pe_ttm, pb, mcap_yi, currency, ...}}
    """
    def _to_prefixed(c: str) -> tuple[str, str]:
        """返回 (前缀, 归一化代码), 如 ("us", "AAPL")"""
        c = c.strip()
        upper = c.upper()
        if upper.startswith("SH"):
            return "sh", c[2:]
        if upper.startswith("SZ"):
            return "sz", c[2:]
        if upper.startswith("BJ"):
            return "bj", c[2:]
        if upper.startswith("US"):
            return "us", c[2:]
        if upper.startswith("HK"):
            return "hk", c[2:]
        if c.startswith(("6", "9")):
            return "sh", c
        if c.startswith("8"):
            return "bj", c
        if c.isdigit() and len(c) == 5:
            return "hk", c
        if c.isalpha():
            return "us", c
        return "sz", c

    prefixed = []
    code_map = {}  # prefixed_code -> original_code
    for c in codes:
        pfx, raw = _to_prefixed(c)
        full = pfx + raw
        prefixed.append(full)
        code_map[full] = raw

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk")

    MARKET_MAP = {"sh": "SH", "sz": "SZ", "bj": "BJ", "us": "US", "hk": "HK"}

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 40:
            continue
        pfx = key[:2]
        market = MARKET_MAP.get(pfx, pfx.upper())
        raw_code = code_map.get(key, key[2:])

        q = {
            "name":         vals[1],
            "price":        float(vals[3]) if vals[3] else 0,
            "last_close":   float(vals[4]) if vals[4] else 0,
            "open":         float(vals[5]) if vals[5] else 0,
            "change_amt":   float(vals[31]) if vals[31] else 0,
            "change_pct":   float(vals[32]) if vals[32] else 0,
            "high":         float(vals[33]) if vals[33] else 0,
            "low":          float(vals[34]) if vals[34] else 0,
            "amount_wan":   float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm":       float(vals[39]) if vals[39] else 0,
            "market":       market,
        }

        if market == "US":
            # 美股字段映射不同于 A 股: [46]=英文公司名, [50]=总股本(万股), [51]=EPS
            q["currency"]  = vals[35] if len(vals) > 35 else "USD"
            q["amplitude_pct"] = float(vals[43]) if vals[43] else 0
            q["mcap_yi"]   = float(vals[44]) if vals[44] else 0
            q["float_mcap_yi"] = float(vals[45]) if vals[45] else 0
            q["pb"]        = 0  # 美股 PB 在本接口不可用 (field 46 为英文名)
            q["total_shares_wan"] = float(vals[50]) if len(vals) > 50 and vals[50] else 0
            q["eps"]       = float(vals[51]) if len(vals) > 51 and vals[51] else 0
        elif market == "HK":
            # 港股字段映射: [46]=英文名, [50]=总股本(万股), [54]=PE(静)
            q["currency"]  = "HKD"
            q["amplitude_pct"] = float(vals[43]) if vals[43] else 0
            q["mcap_yi"]   = float(vals[44]) if vals[44] else 0
            q["float_mcap_yi"] = float(vals[45]) if vals[45] else 0
            q["pb"]        = 0  # 港股 PB 在本接口不可用
            q["total_shares_wan"] = float(vals[50]) if len(vals) > 50 and vals[50] else 0
            q["pe_static"] = float(vals[54]) if len(vals) > 54 and vals[54] else 0
        else:
            # A 股: 标准字段映射
            q["amplitude_pct"] = float(vals[43]) if vals[43] else 0
            q["mcap_yi"]   = float(vals[44]) if vals[44] else 0
            q["float_mcap_yi"] = float(vals[45]) if vals[45] else 0
            q["pb"]        = float(vals[46]) if vals[46] else 0
            q["limit_up"]  = float(vals[47]) if vals[47] else 0
            q["limit_down"]= float(vals[48]) if vals[48] else 0
            q["vol_ratio"] = float(vals[49]) if vals[49] else 0
            q["pe_static"] = float(vals[52]) if vals[52] else 0

        result[raw_code] = q
    return result

# 用法 — A 股
quotes = tencent_quote(["688017", "300476", "002463"])
for code, q in quotes.items():
    print(f"{q['name']}({code}): {q['price']}元 PE={q['pe_ttm']} PB={q['pb']} 市值={q['mcap_yi']}亿")

# 用法 — 美股 + 港股 + A 股混合查询
quotes = tencent_quote(["688017", "AAPL", "00700", "NVDA", "hk00700"])
for code, q in quotes.items():
    cur = q.get("currency", "CNY")
    pb = q["pb"] if q["pb"] else "N/A"
    print(f"[{q['market']}] {q['name']}({code}): {q['price']}{cur} PE={q['pe_ttm']} PB={pb} 市值={q['mcap_yi']}亿")
```

#### 腾讯财经字段索引速查（实测校准 2026-05-15）

| 索引 | A 股含义 | 美股含义 | 港股含义 |
|------|---------|---------|---------|
| 1 | 名称 | 名称 | 名称 |
| 3 | 当前价 | 当前价 | 当前价 |
| 4 | 昨收 | 昨收 | 昨收 |
| 5 | 今开 | 今开 | 今开 |
| 9-18 | 买一~买五(价+量) | 无效(全0) | 无效(全0) |
| 19-28 | 卖一~卖五(价+量) | 无效(全0) | 无效(全0) |
| 31 | 涨跌额 | 涨跌额 | 涨跌额 |
| 32 | 涨跌幅% | 涨跌幅% | 涨跌幅% |
| 33 | 最高 | 最高 | 最高 |
| 34 | 最低 | 最低 | 最低 |
| 35 | 价格/成交量/成交额 | **货币(USD)** | **货币(HKD)** |
| 37 | 成交额(万) | 成交额 | 成交额 |
| 38 | 换手率% | 换手率% | 换手率% |
| **39** | **PE(TTM)** | **PE(TTM)** | **PE(TTM)** |
| 43 | 振幅%(不是PB!) | 振幅% | 振幅% |
| 44 | **总市值(亿)** | **总市值(亿)** | **总市值(亿)** |
| 45 | **流通市值(亿)** | **流通市值(亿)** | **流通市值(亿)** |
| **46** | **PB(市净率)** | **英文公司名**(不可用) | **英文公司名**(不可用) |
| 47 | **涨停价** | 无效 | 无效 |
| 48 | **跌停价** | 无效 | 无效 |
| 49 | 量比 | 无效 | 无效 |
| **52** | **PE(静)** | 无效 | 无效 |
| 50 | — | 总股本(万股) | 总股本(万股) |
| 51 | — | 每股收益 | — |
| 54 | — | — | PE(静) |

> **踩坑提醒：**
> - 网上很多教程把索引 43 写成 PB，实测是振幅%。**PB 在索引 46，但仅 A 股可用**。
> - 美股/港股的 [46] 返回英文公司全名，不是 PB。美股/港股 PB 需走其他接口。
> - 美股/港股无涨跌停（[47]/[48] 无效），无量比（[49] 无效）。
> - 美股无五档盘口（[9-28] 全 0）。

## FAQ

### Q: 腾讯 API 支持美股/港股吗？
A: **支持**，用 `us` / `hk` 前缀：`usAAPL`、`hk00700`。但字段映射与 A 股不同——**PB（索引46）对美股/港股返回英文公司名而非市净率**，涨停/跌停/量比无效。`tencent_quote()` 已自动处理这些差异。美股/港股的 PB 需走其他接口。