"""生成器版本号（后端侧）。

单一定义点在 scripts/dataversion.py（脚本侧无第三方依赖、不能 import
webapp.backend），本模块用 importlib 按路径加载该文件取常量，
保证脚本侧与后端侧版本号不会漂移。
"""
import importlib.util
import os
import re

# webapp/backend/services/ → 上三级到项目根 → scripts/dataversion.py
_DV_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, os.pardir, "scripts", "dataversion.py"))

_spec = importlib.util.spec_from_file_location("stockgod_dataversion", _DV_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - 文件缺失属部署损坏，直接暴露
    raise RuntimeError(f"版本定义文件缺失: {_DV_PATH}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

ANALYSIS_VERSION: str = _mod.ANALYSIS_VERSION
DCF_VERSION: str = _mod.DCF_VERSION

# md 头部版本行：`生成器版本: analysis-v2` / `生成器版本: dcf-v1`
_MD_VERSION_LINE = re.compile(r"^生成器版本:\s*(analysis|dcf)-(\S+)$")


def parse_md_version(text: str, kind: str) -> str | None:
    """从产物 md 头部解析 `生成器版本: {kind}-vN` 行，返回 vN；
    无该行（或 kind 不符）返回 None → 入库为 NULL，视为 legacy 旧数据。
    只扫头部前 20 行，避免正文里引用该行文样被误读。"""
    for line in text.splitlines()[:20]:
        m = _MD_VERSION_LINE.match(line.strip())
        if m and m.group(1) == kind:
            return m.group(2)
    return None
