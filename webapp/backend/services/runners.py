"""各任务类型的脚本命令构造与退出错误分类。仅构造与分类，进程执行在 executor.py。"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")


def build_command(task_type: str, symbol: str, params: dict) -> list[str]:
    """symbol 为 Stock.symbol() 格式（如 US.NKE）；analysis 任务用纯 ticker。"""
    py = sys.executable
    if task_type == "download":
        cmd = [py, os.path.join(SCRIPTS_DIR, "sec_filings.py"), "download", symbol]
        if params.get("years"):
            cmd += ["--years", str(params["years"])]
        if params.get("form"):
            cmd += ["--form", params["form"]]
        return cmd
    if task_type == "analysis":
        cmd = [py, os.path.join(SCRIPTS_DIR, "sec_analysis.py"), symbol.split(".", 1)[-1]]
        # file 优先：行内单文件生成/更新（filing 粒度）；无 file 才整股 --all
        if params.get("file"):
            cmd += ["--file", params["file"]]
        else:
            cmd += ["--all"]
        if params.get("form"):
            cmd += ["--form", params["form"]]
        if params.get("force"):
            cmd += ["--force"]
        return cmd
    if task_type == "dcf":
        cmd = [py, os.path.join(SCRIPTS_DIR, "dcf.py"), symbol]
        for key in ("growth", "discount", "years", "safety"):
            if params.get(key) is not None:
                cmd += [f"--{key}", str(params[key])]
        return cmd
    raise ValueError(f"unknown task_type: {task_type}")


_ERROR_PATTERNS = [
    # 403/429 必须带上下文匹配（正则），否则 traceback 行号（如 line 4032）会误判为限流
    ((
        re.compile(r"(http error (403|429)|status[:=] ?(403|429)|rate limit|forbidden)", re.IGNORECASE),
    ), "SEC_RATE_LIMITED", "数据源限流/拒绝访问（403/429），建议 10 分钟后重试"),
    (("timeout", "timed out"), "NETWORK_TIMEOUT", "网络请求超时，请检查网络后重试"),
    (("no filings", "not found", "404"), "NO_FILINGS_FOUND", "未找到目标财报或资源不存在"),
]


def classify_error(exit_code: int, stderr_tail: str) -> tuple[str, str]:
    """仅对非零退出码分类；exit_code == 0 视为无错误（返回空元组）。"""
    if exit_code == 0:
        return "", ""
    low = stderr_tail.lower()
    for patterns, code, summary in _ERROR_PATTERNS:
        # 正则条目用 search，其余保持子串匹配
        if any(p.search(low) if isinstance(p, re.Pattern) else p in low for p in patterns):
            return code, summary
    return "SCRIPT_EXIT_NONZERO", f"脚本异常退出（exit={exit_code}），详情见任务日志"
