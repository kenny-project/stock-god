import os
from services.metrics import _to_number, parse_analysis_metrics, parse_dcf_valuation

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_analysis_metrics():
    with open(os.path.join(FIX, "10-K_FY2024.md"), encoding="utf-8") as f:
        text = f.read()
    m = parse_analysis_metrics(text)
    assert m["净利润"] == 5700          # $5,700M → 百万单位数值
    assert m["营收"] == 51362
    assert m["净资产收益率"] == 39.5     # 百分数保留数值
    assert m["经营现金流"] == 7429
    assert "每股收益" in m
    assert m["每股收益"] == 3.73        # $3.73
    assert m["资本支出"] == -812        # ($812M) → 保留负号
    assert "市值" not in m              # 解析不到的键缺席，严禁编造


def test_parse_analysis_metrics_quarter():
    """真实 AAPL 10-Q 2024Q3 分析文件——季度正文同样含 财务指标/现金流（YTD 累计） 表。"""
    with open(os.path.join(FIX, "10-Q_2024Q3.md"), encoding="utf-8") as f:
        text = f.read()
    m = parse_analysis_metrics(text)
    assert m["营收"] == 85777            # $85,777M（单季度）
    assert m["净利润"] == 21448          # $21,448M
    assert m["每股收益"] == 1.40
    # "现金流（YTD 累计）" 小节以 前缀匹配 命中
    assert m["经营现金流"] == 91443
    assert m["资本支出"] == -6539        # ($6,539M)
    assert m["自由现金流"] == 84904


def test_to_number_signs_and_formats():
    # _NUM 必须保留负号
    assert _to_number("-812") == -812.0
    assert _to_number("$3.73") == 3.73
    assert _to_number("($812M)") == -812.0
    assert _to_number("-13.6%") == -13.6
    assert _to_number("-") is None
    assert _to_number("$1,499.4M") == 1499.4
    # 多币种（20-F 人民币/港币披露）
    assert _to_number("¥94,163M") == 94163.0
    assert _to_number("(¥55,431M)") == -55431.0
    assert _to_number("¥10.29") == 10.29
    assert _to_number("HK$1,234M") == 1234.0


def test_parse_analysis_metrics_multi_currency():
    """真实 PDD 20-F（¥ 计价）——所有金额都必须解析出来，不得静默丢弃。"""
    with open(os.path.join(FIX, "20-F_FY2023.md"), encoding="utf-8") as f:
        text = f.read()
    m = parse_analysis_metrics(text)
    assert m["经营现金流"] == 94163      # ¥94,163M
    assert m["投资现金流"] == -55431     # (¥55,431M)
    assert m["筹资现金流"] == -8961      # (¥8,961M)
    assert m["资本支出"] == -584         # (¥584M)
    assert m["自由现金流"] == 93579      # ¥93,579M
    assert m["折旧摊销"] == 786          # ¥786M
    assert m["营收"] == 247639           # ¥247,639M
    assert m["净利润"] == 60027          # ¥60,027M
    assert m["每股收益"] == 10.29        # ¥10.29
    assert m["流通股数"] == 5503.5       # 5,503.5M
    assert m["净资产收益率"] == 32.1     # 32.1%


def test_parse_dcf_valuation():
    with open(os.path.join(FIX, "US.NKE_DCF.md"), encoding="utf-8") as f:
        text = f.read()
    v = parse_dcf_valuation(text)
    assert v["intrinsic_value_musd"] == 47099
    assert v["price"] == 36.80
    assert len(v["owner_earnings_by_year"]) == 5   # FY2022..FY2026
    assert v["owner_earnings_by_year"][0]["oe"] == 6308
    # "### 每股估值" 小节：每股口径（与股价同量纲），键带 ** 加粗
    assert v["intrinsic_value_per_share"] == 31.80
    assert v["safety_25_price"] == 23.85
    assert v["safety_50_price"] == 15.90


def test_parse_dcf_valuation_aapl_per_share():
    """真实 AAPL 报告：总市值口径（$M）与每股口径必须同时解析出来，互不覆盖。"""
    with open(os.path.join(FIX, "US.AAPL_DCF.md"), encoding="utf-8") as f:
        text = f.read()
    v = parse_dcf_valuation(text)
    assert v["price"] == 332.27
    assert v["intrinsic_value_musd"] == 1859624
    assert v["intrinsic_value_per_share"] == 127.42
    assert v["safety_25_price"] == 95.57
    assert v["safety_50_price"] == 63.71


def test_parse_dcf_valuation_without_per_share_section():
    """旧报告无 每股估值 小节：每股字段缺席（不编造），旧字段照常解析。"""
    text = """## 基本信息

| 指标 | 数值 |
|:---|:---|
| 当前股价 | $36.80 |

## 估值结果

| 指标 | 数值 |
|:---|:---|
| 内在价值（$M） | $47,099M |
"""
    v = parse_dcf_valuation(text)
    assert v["price"] == 36.80
    assert v["intrinsic_value_musd"] == 47099
    assert "intrinsic_value_per_share" not in v
    assert "safety_25_price" not in v


def test_parse_dcf_valuation_four_column_basic_info():
    """新版 4 列基本信息（项目|数值 两两并排）：当前股价须从第 3/4 列解析出来；
    带中文来源标注/非数值单元格不得破坏解析。"""
    text = """## 基本信息

| 项目 | 数值 | 项目 | 数值 |
|:---|:---|:---|:---|
| 公司名称 | 谷歌-A | 当前股价 | $349.54 |
| 行情来源 | 腾讯行情 | 市值 | $4,274.9B |
| 流通股数 | 12,229.9M · 腾讯行情 | 数据截止期 | TTM 截至 2026Q2 |

## 估值结果

| 项目 | 数值 |
|:---|:---|
| **股权内在价值** | **$47,099M** |
"""
    v = parse_dcf_valuation(text)
    assert v["price"] == 349.54
    assert v["intrinsic_value_musd"] == 47099


def test_parse_dcf_valuation_fy_rows_scoped_to_owner_earnings():
    """敏感性分析行（如 `| 20% | $47 | ...`，--growth 20 时真实生成）
    不得被当成年度 Owner Earnings 数据编造进 owner_earnings_by_year。"""
    with open(os.path.join(FIX, "US.NKE_DCF.md"), encoding="utf-8") as f:
        text = f.read()
    # 按 dcf.py 真实输出格式追加高增长率敏感性表
    text += """
## 敏感性分析

不同增长率 × 折现率下的每股内在价值：

| 增长率 \\ 折现率 | r_4% | r_5% | r_6% | r_7% | r_8% | r_10% | r_11% | r_12% |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 20% | $47 | $44 | $41 | $39 | $36 | $31 | $29 | $26 |
| 23% | $52 | $48 | $45 | $42 | $39 | $33 | $31 | $28 |
"""
    v = parse_dcf_valuation(text)
    assert [e["year"] for e in v["owner_earnings_by_year"]] == [
        "FY2022", "FY2023", "FY2024", "FY2025", "FY2026",
    ]
    assert all(e["oe"] == o for e, o in zip(
        v["owner_earnings_by_year"], [6308, 5192, 6009, 3736, 3445]))
