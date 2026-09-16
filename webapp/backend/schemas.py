from datetime import datetime
from pydantic import BaseModel, Field


class StockOut(BaseModel):
    id: int
    ticker: str
    name_cn: str | None
    name_en: str | None
    market: str
    exchange: str | None
    class Config: from_attributes = True


class StockPage(BaseModel):
    total: int
    items: list[StockOut]


class FilingOut(BaseModel):
    id: int
    form_type: str
    period: str | None
    local_path: str
    downloaded_at: datetime
    class Config: from_attributes = True


class AnalysisOut(BaseModel):
    id: int
    form_type: str
    fiscal_year: int
    metrics: dict | None
    generated_at: datetime
    class Config: from_attributes = True


class DcfOut(BaseModel):
    id: int
    growth: float | None
    discount: float | None
    years: int | None
    safety: float | None
    valuation: dict | None
    generated_at: datetime
    class Config: from_attributes = True


class TaskOut(BaseModel):
    id: int
    task_type: str
    status: str
    error_code: str | None
    error_summary: str | None
    log_path: str | None
    params: dict | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stock: StockOut | None
    class Config: from_attributes = True


class StockDetail(StockOut):
    cik: int | None
    filings: list[FilingOut]
    analyses: list[AnalysisOut]
    dcf_reports: list[DcfOut]
    running_tasks: list[TaskOut]


class TaskCreate(BaseModel):
    task_type: str = Field(pattern="^(download|analysis|dcf)$")
    ticker: str
    params: dict = {}
