# 巴菲特式 DCF 现金流折现估值模型

## 概述

DCF（Discounted Cash Flow）是 Warren Buffett 最推崇的估值方法。本实现基于巴菲特的 **Owner Earnings（所有者收益）** 理念，而非传统会计净利润，更真实地反映企业为股东创造的现金。

### 核心公式

```
内在价值 = Σ [Owner_Earnings_t / (1+r)^t] + 终值 / (1+r)^n

其中：
  Owner Earnings = 净利润 + 折旧/摊销 - 维护性资本支出
  终值 = OE_n × (1+g) / (r - g)
  r = 折现率（WACC）
  g = 永续增长率
  n = 预测年数
```

## 模块架构

```
dcf.py
├── 数据获取层
│   ├── get_futu_snapshot()        # Futu OpenD 实时行情（股价、市值、流通股数）
│   └── (FMP 已移除)
├── 数据解析层
│   └── read_sec_analysis()        # 解析 SEC 分析 .md 文件（营收、净利润、折旧、CapEx 等）
├── 计算层
│   ├── calculate_owner_earnings() # 所有者收益计算
│   ├── estimate_growth_rate()     # 增长率估算
│   ├── dcf_valuation()            # DCF 估值核心
│   └── sensitivity_analysis()     # 敏感性分析
├── 报告层
│   └── generate_report()          # Markdown 报告生成
└── CLI 入口
    └── main()                     # argparse 命令行入口
```

### 数据来源设计

| 数据类型 | 来源 | 说明 |
|:---|:---|:---|
| 实时行情（股价、市值、流通股数） | **Futu OpenD** | 替代原 FMP `quote` 接口 |
| 历史财务数据（营收、净利润、折旧、CapEx、现金流） | **SEC 提取数据** | `sec_analysis` 生成的 .md 文件，已是唯一数据源 |
| 国债收益率（折现率参考） | 默认值 4.25% | 非核心参数，用户可通过 `--discount` 覆盖 |

**为什么移除 FMP？**
- SEC 提取已覆盖 DCF 所需的全部历史财务字段（折旧、CapEx、现金流等）
- Futu 提供实时行情，与 sec_analysis 共享同一数据源体系
- 减少外部 API 依赖，提高离线可用性和稳定性

## 调用流程

```
main(ticker, growth, discount, terminal_growth, years, safety)
  │
  ├─── 1. get_futu_snapshot(ticker)              # Futu 获取实时行情
  │        → dict | None  (price, market_cap, shares_outstanding)
  │
  ├─── 2. read_sec_analysis(ticker)              # 读取 SEC 提取数据
  │        → list[dict]  (10-K 年度数据，含营收/净利润/折旧/CapEx/现金流)
  │
  ├─── 3. 数据直接使用                            # SEC 已包含全部所需字段
  │     for each year in sec_data:
  │       calculate_owner_earnings(ni, dep, capex × 0.6)
  │
  ├─── 4. estimate_growth_rate(fcf_history, rev_history)
  │        → (fcf_cagr, revenue_cagr, recommended_growth)
  │        被 --growth 参数覆盖（如指定）
  │
  ├─── 5. dcf_valuation(base_oe, growth, discount, terminal_growth, years, shares, price)
  │        → dict  (估值结果)
  │
  ├─── 6. sensitivity_analysis(base_oe, shares, price, growth_rates[], discount_rates[], ...)
  │        → list[dict]  (敏感性矩阵)
  │
  └─── 7. generate_report(ticker, dcf_result, sensitivity, oe_data, quote, params, source)
           → str  (Markdown 报告)
           写入 ~/.openclaw/reports/dcf/{TICKER}_DCF_{YYYYMMDD}.md
```

## 函数签名与参数说明

### 数据获取函数

#### `get_futu_snapshot(ticker) → dict | None`

从 Futu OpenD 获取实时行情快照。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `ticker` | str | 股票代码，如 `"US.NKE"` 或 `"AAPL"`（自动添加市场前缀） |

返回字段：

```python
{
    "code": str,              # "US.NKE"
    "name": str,              # "NIKE, Inc."
    "last_price": float,      # 当前股价
    "pe_ttm": float,          # 市盈率
    "pb_ratio": float,        # 市净率
    "eps": float,             # 每股收益
    "net_asset_pershare": float,  # 每股净资产
    "total_mkt_val": float,   # 总市值
    "dividend_ttm": float,    # 每股分红
    "dividend_ratio_ttm": float,  # 股息率
    "issued_shares": float,   # 已发行股数
    "outstanding_shares": float,  # 流通股数
}
```

**降级处理**：Futu 不可用时返回 `None`，DCF 报告中行情部分显示"无法获取"。

### 数据解析函数

#### `read_sec_analysis(ticker) → list[dict]`

读取 `~/.openclaw/reports/sec_analysis/{SYMBOL}/*.md`，解析中文标签表格。

返回每条记录结构：

```python
{
    "source": str,           # 文件名，如 "10-K_FY2025.md"
    "type": str,             # "10-K"（已过滤，仅保留年报）
    "period": str,           # "FY2025"
    "revenue": int,          # 营收（百万 USD）
    "net_income": int,       # 净利润
    "operating_income": int, # 营业利润
    "fcf": int,              # 自由现金流
    "cfo": int,              # 经营现金流
    "depreciation": int,     # 折旧/摊销
    "capex": int,            # 资本支出
    "total_assets": int,     # 总资产
    "equity": int,           # 股东权益
    "eps": float,            # 每股收益
}
```

### 核心计算函数

#### `calculate_owner_earnings(net_income, depreciation, maintenance_capex) → float`

巴菲特所有者收益 = 净利润 + 折旧/摊销 - 维护性资本支出。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `net_income` | float | 净利润（百万 USD） |
| `depreciation` | float | 折旧/摊销（百万 USD） |
| `maintenance_capex` | float | 维护性资本支出（百万 USD） |

#### `estimate_growth_rate(historical_fcf, historical_revenue=None) → tuple`

基于历史数据估算增长率。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `historical_fcf` | list[float] | 历年 FCF 序列 |
| `historical_revenue` | list[float] \| None | 历年营收序列（可选） |

返回：`(fcf_cagr, revenue_cagr, recommended_growth)`

逻辑：取 FCF CAGR 和营收 CAGR 的较低正值，限制在 [2%, 15%] 区间。

#### `dcf_valuation(base_owner_earnings, growth_rate, discount_rate, terminal_growth_rate, projection_years, shares_outstanding, current_price) → dict`

DCF 估值核心函数。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `base_owner_earnings` | float | 基准年 OE（百万 USD） |
| `growth_rate` | float | 增长率，如 0.08 = 8% |
| `discount_rate` | float | 折现率，如 0.10 = 10% |
| `terminal_growth_rate` | float | 永续增长率，如 0.03 = 3% |
| `projection_years` | int | 预测年数 |
| `shares_outstanding` | float | 流通股数（百万） |
| `current_price` | float | 当前股价（USD） |

返回 `dict`：

```python
{
    "base_earnings": float,              # 基准 OE
    "growth_rate": float,                # 增长率
    "discount_rate": float,              # 折现率
    "terminal_growth": float,            # 永续增长率
    "projection_years": int,             # 预测年数
    "shares_outstanding": float,         # 流通股数
    "current_price": float,              # 当前股价
    "projections": list[dict],           # 逐年预测 [{year, projected_earnings, present_value, cumulative_pv}]
    "sum_pv_projections": float,         # 预测期现金流现值合计
    "terminal_value": float,             # 终值
    "terminal_pv": float,                # 终值现值
    "intrinsic_value": float,            # 企业内在价值（百万 USD）
    "intrinsic_value_per_share": float,  # 每股内在价值
    "margin_of_safety_25": float,        # 25% 安全边际价格
    "margin_of_safety_50": float,        # 50% 安全边际价格
    "upside_pct": float,                 # 上行空间 %
    "verdict": str,                      # 估值判断
    "terminal_pct": float,               # 终值占内在价值比例
}
```

估值判断逻辑：
- `upside_pct >= 50%` → "严重低估"
- `upside_pct >= 20%` → "低估"
- `upside_pct >= -20%` → "合理估值"
- `upside_pct >= -50%` → "高估"
- 其他 → "严重高估"

#### `sensitivity_analysis(base_owner_earnings, shares_outstanding, current_price, growth_rates, discount_rates, terminal_growth_rate, projection_years) → list[dict]`

敏感性分析：增长率 × 折现率矩阵。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `growth_rates` | list[float] | 增长率列表，如 [0.05, 0.07, 0.08, 0.09, 0.11] |
| `discount_rates` | list[float] | 折现率列表，如 [0.04, 0.06, 0.08, 0.10, 0.12] |

返回：`[{"growth": "8%", "r_6%": "$142", "r_8%": "$120", ...}, ...]`

### 报告生成函数

#### `generate_report(ticker, dcf_result, sensitivity, owner_earnings_data, quote, params, data_source="SEC") → str`

生成完整 Markdown 报告。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `dcf_result` | dict | `dcf_valuation()` 输出 |
| `sensitivity` | list[dict] | `sensitivity_analysis()` 输出 |
| `owner_earnings_data` | list[dict] | 历年 OE 明细 |
| `quote` | dict \| None | 行情数据 |
| `params` | dict | `{"growth", "discount", "terminal_growth", "years"}` |
| `data_source` | str | `"SEC"` 或 `"FMP"` |

输出路径：`~/.openclaw/reports/dcf/{TICKER}_DCF_{YYYYMMDD}.md`

| 项目 | 传统 DCF | 巴菲特 DCF（本模型） |
|:---|:---|:---|
| 现金流基础 | 自由现金流 (FCF) | Owner Earnings |
| CapEx 处理 | 全额扣除 | 只扣除维护性 CapEx（约60%） |
| 增长假设 | 激进预测 | 保守上限 15% |
| 折现率 | WACC（通常 8-12%） | 偏好 10%（简单透明） |
| 安全边际 | 可选 | **必须**（25-50%） |

### 为什么用 Owner Earnings 而非 FCF？

巴菲特在 1986 年致股东信中提出 Owner Earnings 概念：

> "Owner Earnings = (a) 报告收益 + (b) 折旧、摊销和其他非现金费用 - (c) 企业为保持长期竞争地位和单位产量所需的年均资本支出。"

关键区别：**区分维护性 CapEx 和增长性 CapEx**。

- **维护性 CapEx**：维持现有业务运转的必要支出（约占总 CapEx 的 60%）
- **增长性 CapEx**：用于扩张新业务的支出（不应从 Owner Earnings 中扣除）

传统 FCF 将全部 CapEx 扣除，会低估那些大力投资扩张的优质企业。

## 数据来源

```
数据源 1: SEC 分析数据（sec_analysis 提取的 .md 文件）— 历史财务数据
  ├── 营收、净利润、营业利润、毛利率、净利率
  ├── 经营现金流、投资现金流、筹资现金流
  ├── 折旧/摊销、资本支出 (CapEx)
  ├── 自由现金流
  ├── 总资产、总负债、股东权益
  └── 数据来自 SEC EDGAR 原始财报，经过审计

数据源 2: Futu OpenD — 实时行情数据
  ├── 股价、市值、流通股数
  ├── PE、PB、EPS（用于报告展示）
  └── 通过 OpenD 本地接口获取，无需 API Key

默认值: 国债收益率
  └── 4.25%（可被 --discount 参数覆盖，非核心依赖）
```

**为什么 Futu + SEC 而非 FMP？**
- SEC EDGAR 是上市公司法定披露的一手数据源，数据经过审计
- Futu OpenD 是本地行情接口，无需外部 API Key，稳定性高
- FMP 是第三方付费 API，增加外部依赖和成本
- 减少 API 调用，提高离线可用性

## 使用方法

### 基本用法

```bash
# 最简单的用法：自动获取数据并估值
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py US.NKE

# 指定股票代码（支持多种格式）
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py AAPL
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py QCOM
```

### 自定义参数

```bash
# 指定增长率 8%、折现率 10%、永续增长率 3%
python3 dcf.py US.NKE --growth 8 --discount 10 --terminal-growth 3

# 5 年预测期
python3 dcf.py AAPL --years 5

# 30% 安全边际
python3 dcf.py QCOM --safety 0.30
```

### 参数说明

| 参数 | 默认值 | 说明 |
|:---|:---:|:---|
| `ticker` | 必填 | 股票代码（US.NKE、AAPL 等） |
| `--growth` | 自动估算 | 预测增长率（%），不指定则基于历史数据估算 |
| `--discount` | 10.0 | 折现率 / WACC（%） |
| `--terminal-growth` | 3.0 | 永续增长率（%），通常 2-3% |
| `--years` | 10 | 预测年数，通常 5-10 年 |
| `--safety` | 0.25 | 安全边际（0-1），0.25 表示 25% 折扣 |

## 输出报告

报告保存至：`~/.openclaw/reports/dcf/{TICKER}_DCF_{YYYYMMDD}.md`

### 报告包含

1. **基本信息**：股价、市值、流通股数
2. **Owner Earnings 计算表**：历年净利润、折旧、CapEx、OE
3. **DCF 计算参数**：增长率、折现率、永续增长率
4. **预测现金流表**：逐年预测值和折现值
5. **估值结果**：
   - 企业内在价值（总计）
   - 每股内在价值
   - 上行空间百分比
   - 估值判断（严重低估/低估/合理/高估/严重高估）
   - 25% 和 50% 安全边际价格
6. **敏感性分析**：增长率 × 折现率 矩阵
7. **巴菲特投资检查清单**：估值、确定性、成长性、盈利质量

## 实现细节

### 1. Owner Earnings 计算

```python
def calculate_owner_earnings(net_income, depreciation, maintenance_capex):
    """
    巴菲特 Owner Earnings 定义：
    Owner Earnings = 净利润 + 折旧/摊销 - 维护性资本支出

    维护性资本支出 ≈ 总资本支出 × 60%
    （经验值：维持现有业务所需的最低资本支出约占总 CapEx 的 60%）
    """
    return net_income + depreciation - maintenance_capex
```

**为什么维护性 CapEx 用 60%？**

没有公开数据能精确区分维护性和增长性 CapEx。60% 是业界常用的经验比例：
- 对于成熟企业（低增长），维护性占比可能高达 80%
- 对于高增长企业（如科技公司），维护性占比可能低至 40%
- 60% 是一个保守的中间值

### 2. 增长率估算

```python
def estimate_growth_rate(historical_fcf, historical_revenue=None):
    """
    估算逻辑：
    1. 计算历史 FCF 的复合增长率 (CAGR)
    2. 计算历史营收的 CAGR
    3. 取两者较低值，上限 15%，下限 2%
    4. 无数据时默认 5%
    """
```

**巴菲特的增长率原则：**

- 宁可低估，不可高估
- 15% 是合理上限（超过此值的长期增长极为罕见）
- 2% 是下限（至少跑赢通胀）
- 用户可通过 `--growth` 覆盖自动估算

### 3. 折现率选择

巴菲特偏好使用 **10%** 作为折现率，理由：
- 足够简单透明
- 约等于美国股市长期年化回报率
- 对应机会成本（如果不能获得 10% 回报，不如买指数基金）

用户可通过 `--discount` 调整，建议范围 8-12%。

### 4. 终值计算

```python
terminal_value = OE_n × (1 + g) / (r - g)
terminal_pv = terminal_value / (1 + r)^n
```

**永续增长率 g 的选择：**
- 通常 2-3%（接近长期 GDP 增速）
- 不应超过折现率 r
- 越保守越好

### 5. 敏感性分析

生成增长率 × 折现率的估值矩阵，帮助理解：

**敏感性分析折现率范围**：`[3%, 4%, 5%, 8%, 10%, 12%, 15%]`
- 覆盖从保守（3%）到激进（15%）的折现率区间
- 低折现率（3-5%）适用于低利率环境或极稳定企业
- 中等折现率（8-10%）适用于大多数情况
- 高折现率（12-15%）适用于高风险或高利率环境
- 哪些假设变化对估值影响最大
- 在不同情景下的估值区间
- 终值占比是否过高（>70% 说明对长期假设过于敏感）

### 6. 安全边际

巴菲特的核心投资原则之一：

> "安全边际就是用 50 美分买 1 美元的东西。"

模型自动计算：
- **25% 安全边际价格**：每股内在价值 × 0.75
- **50% 安全边际价格**：每股内在价值 × 0.50

## 已知限制

| 限制 | 说明 | 缓解措施 |
|:---|:---|:---|
| 维护性 CapEx 估算 | 使用 60% 经验比例 | 可通过 `--growth` 调整补偿 |
| 周期性企业 | DCF 不适合强周期行业 | 结合 PE/PB 等指标综合判断 |
| 银行/金融企业 | 现金流定义不同 | 不建议使用本模型 |
| 高增长初创企业 | 历史数据少，增长不可预测 | 需人工调整增长率 |
| 未下载财报的公司 | SEC 数据需要先下载并分析 | 运行 `/stock-god download` + `/stock-god analyze` |

## 与 stock-god 其他工具配合使用

```
1. /stock-god download US.NKE    # 下载 SEC 财报
2. /stock-god analyze US.NKE     # 分析财报，生成提取数据
3. /stock-god dcf US.NKE         # 基于分析数据进行 DCF 估值
4. /stock-god report US.NKE      # 查看综合报告（含估值指标）
```

建议流程：先 `analyze` 生成历史数据，再 `dcf` 进行估值分析。

## FMP → Futu + SEC 迁移计划

### 迁移目标

移除 FMP API 依赖，改用 Futu OpenD（行情）+ SEC 提取数据（财务）完成 DCF 全部功能。

### 改动范围

| 文件 | 改动内容 |
|:---|:---|
| `dcf.py` | 移除 FMP 相关函数，新增 `get_futu_snapshot()`，简化数据合并逻辑 |
| `docs/dcf.md` | 更新文档（本文档） |

### 具体步骤

#### Step 1: 新增 `get_futu_snapshot()` 函数

复用 `report.py` 中已有的 Futu 接入模式：

```python
def get_futu_snapshot(ticker: str) -> dict | None:
    """从 Futu OpenD 获取实时行情快照"""
    # 动态加载 futuapi common 模块
    # 返回 last_price, total_mkt_val, outstanding_shares 等
```

#### Step 2: 修改 `main()` 数据获取流程

**Before（当前）：**
```
1. get_stock_quote(ticker)          # FMP 行情
2. read_sec_analysis(ticker)        # SEC 财务
3. get_fmp_cashflow(ticker)         # FMP 现金流（补充折旧/CapEx）
4. get_fmp_income(ticker)           # FMP 利润表（补充营收/净利润）
5. 数据合并（SEC 为主，FMP 补缺）
```

**After（迁移后）：**
```
1. get_futu_snapshot(ticker)        # Futu 行情（替代 FMP quote）
2. read_sec_analysis(ticker)        # SEC 财务（已是完整数据源）
3. 直接使用 SEC 数据（无需合并）
```

#### Step 3: 移除 FMP 函数

删除以下函数和常量：

| 移除项 | 说明 |
|:---|:---|
| `FMP_KEY` | API Key 常量 |
| `FMP_URL` | API URL 常量 |
| `get_fmp_json()` | FMP 通用请求 |
| `get_stock_quote()` | FMP 行情（被 Futu 替代） |
| `get_fmp_cashflow()` | FMP 现金流（SEC 已覆盖） |
| `get_fmp_income()` | FMP 利润表（SEC 已覆盖） |
| `get_fmp_key_metrics()` | FMP 关键指标（未使用） |
| `get_treasury_yield()` | 国债收益率（改为默认值） |

#### Step 4: 简化数据合并逻辑

当前 `main()` 中 line 609-697 的 FMP 合并逻辑可大幅简化：

```python
# Before: 复杂的 SEC + FMP 合并
fmp_by_year = {}
if fmp_cashflow:
    for i, cf in enumerate(fmp_cashflow):
        ...
        fmp_by_year[year_key] = {...}

for d in sec_data:
    fmp = fmp_by_year.get(period, {})
    if depreciation == 0 and fmp.get("depreciation"):
        depreciation = fmp["depreciation"]
    ...

# After: 直接使用 SEC 数据
for d in sec_data:
    depreciation = d.get("depreciation", 0)
    capex = d.get("capex", 0)
    ...
```

#### Step 5: 更新 `generate_report()` 参数

`data_source` 参数固定为 `"SEC"`，移除 `"FMP"` 分支。

### 验证方式

```bash
# 1. 对 NKE 运行 DCF
python3 ~/.openclaw/skills/stock-god/scripts/dcf.py US.NKE

# 2. 验证报告中：
#    - 行情数据来自 Futu（股价、市值）
#    - 历史财务数据来自 SEC（折旧、CapEx 完整）
#    - 无 FMP API 调用日志

# 3. 验证 Futu 降级场景（关闭 Futu OpenD）
#    → 报告中行情部分显示"无法获取"，其余正常
```

## 参考资料

- Warren Buffett, Berkshire Hathaway 1986 Annual Report (Owner Earnings 定义)
- Warren Buffett, "The Essays of Warren Buffett" (安全边际原则)
- Aswath Damodaran, "Investment Valuation" (DCF 方法论)
