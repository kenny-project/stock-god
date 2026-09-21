import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from db import make_engine, make_session_factory, Base
import models  # noqa
from deps import set_engine
from executor import TaskExecutor
from api import stocks as stocks_api
from api import tasks_api
from api import reports as reports_api
from api import versions as versions_api
from services.edgar import fetch_company_tickers, upsert_stocks


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    Factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    set_engine(engine, Factory)
    ex = TaskExecutor(Factory)
    tasks_api.set_executor(ex)
    # 首次启动：股票表为空则自动拉 EDGAR（失败不阻塞启动，可稍后手动同步）
    def _seed():
        with Factory() as s:
            from sqlalchemy import select, func
            from models import Stock
            if s.scalar(select(func.count(Stock.id))) == 0:
                upsert_stocks(s, fetch_company_tickers())
    try:
        await asyncio.get_running_loop().run_in_executor(None, _seed)
    except Exception as e:
        print(f"[startup] EDGAR 拉取失败，股票表为空，可稍后手动同步: {e}")
    yield


app = FastAPI(title="stock-god web", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(stocks_api.router)
app.include_router(tasks_api.router)
app.include_router(reports_api.router)
app.include_router(versions_api.router)

# 生产模式：托管前端构建产物（SPA 回退到 index.html；realpath 校验防路径穿越）
DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend-dist"))
if os.path.isdir(DIST):
    _dist_real = os.path.realpath(DIST) + os.sep
    _assets_dir = os.path.join(DIST, "assets")

    class _HashedAssets(StaticFiles):
        """assets 下的带 hash 文件长缓存（StaticFiles 默认不带 Cache-Control）"""

        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            if resp.status_code == 200:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    if os.path.isdir(_assets_dir):
        app.mount("/assets", _HashedAssets(directory=_assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        full = os.path.realpath(os.path.join(DIST, path))
        if path and full.startswith(_dist_real) and os.path.isfile(full):
            return FileResponse(full, headers={"Cache-Control": "public, max-age=31536000, immutable"})
        return FileResponse(os.path.join(DIST, "index.html"), headers={"Cache-Control": "no-cache"})


def set_engine_for_test(engine, factory):
    set_engine(engine, factory)
