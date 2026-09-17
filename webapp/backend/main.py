import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import make_engine, make_session_factory, Base
import models  # noqa
from deps import set_engine
from executor import TaskExecutor
from api import stocks as stocks_api
from api import tasks_api
from api import reports as reports_api
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


def set_engine_for_test(engine, factory):
    set_engine(engine, factory)
