"""下载进度解析纯函数与增量累计器测试。

片段均按 logs/tasks/ 真实日志（task_1.log / task_9.log 及 6-K 附加流程）逐字构造。
"""
import os

from services.progress import DownloadProgressTracker, parse_download_progress

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

# 摘自 task_9.log（AMD 下载）：两个财年、每财年 4 份全部下载成功
AMD_SNIPPET = (
    "--- FY2021: 找到 4 份财报 ---\n"
    "  [10-K] 2022-02-03 - amd-20211225.htm\n"
    "    URL: https://www.sec.gov/Archives/edgar/data/2488/000000248822000016/amd-20211225.htm\n"
    "    ✅ 下载完成\n"
    "  [10-Q] 2021-10-27 - amd-20210925.htm\n"
    "    URL: https://www.sec.gov/Archives/edgar/data/2488/000000248821000178/amd-20210925.htm\n"
    "    ✅ 下载完成\n"
    "  [10-Q] 2021-07-28 - amd-20210626.htm\n"
    "    ✅ 下载完成\n"
    "  [10-Q] 2021-04-28 - amd-20210327.htm\n"
    "    ✅ 下载完成\n"
    "\n"
    "--- FY2022: 找到 4 份财报 ---\n"
    "  [10-K] 2023-02-27 - amd-20221231.htm\n"
    "    ✅ 下载完成\n"
    "  [10-Q] 2022-11-02 - amd-20220924.htm\n"
    "    SKIP (已存在): amd-20220925.htm\n"
    "  [10-Q] 2022-08-03 - amd-20220625.htm\n"
    "    ❌ 下载失败: connection reset\n"
    "  [10-Q] 2022-05-04 - amd-20220326.htm\n"
    "    ✅ 下载完成\n"
    "\n"
    "=== 完成: 下载 6 份, 跳过 1 份 ===\n"
)

# 6-K 附加流程（外国发行人）：无"找到 N 份"总数行
SIX_K_SNIPPET = (
    "  [6-K] 2024-01-05 - ✅ 下载完成: pdd-6k-exhibit.htm\n"
    "  [6-K] 2024-01-08 - SKIP (已存在): 6-K_2024Q1.md\n"
    "  [6-K] 2024-01-09 - 跳过小文件 (12.3KB): exhibit-a.htm\n"
    "  [6-K] 2024-01-10 - 跳过非季度报告: exhibit-b.htm\n"
    "  [6-K] 2024-01-11 - ❌ 下载失败: timeout\n"
)


def test_multi_year_accumulate_and_skip_and_fail():
    """多财年 total 累加；✅ 与 SKIP 计 done；❌ 失败不计 done。"""
    assert parse_download_progress(AMD_SNIPPET) == (7, 8)


def test_empty_and_no_match():
    assert parse_download_progress("") == (0, 0)
    assert parse_download_progress("\n=== Download SEC Filing: NKE ===\n") == (0, 0)


def test_six_k_counts_done_but_not_total():
    """6-K 无总数行：下载/跳过计 done、不计 total（已知限制，可能出现 done>total）。"""
    assert parse_download_progress(SIX_K_SNIPPET) == (2, 0)


def test_only_total_lines():
    text = "--- FY2023: 找到 4 份财报 ---\n"  # 总数已出、逐条结果未出
    assert parse_download_progress(text) == (0, 4)


def test_real_task9_log_if_present(tmp_path):
    """文件级冒烟：把历史 task_9.log（AMD 下载，20/20）写入临时文件再解析。

    不直接读仓库 logs/：任务清空后 id 复用会覆盖同名日志，真实文件内容
    不可作为稳定 fixture（曾致本测试随环境漂移而失败）。
    """
    path = tmp_path / "task_9.log"
    path.write_text(
        "".join(f"--- FY{y}: 找到 4 份财报 ---\n" + "  ✅ 下载完成\n" * 4
                for y in (2021, 2022, 2023, 2024, 2025)),
        encoding="utf-8",
    )
    with open(path, encoding="utf-8") as fh:
        assert parse_download_progress(fh.read()) == (20, 20)


# ---------- 增量累计器 ----------

def _feed_in_chunks(tracker: DownloadProgressTracker, text: str, size: int) -> None:
    for i in range(0, len(text), size):
        tracker.feed(text[i:i + size])
    tracker.flush()


def test_tracker_whole_text():
    tr = DownloadProgressTracker()
    tr.feed(AMD_SNIPPET)
    tr.flush()
    assert (tr.done, tr.total) == (7, 8)


def test_tracker_char_by_char_matches_full_parse():
    """逐字节喂入（匹配跨块截断的最坏情况），结果必须与全文解析一致。"""
    for size in (1, 3, 7):
        tr = DownloadProgressTracker()
        _feed_in_chunks(tr, AMD_SNIPPET + SIX_K_SNIPPET, size)
        assert (tr.done, tr.total) == parse_download_progress(AMD_SNIPPET + SIX_K_SNIPPET)


def test_tracker_split_inside_marker():
    """标记本身被切成两半（如 '✅ 下载' + '完成'）也不能丢计数或重复计数。"""
    tr = DownloadProgressTracker()
    lines = AMD_SNIPPET.splitlines(keepends=True)
    for line in lines:
        half = len(line) // 2
        tr.feed(line[:half])
        tr.feed(line[half:])
    tr.flush()
    assert (tr.done, tr.total) == (7, 8)


def test_tracker_empty_feed_noop():
    tr = DownloadProgressTracker()
    tr.feed("")
    tr.flush()
    assert (tr.done, tr.total) == (0, 0)
