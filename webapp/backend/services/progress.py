"""下载任务日志进度解析：从 sec_filings.py download 的实时日志里提取 (done, total)。

日志格式（10-K/10-Q 主流程，见 scripts/sec_filings.py）::

    --- FY2021: 找到 4 份财报 ---          ← 每财年总数行，累加即 total
      [10-K] ... ✅ 下载完成               ← 计入 done
      [10-Q] ... SKIP (已存在): xxx        ← 计入 done（已处理）
      [10-Q] ... ❌ 下载失败: err          ← 不计入 done（留在分母）

6-K 附加流程（外国发行人）没有"找到 N 份"总数行，其行如::

      [6-K] 2024-01-05 - ✅ 下载完成: out.htm
      [6-K] ... SKIP (已存在): ...
      [6-K] ... 跳过小文件 / 跳过非季度报告  ← 不计入 done

已知限制：6-K 的下载/跳过行会计入 done 但不计入 total，极端情况可能出现
done > total（total 只统计 10-K/10-Q/20-F 主流程的报文数）。
"""
import re

# 每财年总数行：--- FY2021: 找到 4 份财报 ---
_TOTAL_RE = re.compile(r"找到 (\d+) 份财报")
# ✅ 为 U+2705 + 空格；同时命中主流程行尾与 6-K 的 "✅ 下载完成: xxx"（已按 task_9.log 字节核对）
_DONE_MARK = "✅ 下载完成"
_SKIP_MARK = "SKIP (已存在)"


def parse_download_progress(text: str) -> tuple[int, int]:
    """解析日志全文，返回 (done, total)。

    total = 各财年 "找到 N 份财报" 之和；done = "✅ 下载完成" + "SKIP (已存在)" 行数。
    无任何匹配时返回 (0, 0)。
    """
    if not text:
        return (0, 0)
    total = sum(int(n) for n in _TOTAL_RE.findall(text))
    done = text.count(_DONE_MARK) + text.count(_SKIP_MARK)
    if done == 0 and total == 0:
        return (0, 0)
    return (done, total)


class DownloadProgressTracker:
    """增量日志进度累计器：feed 新增日志段，内部累加匹配数，避免每次全文解析。

    feed 会保留最后一个未换行的残行拼到下一段，避免匹配跨块边界被截断。
    """

    def __init__(self) -> None:
        self.done = 0
        self.total = 0
        self._carry = ""  # 尾部残行（可能不完整），留待下一段拼接

    def feed(self, chunk: str) -> None:
        """喂入一段新增日志（按流式顺序），累计内部计数。"""
        if not chunk:
            return
        text = self._carry + chunk
        nl = text.rfind("\n")
        if nl == -1:
            # 整段都没有换行：全部视为残行，等下一段
            self._carry = text
            return
        self._carry = text[nl + 1:]
        done, total = parse_download_progress(text[:nl + 1])
        self.done += done
        self.total += total

    def flush(self) -> None:
        """流结束时解析残行尾巴，保证计数完整。"""
        if self._carry:
            done, total = parse_download_progress(self._carry)
            self.done += done
            self.total += total
            self._carry = ""
