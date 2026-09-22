# 财报数据 Tab 改造设计与追踪

> 状态：已与用户确认（2026-09-21），开发中。
> 需求来源：QCOM 详情页验收反馈——图表 Tab 信息量不足，改为结构化财务指标表。

## 1. 需求与已确认决策

| # | 决策点 | 结论 |
|:---|:---|:---|
| 1 | Tab 顺序 | `财报原文｜财报数据｜DCF｜财报分析`（财报分析移到最后） |
| 2 | 原图表 Tab | 取消，营收/净利润趋势图移入财报数据 Tab 底部 |
| 3 | 指标列范围 | 年报（FY2025…，最新在前）+ 最近 4 个季度 |
| 4 | DCF 基础信息段 | TTM 基期明细套：基期 OE + 三成分（净利润/折旧摊销/CapEx 及推导）+ 流通股数 + 现价 + 数据截止期 |
| 5 | 单位 | 所有金额以美元计价、以**亿**为单位（DB 存百万 M，÷100 展示）；比率为 %；每股收益为 $/股原值 |

## 2. 界面布局（财报数据 Tab）

```
┌ 基础信息（行=指标，列=报告期，最新在前）──────────────────────┐
│ 指标          FY2025   FY2024   …  2026Q3  2026Q2  2026Q1  2025Q4 │
│ 营收（亿$）    4028     3500        …                          │
│ 净利润（亿$）  1322     1001                                   │
│ 净利率         32.8%    28.6%                                  │
│ 毛利润 / 毛利率、每股收益($)、现金及等价物、自由现金流、        │
│ 总资产 / 总负债 / 资产负债率、净资产收益率                     │
├ DCF 基础信息 ────────────────────────────────────────────────┤
│ 基期 OE 9,641（TTM 截至 2026Q3）＝净利润 9,259 + 折旧 1,573    │
│   − 维护 CapEx 1,191（CapEx 1,985 × 0.6）                     │
│ 流通股数 1,068M · 现价 $177.72                                │
├ 趋势图 ──────────────────────────────────────────────────────┤
│ 营收/净利润趋势（原 TrendChart，年报口径）                     │
└──────────────────────────────────────────────────────────────┘
```

- 指标行固定 12 行：营收、净利润、净利率、毛利润、毛利率、每股收益、现金及等价物、自由现金流、总资产、总负债、资产负债率、净资产收益率
- 缺数据的期显示 `-`，不编造（数据纪律）
- 年报与季度列同表并排，季度列用浅色底区分

## 3. 数据契约

**新增后端接口 `GET /api/stocks/{ticker}/financials`**：

```json
{
  "columns": [
    {"kind": "annual", "label": "FY2025", "analysis_id": 34},
    {"kind": "quarter", "label": "2026Q3", "analysis_id": 155}
  ],
  "rows": [
    {"key": "revenue", "label": "营收（亿$）", "type": "money_yi",
     "values": {"FY2025": 4028.4, "2026Q3": 1197.9, "2025Q3": null}}
  ],
  "ttm": {
    "base_period": "TTM 截至 2026Q3",
    "net_income": 9259, "depreciation": 1573, "capex": 1985,
    "maintenance_capex": 1191, "owner_earnings": 9641,
    "components": "年报 5,541 + 26Q1 (…) + …"
  },
  "shares_outstanding": 1068.1,
  "price": 177.72
}
```

- `columns`：年报 = `quarter IS NULL` 的 analysis 记录（form_type 10-K/20-F），季度取最近 4 条（10-Q/6-K），均按期倒序；两列来源都是 `analysis.metrics`（JSON 中文键）
- `rows`：12 指标 → metrics 键映射：营收/净利润/净利率/毛利润/毛利率/每股收益/现金及等价物/自由现金流/总资产/总负债/资产负债率/净资产收益率；money_yi = M 值 ÷100 保留 1 位小数，ratio 原值加 %，eps 保留 $ 原值
- `ttm`：由后端从 DB 的年报+季报 metrics 计算（与 scripts/dcf.py compute_ttm 同一套公式：利润表科目单季滚动、现金流科目 YTD 累计；任一成分缺失则该成分置 null 并在 components 注明，不编造）。实现放 `webapp/backend/services/ttm.py`（不 import scripts/dcf.py，避免跨目录依赖；公式注释互引）
- 股数：取最近一期 metrics 的"流通股数"；现价：复用腾讯行情适配器（scripts/common.py TencentQuoteProvider），取不到置 null

## 4. 任务清单

- [x] 后端 services/ttm.py：从 DB analysis 记录计算 TTM 基期（含 components 推导串），单测
- [x] 后端 GET /api/stocks/{ticker}/financials（columns/rows/ttm/shares/price），单测
- [x] 前端 Tab 重排 + 图表 Tab 移除、TrendChart 移入财报数据 Tab
- [x] 前端 财报数据 Tab：基础信息表（12 行 × 年报+四季列）、DCF 基础信息段、空态
- [x] 评审闭环（spec 合规 + 代码质量）：修 Critical fmtYi 双重除100、Important 同FY列label消歧（+单测）、Minor finLoading 时序（adde643，148 tests）
- [x] 重启服务并通知验收

## 5. 变更记录

- 2026-09-21：设计确认（含 4 项用户选择），文档建立
