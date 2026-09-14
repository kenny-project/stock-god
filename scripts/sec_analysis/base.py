"""
SEC 财报分析器基础抽象类
定义 10-K 和 10-Q 的公共接口和共用逻辑
"""

import os
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Dict, List, Optional, Any


class HTMLTextExtractor(HTMLParser):
    """从 HTML 提取纯文本

    空白策略：文本节点按源码原样保留（相邻 inline span 拆开的单词自动合并，
    如 MSFT 10-K 标题 "<span>ITEM 1. B</span><span>USINESS</span>"），
    仅在块级标签边界插入空格（表格单元格/段落之间的词分隔）。
    """

    BLOCK_TAGS = {
        "p", "div", "td", "th", "tr", "table", "thead", "tbody", "tfoot",
        "br", "hr", "li", "ul", "ol", "dl", "dt", "dd",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer",
    }

    def __init__(self):
        super().__init__()
        self.result = []
        self.skip_tags = {"script", "style", "head"}
        self.current_tag = None

    def handle_starttag(self, tag, attrs):
        self.current_tag = tag
        if tag in self.skip_tags:
            self.skip = True
        elif tag in self.BLOCK_TAGS:
            self.result.append(" ")

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self.skip = False
        elif tag in self.BLOCK_TAGS:
            self.result.append(" ")
        self.current_tag = None

    def handle_data(self, data):
        if not hasattr(self, "skip") or not self.skip:
            self.result.append(data)

    def get_text(self):
        return re.sub(r"\s+", " ", "".join(self.result)).strip()


class FilingAnalyzer(ABC):
    """SEC 财报分析器基类"""

    def __init__(self):
        self.sections = {}
        self.metrics = {}
        self.cash_flows = {}

    @property
    @abstractmethod
    def FILING_TYPE(self) -> str:
        """财报类型（10-K 或 10-Q）"""
        pass

    @property
    @abstractmethod
    def SECTION_PATTERNS(self) -> Dict[str, List[str]]:
        """章节匹配模式（子类定义）"""
        pass

    @property
    @abstractmethod
    def EXPECTED_SECTIONS(self) -> List[str]:
        """预期存在的章节列表（子类定义）"""
        pass

    def find_body_start(self, text: str) -> int:
        """找到正文开始位置，跳过目录"""
        # 通用方法：查找 "Part I" 或 "Item 1"
        part_markers = [
            r"item\s*1[.\s:—–-]+\s*(?:business|financial)",  # 10-K: Business, 10-Q: Financial
            r"part\s+[iI1][\s.:—–-]",
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

    def find_sections(self, text: str, section_type: str, start_from: int = 0) -> int:
        """在文本中查找关键章节，从 start_from 位置开始"""
        patterns = self.SECTION_PATTERNS.get(section_type, [])
        search_text = text[start_from:]

        for pattern in patterns:
            matches = list(re.finditer(pattern, search_text, re.IGNORECASE))
            if matches:
                # 返回第一个匹配的位置（加上 start_from 偏移）
                return start_from + matches[0].start()
        return -1

    def extract_section_content(self, text: str, start_pos: int, max_chars: Optional[int] = None) -> str:
        """从指定位置提取章节内容"""
        if start_pos < 0:
            return ""

        # 从 start_pos 开始，提取到下一个主要章节
        end_markers = [
            r"item\s+\d+[a-z]?[.\s:—–-]",
            r"part\s+[ivx]+",
            r"table\s+of\s+contents",
        ]

        end_pos = len(text) if max_chars is None else min(start_pos + max_chars, len(text))

        # 尝试找到下一个章节的开始
        for marker in end_markers:
            matches = list(re.finditer(marker, text[start_pos + 100:], re.IGNORECASE))
            if matches:
                candidate_end = start_pos + 100 + matches[0].start()
                if candidate_end < end_pos:
                    end_pos = candidate_end
                    break

        content = text[start_pos:end_pos]
        # 清理多余空白
        content = re.sub(r"\s+", " ", content).strip()
        return content

    def extract_text_from_html(self, html_content: str) -> str:
        """从 HTML 内容提取纯文本"""
        extractor = HTMLTextExtractor()
        try:
            extractor.feed(html_content)
            return extractor.get_text()
        except Exception:
            # 如果解析失败，用正则简单去除标签
            text = re.sub(r"<[^>]+>", " ", html_content)
            text = re.sub(r"\s+", " ", text)
            return text.strip()

    def extract_sections(self, text: str, body_start: int) -> Dict[str, Dict[str, Any]]:
        """提取所有章节（只检查预期存在的章节）"""
        sections = {}

        for section_type in self.EXPECTED_SECTIONS:
            pos = self.find_sections(text, section_type, start_from=body_start)
            if pos >= 0:
                content = self.extract_section_content(text, pos)
                sections[section_type] = {
                    "found": True,
                    "content": content,
                }
            else:
                sections[section_type] = {"found": False}

        return sections

    def analyze(self, html_content: str) -> Dict[str, Any]:
        """分析流程（共用）"""
        # 提取纯文本
        text = self.extract_text_from_html(html_content)

        # 找到正文开始位置
        body_start = self.find_body_start(text)

        # 提取各章节
        self.sections = self.extract_sections(text, body_start)

        return {
            "text": text,
            "body_start": body_start,
            "sections": self.sections,
        }
