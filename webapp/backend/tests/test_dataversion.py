"""生成器版本号机制测试：单一定义点、md 头部版本行解析、dcf.py 侧旧版数据校验。

- 单一定义点：webapp 后端常量必须与 scripts/dataversion.py 完全一致（不漂移）
- register/前端依赖的 parse_md_version：有版本行 → vN，无版本行/kind 不符 → None（legacy）
- dcf.py：find_stale_analysis 识别 legacy/旧版本号记录；generate_report 写版本行，
  旧版数据参与计算时报告头部下方出现 legacy 警告引用块（警告后继续，不阻断）
"""

import os
import sys

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import dcf  # noqa: E402
import dataversion as scripts_dv  # noqa: E402
from services.dataversion import ANALYSIS_VERSION, DCF_VERSION, parse_md_version  # noqa: E402


def test_single_definition_point():
    """后端常量从 scripts/dataversion.py 加载，两侧必须一致。"""
    assert ANALYSIS_VERSION == scripts_dv.ANALYSIS_VERSION
    assert DCF_VERSION == scripts_dv.DCF_VERSION


def test_parse_md_version():
    """有版本行返回 vN；无行/kind 不符返回 None；只扫头部前 20 行。"""
    md = "# NKE 10-K FY2024\n\n提取时间: 2026-09-20 10:00:00\n\n生成器版本: analysis-v2\n\n## 财务指标\n"
    assert parse_md_version(md, "analysis") == "v2"
    assert parse_md_version(md, "dcf") is None  # kind 不符（分析文件不解析 dcf 版本）
    assert parse_md_version("# 旧产物\n\n提取时间: 2026-01-01\n", "analysis") is None  # legacy
    assert parse_md_version("生成器版本: dcf-v1", "analysis") is None
    # 版本行落在 20 行以外 → 不认（只采信头部）
    assert parse_md_version("\n" * 25 + "生成器版本: analysis-v2\n", "analysis") is None


def test_dcf_parse_gen_version():
    """dcf.py 侧从分析 md 头部解析 analysis 版本，缺失 → None（legacy）。"""
    with_v = "# NKE 10-K FY2025\n\n提取时间: 2026-09-20\n\n生成器版本: analysis-v2\n\n## 财务指标\n"
    assert dcf._parse_gen_version(with_v) == scripts_dv.ANALYSIS_VERSION
    assert dcf._parse_gen_version("# legacy 产物，无版本行\n") is None


def test_find_stale_analysis():
    """legacy（无版本号）与版本号 ≠ 当前 的记录都算旧版；当前版本不算。"""
    cur = scripts_dv.ANALYSIS_VERSION
    fresh = {"source": "10-K_FY2025.md", "gen_version": cur}
    legacy = {"source": "10-K_FY2020.md", "gen_version": None}
    older = {"source": "10-Q_2019Q3.md", "gen_version": "v0"}
    stale = dcf.find_stale_analysis([fresh, legacy], [older])
    assert [r["source"] for r in stale] == ["10-K_FY2020.md", "10-Q_2019Q3.md"]
    assert dcf.find_stale_analysis([fresh], []) == []


def _minimal_dcf_result():
    return dcf.dcf_valuation(100, 0.05, 0.10, 0.03, 5, 10.0, 20.0)


def test_generate_report_writes_version_line():
    report = dcf.generate_report(
        "US.NKE", _minimal_dcf_result(), [], [], None,
        {"currency": "$", "base_period": "测试基期"})
    assert f"生成器版本: dcf-{scripts_dv.DCF_VERSION}" in report
    # 非旧版数据不出 legacy 警告引用块
    assert "本报告基于旧版分析数据" not in report


def test_generate_report_marks_stale_analysis():
    report = dcf.generate_report(
        "US.NKE", _minimal_dcf_result(), [], [], None,
        {"currency": "$", "base_period": "测试基期"}, stale_analysis=True)
    assert "> ⚠️ 本报告基于旧版分析数据（legacy），结果可能不可靠，建议先重新生成财报分析" in report
    # 引用块在头部下方（基本信息小节之前）
    assert report.index("本报告基于旧版分析数据") < report.index("## 基本信息")
