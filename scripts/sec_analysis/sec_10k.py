"""
SEC 10-K 年报分析器
处理 10-K 年报的特定章节和结构
"""

from typing import Dict, List
from .base import FilingAnalyzer


class Sec10KAnalyzer(FilingAnalyzer):
    """10-K 年报分析器"""

    @property
    def FILING_TYPE(self) -> str:
        return "10-K"

    @property
    def SECTION_PATTERNS(self) -> Dict[str, List[str]]:
        """10-K 的章节匹配模式"""
        return {
            "business": [
                r"item\s*1[.\s:—–-]+\s*business",
                r"business\s+overview",
            ],
            "risk_factors": [
                r"item\s*1a[.\s:—–-]+\s*risk\s+factors",
                r"risk\s+factors",
            ],
            "properties": [
                r"item\s*2[.\s:—–-]+\s*properties",
            ],
            "legal_proceedings": [
                r"item\s*3[.\s:—–-]+\s*legal\s+proceedings",
            ],
            "mda": [
                r"item\s*7[.\s:—–-]+\s*management.*?discussion",
                r"management.*?discussion.*?analysis",
                r"md&a",
            ],
            "market_risk": [
                r"item\s*7a[.\s:—–-]+\s*(?:quantitative|market\s+risk)",
            ],
            "financials": [
                r"item\s*8[.\s:—–-]+\s*financial\s+statements",
                r"consolidated\s+statements\s+of\s+operations",
            ],
            "earnings": [
                r"earnings\s+per\s+share",
                r"diluted\s+earnings\s+per\s+share",
            ],
            "financial_statements_notes": [
                r"notes\s+to\s+(?:consolidated\s+)?financial\s+statements",
                r"note\s+\d+[.\s]",
            ],
            "controls_procedures": [
                r"item\s*9a[.\s:—–-]+\s*(?:controls|management.*?assessment)",
            ],
            "directors_officers": [
                r"item\s*10[.\s:—–-]+\s*(?:directors|management)",
            ],
            "compensation": [
                r"item\s*11[.\s:—–-]+\s*executive\s+compensation",
            ],
            "ownership": [
                r"item\s*12[.\s:—–-]+",
            ],
            "related_party": [
                r"item\s*13[.\s:—–-]+",
            ],
            "accounting_fees": [
                r"item\s*14[.\s:—–-]+",
            ],
            "exhibits": [
                r"item\s*15[.\s:—–-]+\s*(?:exhibits|financial\s+statement)",
            ],
            "form_10k_summary": [
                r"item\s*16[.\s:—–-]+",
            ],
        }

    @property
    def EXPECTED_SECTIONS(self) -> List[str]:
        """10-K 预期存在的章节"""
        return [
            "business",
            "risk_factors",
            "legal_proceedings",
            "mda",
            "financials",
            "earnings",
            "financial_statements_notes",
        ]
