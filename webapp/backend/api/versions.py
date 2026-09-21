from fastapi import APIRouter

from services.dataversion import ANALYSIS_VERSION, DCF_VERSION

router = APIRouter(prefix="/api", tags=["versions"])


@router.get("/versions")
def get_versions():
    """当前生成器版本（单一定义点 scripts/dataversion.py，本接口只读常量）。

    前端据此给 generator_version 为 NULL（legacy）或 ≠ 当前版本的存量产物
    打"旧版"徽标——仅展示提示，不做任何阻断。"""
    return {"analysis": ANALYSIS_VERSION, "dcf": DCF_VERSION}
