"""
SEC 10-Q 季报分析器
处理 10-Q 季报的特定章节和结构

10-Q 结构（与 10-K 不同）：
- Part I:
  - Item 1: Financial Statements（财务报表）
  - Item 2: Management's Discussion and Analysis（MD&A）
  - Item 3: Quantitative and Qualitative Disclosures About Market Risk
  - Item 4: Controls and Procedures
- Part II:
  - Item 1A: Risk Factors
  - Item 2: Unregistered Sales of Equity Securities
  - Item 5: Other Information
  - Item 6: Exhibits
"""

import re
from typing import Dict, List
from .base import FilingAnalyzer


class Sec10QAnalyzer(FilingAnalyzer):
    """10-Q 季报分析器"""

    @property
    def FILING_TYPE(self) -> str:
        return "10-Q"

    @property
    def SECTION_PATTERNS(self) -> Dict[str, List[str]]:
        """10-Q 的章节匹配模式"""
        return {
            # Part I - Financial Information
            "financial_statements": [
                r"item\s*1[.\s:—–-]+\s*(?:financial\s+statements|condensed\s+consolidated)",
                r"condensed\s+consolidated\s+statements\s+of\s+operations",
                r"consolidated\s+statements\s+of\s+operations",
            ],
            "mda": [
                r"item\s*2[.\s:—–-]+\s*management.*?discussion",
                r"management.*?discussion.*?analysis",
                r"md&a",
            ],
            "market_risk": [
                r"item\s*3[.\s:—–-]+\s*(?:quantitative|market\s+risk)",
            ],
            "controls_procedures": [
                r"item\s*4[.\s:—–-]+\s*(?:controls|management.*?assessment)",
            ],
            # Part II - Other Information
            "risk_factors": [
                r"item\s*1a[.\s:—–-]+\s*risk\s+factors",
                r"risk\s+factors",
            ],
            "earnings": [
                r"earnings\s+per\s+share",
                r"diluted\s+earnings\s+per\s+share",
            ],
            "financial_statements_notes": [
                r"notes\s+to\s+(?:condensed\s+)?(?:consolidated\s+)?financial\s+statements",
                r"note\s+\d+[.\s]",
            ],
            "exhibits": [
                r"item\s*6[.\s:—–-]+\s*(?:exhibits|financial\s+statement)",
            ],
        }

    @property
    def EXPECTED_SECTIONS(self) -> List[str]:
        """10-Q 预期存在的章节"""
        return [
            "financial_statements",
            "mda",
            "controls_procedures",
            "risk_factors",
            "earnings",
            "financial_statements_notes",
        ]

    def find_body_start(self, text: str) -> int:
        """
        10-Q 的正文开始位置逻辑
        10-Q 的 Item 1 是 Financial Statements，不是 Business
        """
        # 方法1: 查找 "Part I" 或 "Item 1 Financial"
        part_markers = [
            r"item\s*1[.\s:—–-]+\s*financial",
            r"part\s+[iI][\s.:—–-]",
            r"PART\s+I\s*\.",
        ]

        for pattern in part_markers:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if matches:
                # 跳过目录（通常在前 200000 字符内），找正文中的匹配
                for m in matches:
                    if m.start() > 200000:
                        return m.start()
                # 如果都在前 200000 字符内，取最后一个
                return matches[-1].start()

        # 方法2: 跳过前 15000 字符（目录通常不会超过这个长度）
        return min(15000, len(text) // 4)
