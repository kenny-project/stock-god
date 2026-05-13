# SEC 财报分析字段说明文档

> 本文档描述 `/stock-god analyze` 命令从 SEC 财报中提取的所有字段。

## 概述

`/stock-god analyze` 命令分析下载的 SEC 财报（10-K/10-Q），提取关键章节和财务指标，生成结构化分析报告。

每个提取文件包含三大部分：
1. **财务数据表格** - 核心财务指标
2. **现金流明细** - 现金流量表关键数据
3. **章节内容** - SEC 财报原文章节

---

## 1. 财务数据表格

从财报中正则提取或计算得出的核心财务指标。

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 说明 |
|:---|:---|:---:|:---|:---|
| 营收 | Revenue | 数值 | 正则匹配 "Total revenues" | 公司总收入，单位：百万美元 |
| 主营收 | Primary Revenue | 数值 | 近似为营收 | 主营业务收入（暂用总营收近似） |
| 主营成本 | Primary Cost | 数值 | 近似为成本 | 主营业务成本（暂用总成本近似） |
| 主营利润 | Primary Profit | 数值 | 近似为毛利润 | 主营业务毛利润（暂用毛利润近似） |
| 主营利润率 | Primary Margin | 百分比 | 近似为毛利率 | 主营业务盈利能力（暂用毛利率近似） |
| 成本 | Cost of revenues | 数值 | 正则匹配 "Cost of revenues" | 营业成本，用于计算毛利润 |
| 毛利润 | Gross Profit | 数值 | **计算**：营收 - 成本 | 反映产品/服务的盈利能力 |
| 毛利率 | Gross Margin | 百分比 | **计算**：毛利润 / 营收 × 100% | 反映定价能力和成本控制 |
| 营业利润 | Operating Income | 数值 | 正则匹配 "Operating income" | 扣除运营费用后的利润 |
| 营业利润率 | Operating Margin | 百分比 | **计算**：营业利润 / 营收 × 100% | 反映运营效率 |
| 净利润 | Net Income | 数值 | 正则匹配 "Net income" | 最终利润（扣除税费、利息等） |
| 净利率 | Net Margin | 百分比 | **计算**：净利润 / 营收 × 100% | 反映综合盈利能力 |
| 每股收益 | EPS | 金额 | 正则匹配 "earnings per share" | 每股普通股的盈利 |
| 总资产 | Total Assets | 数值 | 正则匹配 "Total assets" | 公司拥有的全部资产 |
| 总负债 | Total Liabilities | 数值 | 正则匹配 "Total liabilities" | 公司承担的全部债务 |
| 股东权益 | Stockholders' Equity | 数值 | **计算**：总资产 - 总负债 | 股东在公司中的权益 |
| ROE | Return on Equity | 百分比 | **已实现**：净利润 / 股东权益 × 100% (extraction.py) | 净资产收益率，衡量股东回报 |

### 计算字段说明

- **毛利润** = 营收 - 成本
- **毛利率** = 毛利润 / 营收 × 100%
- **营业利润率** = 营业利润 / 营收 × 100%
- **净利率** = 净利润 / 营收 × 100%
- **股东权益** = 总资产 - 总负债
- **ROE** = 净利润 / 股东权益 × 100%（已实现：extraction.py）
- **主营利润** = 主营收入 - 主营成本（近似为毛利润）
- **主营利润率** = 主营利润 / 主营收入 × 100%（近似为毛利率）

---

## 2. 现金流明细

从现金流量表中提取的关键数据。

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 说明 |
|:---|:---|:---:|:---|:---|
| 经营现金流 | Operating Cash Flow | 数值 | 正则匹配 "Net cash provided by operating activities" | 经营活动产生的现金流入 |
| 投资现金流 | Investing Cash Flow | 数值 | 正则匹配 "Net cash (used) provided by investing activities" | 投资活动的现金流动 |
| 筹资现金流 | Financing Cash Flow | 数值 | 正则匹配 "Net cash used by financing activities" | 筹资活动的现金流动 |
| 资本支出 | CapEx | 数值 | 正则匹配 "Capital expenditures" | 购买固定资产的支出（内部使用） |
| 自由现金流 | Free Cash Flow | 数值 | **计算**：经营现金流 - 资本支出 | 公司可自由分配的现金 |

### 计算字段说明

- **自由现金流** = 经营现金流 - |资本支出|

### 负数表示

- 正数：`$X,XXXM`
- 负数：`($X,XXXM)` （括号表示负数）

---

## 3. 成长性指标

价值投资不追高增长，但需要确认公司还在成长。成长性要和盈利质量一起看，高增长但毛利率下降 = 以价换量，不健康。

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 说明 |
|:---|:---|:---:|:---|:---|
| 营收增长率 | Revenue Growth Rate | 百分比 | **已实现**：(本期营收 - 上期营收) / 上期营收 × 100% (report.py) | 收入是否持续增长 |
| 净利润增长率 | Net Income Growth Rate | 百分比 | **已实现**：(本期净利润 - 上期净利润) / 上期净利润 × 100% (report.py) | 利润增速是否匹配或超过营收增速 |
| 自由现金流增长率 | FCF Growth Rate | 百分比 | **已实现**：(本期FCF - 上期FCF) / 上期FCF × 100% (report.py) | 真金白银的增速，比利润更难造假 |
| 主营收入增长率 | Primary Revenue Growth Rate | 百分比 | 近似为营收增长率 | 核心业务的增长（暂用营收增长率近似） |

### 成长性分析要点

- 营收增长率 vs 净利润增长率：利润增速应 ≥ 营收增速
- 自由现金流增长率：比净利润增长更可靠
- 主营收入增长率：排除非经常性收入干扰
- **警戒信号**：高增长但毛利率下降 = 以价换量，不健康

---

## 4. 财务健康指标

格雷厄姆说"投资的首要原则是不要亏损"，财务安全是底线。

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 警戒线 | 说明 |
|:---|:---|:---:|:---|:---:|:---|
| 资产负债率 | Debt-to-Asset Ratio | 百分比 | **计算**：总负债 / 总资产 × 100% | >70% | 负债占总资产比例 |
| 流动比率 | Current Ratio | 倍数 | **已实现**：流动资产 / 流动负债 (extraction.py) | <1.0 | 短期偿债能力 |
| 速动比率 | Quick Ratio | 倍数 | **已实现**：(流动资产 - 存货) / 流动负债 (extraction.py) | <0.5 | 更严格的短期偿债能力 |
| 净现金 | Net Cash | 数值 | **已实现**：现金及等价物 - 总负债 (extraction.py) | 负数 | 净现金/净负债 |
| 利息覆盖倍数 | Interest Coverage Ratio | 倍数 | **已实现**：营业利润 / 利息费用 (extraction.py) | <3x | 偿债压力指标 |
| 商誉占比 | Goodwill to Assets | 百分比 | **已实现**：商誉 / 总资产 × 100% (extraction.py) | >20% | 高商誉 = 减值风险 |

### 财务健康评估

| 指标 | 健康 | 警戒 | 危险 |
|:---|:---:|:---:|:---:|
| 资产负债率 | <50% | 50-70% | >70% |
| 流动比率 | >2.0 | 1.0-2.0 | <1.0 |
| 速动比率 | >1.0 | 0.5-1.0 | <0.5 |
| 利息覆盖倍数 | >5x | 3-5x | <3x |
| 商誉占比 | <10% | 10-20% | >20% |

### 所需新增字段

以下字段需要从财报中提取：

| 字段名 | 英文名 | 提取来源 |
|:---|:---|:---|
| 流动资产 | Current Assets | 资产负债表 |
| 流动负债 | Current Liabilities | 资产负债表 |
| 存货 | Inventories | 资产负债表 |
| 现金及等价物 | Cash and Cash Equivalents | 资产负债表 |
| 利息费用 | Interest Expense | 利润表 / 附注：Debt |
| 商誉 | Goodwill | 资产负债表 / 附注：Goodwill |

---

## 5. 现金流质量指标

现金流是价值投资最看重的维度之一。利润可以调节，现金流很难造假。

**黄金法则**：连续5年自由现金流为正 + CFO ≥ 净利润 = 利润质量可信。

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 说明 |
|:---|:---|:---:|:---|:---|
| 经营现金流 | Operating Cash Flow (CFO) | 数值 | 正则匹配 | 核心业务产生的现金，必须为正 |
| 自由现金流 | Free Cash Flow (FCF) | 数值 | **计算**：CFO - CapEx | 价值投资最核心的指标之一 |
| 资本支出 | CapEx | 数值 | 正则匹配 | 高资本支出的公司盈利质量打折 |
| CFO vs 净利润 | CFO to Net Income | 倍数 | **已实现**：CFO / 净利润 (main.py) | 长期看应 ≥ 1.0 |
| 分红比例 | Dividend Payout Ratio | 百分比 | **待实现**：现金分红 / 净利润 × 100% | 越高说明利润越真实 |
| FCF 利润率 | FCF Margin | 百分比 | **已实现**：FCF / 营收 × 100% (main.py) | 自由现金流占营收比例 |
| CapEx/营收 | CapEx to Revenue | 百分比 | **已实现**：CapEx / 营收 × 100% (main.py) | 资本支出强度 |

### 现金流质量评估

| 指标 | 优秀 | 一般 | 警戒 |
|:---|:---:|:---:|:---:|
| CFO 连续5年 | 正 | 波动 | 负 |
| FCF 连续5年 | 正 | 波动 | 负 |
| CFO/净利润 | >1.0 | 0.8-1.0 | <0.8 |
| 分红比例 | >30% | 10-30% | <10% |

### 所需新增字段

| 字段名 | 英文名 | 提取来源 |
|:---|:---|:---|
| 现金分红 | Cash Dividends Paid | Futu `dividend_per_share` × 总股本 / 筹资现金流附注 |

---

## 6. 估值指标

价值投资的核心是"好公司，好价格"。估值指标帮助判断当前价格是否合理。

### 6.1 相对估值指标

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 说明 |
|:---|:---|:---:|:---|:---|
| 市盈率 | PE Ratio (TTM) | 倍数 | **已实现**：Futu `pe_ttm` (report.py) | 最常用的估值指标 |
| 市净率 | PB Ratio | 倍数 | **已实现**：Futu `pb_ratio` (report.py) | 适合重资产行业 |
| 市销率 | PS Ratio | 倍数 | **已实现**：Futu 市值 / SEC 营收 (report.py) | 适合高成长、亏损公司 |
| PEG | PEG Ratio | 倍数 | **已实现**：PE / 净利润增长率 (report.py) | PE 相对盈利增长，<1 可能低估 |
| EV/EBITDA | EV/EBITDA | 倍数 | **已实现**：(市值 + 总负债 - 现金) / SEC 数据 (report.py) | 排除资本结构差异 |
| 股息率 | Dividend Yield | 百分比 | **已实现**：Futu `dividend_ratio_ttm` (report.py) | 分红回报率 |
| 自由现金流收益率 | FCF Yield | 百分比 | **已实现**：SEC FCF / Futu 市值 (report.py) | 真金白银的回报率 |
| 安全边际 | Margin of Safety | 百分比 | 待实现：(内在价值 - 当前价格) / 内在价值 × 100% | 需要 DCF 内在价值，当前通过 DCF 工具单独计算 |

### 6.2 绝对估值指标

| 字段名 | 英文名 | 数据类型 | 来源/计算方式 | 说明 |
|:---|:---|:---:|:---|:---|
| 市值 | Market Cap | 数值 | **已实现**：Futu `total_mkt_val` (report.py) | 公司总市值 |
| 企业价值 | Enterprise Value (EV) | 数值 | **已实现**：市值 + 总负债 - 现金 (report.py) | 收购一家公司的理论成本 |
| 每股净资产 | Book Value Per Share | 数值 | **已实现**：Futu `net_asset_pershare` (report.py) | 每股账面价值 |
| 每股收益 | EPS (TTM) | 金额 | **已实现**：Futu `eps` (report.py) | 过去12个月每股盈利 |
| 每股分红 | Dividend Per Share | 金额 | **已实现**：Futu `dividend_per_share` (report.py) | 每股分红金额 |

### 6.3 估值倍数参考区间

| 指标 | 低估 | 合理 | 高估 | 说明 |
|:---|:---:|:---:|:---:|:---|
| PE (TTM) | <15 | 15-25 | >25 | 因行业而异 |
| PB | <1.5 | 1.5-3 | >3 | 重资产行业参考 |
| PS | <2 | 2-5 | >5 | 高成长行业参考 |
| PEG | <1 | 1-2 | >2 | <1 可能被低估 |
| EV/EBITDA | <8 | 8-15 | >15 | 跨行业可比性好 |
| 股息率 | >3% | 1-3% | <1% | 高股息策略参考 |
| FCF Yield | >5% | 2-5% | <2% | 价值投资核心指标 |

### 6.4 估值分析要点

**PE 估值**：
- 静态 PE vs 动态 PE（TTM vs Forward）
- 与历史 PE 区间比较（PE 百分位）
- 与同行业 PE 比较

**PB 估值**：
- 适合银行、地产等重资产行业
- 轻资产公司 PB 参考价值低
- 关注 ROE 与 PB 的匹配（PB = ROE × PE）

**EV/EBITDA**：
- 排除不同资本结构的影响
- 跨行业、跨国比较更公平
- 适合高负债公司估值

**FCF Yield**：
- 价值投资最看重的估值指标
- 比 PE 更难造假
- 连续5年 FCF Yield > 5% = 优质标的

### 6.5 数据来源

| 字段名 | 英文名 | 数据来源 |
|:---|:---|:---|
| 股价 | Stock Price | Futu OpenD `last_price` |
| PE (TTM) | PE Ratio | Futu OpenD `pe_ttm` |
| PB | PB Ratio | Futu OpenD `pb_ratio` |
| EPS | Earnings Per Share | Futu OpenD `eps` |
| 每股净资产 | Book Value Per Share | Futu OpenD `net_asset_pershare` |
| 股息率 | Dividend Yield | Futu OpenD `dividend_ratio_ttm` |
| 每股分红 | Dividend Per Share | Futu OpenD `dividend_per_share` |
| 市值 | Market Cap | Futu OpenD `total_mkt_val` |
| PS / EV/EBITDA / FCF Yield / PEG | 综合计算 | Futu 市值 + SEC 财务数据 |

---

## 7. 详细利润项目（大模型提取）

以下利润项目格式不统一，适合用大模型（LLM）从财报原文中提取，而非正则匹配。

### 7.1 投资相关收益

| 字段名 | 英文名 | 说明 | 提取来源 |
|:---|:---|:---|:---|
| 投资收益 | Investment Income | 股权投资、债券投资收益 | 利润表 / 附注：Investments |
| 利息收入 | Interest Income | 银行存款、短期投资利息 | 利润表 |
| 利息支出 | Interest Expense | 债务利息支出 | 利润表 |
| 股息收入 | Dividend Income | 持有股票的分红收入 | 利润表 / 附注：Investments |
| 联营企业收益 | Equity in Earnings of Affiliates | 按权益法核算的投资收益 | 利润表 |

### 7.2 资产处置与减值

| 字段名 | 英文名 | 说明 | 提取来源 |
|:---|:---|:---|:---|
| 资产处置收益 | Gain/Loss on Sale of Assets | 卖厂房、设备等固定资产的损益 | 利润表 / 附注：PP&E |
| 商誉减值 | Goodwill Impairment | 商誉减值损失 | 利润表 / 附注：Goodwill |
| 无形资产减值 | Intangible Asset Impairment | 专利、商标等减值 | 利润表 / 附注：Intangible Assets |
| 长期资产减值 | Long-lived Asset Impairment | 固定资产减值 | 利润表 / 附注：PP&E |
| 投资减值 | Investment Impairment | 投资证券减值 | 利润表 / 附注：Investments |

### 7.3 汇兑与衍生品

| 字段名 | 英文名 | 说明 | 提取来源 |
|:---|:---|:---|:---|
| 汇兑损益 | Foreign Currency Exchange Gain/Loss | 外币兑换损益 | 利润表 / 附注：Foreign Currency |
| 衍生品收益 | Derivative Gain/Loss | 金融衍生品公允价值变动 | 利润表 / 附注：Derivatives |
| 套期保值收益 | Hedging Gain/Loss | 套期保值有效性部分 | 利润表 / 附注：Derivatives |

### 7.4 重组与特殊项目

| 字段名 | 英文名 | 说明 | 提取来源 |
|:---|:---|:---|:---|
| 重组费用 | Restructuring Charges | 裁员、关闭工厂等重组成本 | 利润表 / 附注：Restructuring |
| 诉讼和解 | Legal Settlements | 法律诉讼和解费用或收入 | 利润表 / 附注：Legal Proceedings |
| 环境整治 | Environmental Remediation | 环境污染治理费用 | 利润表 / 附注：Environmental |
| 资产报废损失 | Asset Retirement Obligations | 资产报废相关费用 | 利润表 / 附注：ARO |

### 7.5 政府与税务

| 字段名 | 英文名 | 说明 | 提取来源 |
|:---|:---|:---|:---|
| 政府补助 | Government Grants | 政府补贴收入 | 利润表 / 附注：Government |
| 税收返还 | Tax Refunds | 退税收入 | 利润表 / 附注：Income Tax |
| 税率变动影响 | Tax Rate Change Impact | 税法变动对利润的影响 | 附注：Income Tax |

### 7.6 其他非经常性项目

| 字段名 | 英文名 | 说明 | 提取来源 |
|:---|:---|:---|:---|
| 保险理赔 | Insurance Proceeds | 保险赔偿收入 | 利润表 / 附注：Insurance |
| 债务重组收益 | Debt Restructuring Gain | 债务重组产生的收益 | 利润表 / 附注：Debt |
| 担保损失 | Guarantee Losses | 担保相关损失 | 附注：Guarantees |
| 或有对价调整 | Contingent Consideration Adjustments | 并购或有对价公允价值变动 | 附注：Business Combinations |
| 其他收入/费用 | Other Income/Expenses | 其他杂项收入或费用 | 利润表 |

### 大模型提取方案

#### 提取策略

1. **输入**：财报原文（HTML 文本）
2. **提取方式**：使用 LLM 理解上下文，提取结构化数据
3. **输出格式**：

```json
{
  "investment_income": {
    "value": 123,
    "unit": "millions",
    "period": "FY2025",
    "source": "Consolidated Statements of Income"
  },
  "gain_on_sale_of_assets": {
    "value": -45,
    "unit": "millions",
    "period": "FY2025",
    "source": "Note 5: Property, Plant and Equipment"
  }
}
```

#### Prompt 示例

```
请从以下 SEC 财报文本中提取详细的利润项目数据。对于每个项目，提取：
1. 项目名称（中英文）
2. 金额（正数表示收益，负数表示损失）
3. 单位（通常是百万美元）
4. 所在期间（FY/Q）
5. 数据来源（利润表或附注）

重点关注以下项目：
- 投资收益、利息收入/支出
- 资产处置收益/损失
- 各类减值损失
- 汇兑损益
- 重组费用
- 诉讼和解
- 政府补助
- 其他非经常性项目

如果某个项目在财报中不存在，标记为 null。

财报文本：
{filing_text}
```

#### 实现建议

1. **优先级**：先提取利润表中明确列示的项目，再提取附注中的项目
2. **验证**：对比利润表合计数，验证提取的准确性
3. **缓存**：提取结果缓存到文件，避免重复调用 LLM
4. **格式化**：输出为 Markdown 表格，便于阅读

---

## 8. 章节内容

从 SEC 财报中提取的原文章节，按 Item 编号组织。

### 10-K 年报章节

| 章节代码 | 中文名称 | 英文名称 | Item | 说明 |
|:---|:---|:---|:---:|:---|
| business | 公司概况 | Business | 1 | 公司业务模式、产品、市场、竞争环境 |
| risk_factors | 风险因素 | Risk Factors | 1A | 可能影响公司业绩的风险 |
| properties | 物业信息 | Properties | 2 | 公司拥有的不动产 |
| legal_proceedings | 法律诉讼 | Legal Proceedings | 3 | 正在进行的法律诉讼 |
| mda | 管理层讨论与分析 | Management Discussion & Analysis | 7 | 管理层对财务状况和经营成果的讨论 |
| market_risk | 市场风险 | Quantitative and Qualitative Disclosures About Market Risk | 7A | 市场风险的定量和定性披露 |
| financials | 财务报表 | Financial Statements | 8 | 完整的财务报表 |
| earnings | 每股收益 | Earnings Per Share | - | 基本和稀释每股收益 |
| financial_statements_notes | 财务报表附注 | Notes to Financial Statements | - | 财务报表的详细注释 |
| controls_procedures | 内部控制 | Controls and Procedures | 9A | 管理层对内部控制的评估 |
| directors_officers | 董事与高管 | Directors and Officers | 10 | 公司治理结构 |
| compensation | 高管薪酬 | Executive Compensation | 11 | 高管薪酬信息 |
| ownership | 股权信息 | Security Ownership | 12 | 股权结构和受益所有人 |
| related_party | 关联方交易 | Certain Relationships and Related Transactions | 13 | 与关联方的交易 |
| accounting_fees | 审计费用 | Principal Accountant Fees and Services | 14 | 审计和相关费用 |
| exhibits | 附件清单 | Exhibits and Financial Statement Schedules | 15 | 合同、协议等附件 |
| form_10k_summary | 10-K 摘要 | Summary | 16 | 年度报告摘要 |

### 10-Q 季度报告章节

10-Q 季度报告通常包含以下章节：
- **业务分部信息** (Segment Information)
- **管理层讨论与分析** (MD&A)
- **财务报表** (Condensed Consolidated Financial Statements)
- **每股收益** (Earnings Per Share)

---

## 9. 输出文件格式

### 单个财报提取文件

文件路径：`~/.openclaw/reports/sec_analysis/{TICKER}/{TYPE}_{PERIOD}.md`

示例：`~/.openclaw/reports/sec_analysis/QCOM/10-K_FY2025.md`

文件结构：
```markdown
# {TICKER} {TYPE} {PERIOD}

- 财年/季度: {PERIOD}
- 来源: {FILENAME}
- 提取时间: {YYYY-MM-DD HH:MM:SS}

## 财务数据
| 指标 | 数值 |
|:---|:---|
| 营收 | $44,284M |
...

## 现金流明细
| 项目 | 数值 |
|:---|:---|
| 经营现金流 | $14,012M |
...

## 公司概况 (Item 1: Business)
{原文内容}

## 风险因素 (Item 1A: Risk Factors)
{原文内容}

...
```

### 最终分析文档

文件路径：`~/.openclaw/reports/sec_analysis/{TICKER}_analysis_{YYYYMMDD}.md`

包含：
- 年度趋势表格（多期对比 + 3年 CAGR）
- 季度趋势表格（多期对比）
- 现金流趋势表格（年度对比）
- 公司概况汇总（已翻译为中文）
- 风险因素汇总（已翻译为中文）
- 管理层展望汇总（已翻译为中文）

---

## 10. 数据来源说明

### 正则匹配模式

财务数据通过正则表达式从财报原文中提取：

| 字段 | 正则模式 |
|:---|:---|
| 营收 | `Total\s+revenues?\s+\$?\s*([\d,]+)` |
| 成本 | `Cost\s+of\s+revenues?\s+\$?\s*([\d,]+)` |
| 营业利润 | `Operating\s+income\s+\$?\s*([\d,]+)` |
| 净利润 | `Net\s+income\s+\$?\s*([\d,]+)` |
| EPS | `(?:Basic\|Diluted)\s+earnings\s+per\s+share\s+\$?\s*([\d.]+)` |
| 总资产 | `Total\s+assets\s+\$?\s*([\d,]+)` |
| 总负债 | `Total\s+liabilities\s+\$?\s*([\d,]+)` |
| 经营现金流 | `Net\s+cash\s+provided\s+by\s+operating\s+activities\s*[\$]?\s*([\d,]+)` |
| 投资现金流 | `Net\s+cash\s+\(used\)\s+provided\s+by\s+investing\s+activities\s*[\$]?\s*\(([\d,]+)\)` |
| 筹资现金流 | `Net\s+cash\s+used\s+by\s+financing\s+activities\s*[\$]?\s*\(([\d,]+)\)` |
| 资本支出 | `Capital\s+expenditures\s*[\$]?\s*\(\s*([\d,]+)\s*\)` |

### 单位说明

- 所有金额单位为**百万美元** (Millions)
- 格式：`$X,XXXM` 或 `($X,XXXM)`（负数用括号表示）
- 百分比格式：`XX.X%`

---

## 11. 增量更新机制

- 已存在的提取文件会自动跳过
- 只提取新增的财报
- 最终分析文档每次重新生成
- 使用 `--force` 参数可强制重新提取

---

## 12. 使用示例

```bash
# 分析最新的财报（默认行为）
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py QCOM

# 分析所有已下载的财报
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py QCOM --all

# 只分析 10-K 年报（所有年份）
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py QCOM --all --form 10-K

# 分析指定的单个文件
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py QCOM --file qcom-20240929.htm

# 强制重新提取（覆盖已有文件）
python3 ~/.openclaw/skills/stock-god/scripts/sec_analysis.py QCOM --all --force
```

---

## 13. 相关文件

- **源代码**: `~/.openclaw/skills/stock-god/scripts/sec_analysis.py`
- **SKILL 文档**: `~/.openclaw/skills/stock-god/SKILL.md`
- **提取文件目录**: `~/.openclaw/reports/sec_analysis/{TICKER}/`
- **分析报告目录**: `~/.openclaw/reports/sec_analysis/`
