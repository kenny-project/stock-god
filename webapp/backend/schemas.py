from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticker: str
    name_cn: str | None
    name_en: str | None
    market: str
    exchange: str | None
    is_favorite: bool = False
    aliases: list[str] = []  # 搜索别名（中文/简称）
    filed_count: int = 0  # 已下载财报数量（列表/详情由查询填充）

    @field_validator("aliases", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or []  # 旧行/异常路径下 aliases 为 NULL 时按空列表处理


class FavoriteUpdate(BaseModel):
    favorite: bool


class AliasUpdate(BaseModel):
    aliases: list[str]  # 覆盖式保存；去空白/去重/丢空串由端点处理


class StockPage(BaseModel):
    total: int
    items: list[StockOut]


class FilingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    form_type: str
    period: str | None
    local_path: str
    downloaded_at: datetime


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    form_type: str
    fiscal_year: int
    quarter: str | None = None  # 季报有值（如 "2024Q3"），年报为 None
    period: str | None = None  # 配对到的财报报告期（filing.period），配不到为 None
    metrics: dict | None
    # 生成器版本（如 "v2"）；NULL = legacy 旧数据，前端据此打"旧版"徽标（仅提示不阻断）
    generator_version: str | None = None
    generated_at: datetime


class DcfOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    growth: float | None
    discount: float | None
    years: int | None
    safety: float | None
    valuation: dict | None
    # 生成器版本（如 "v1"）；NULL = legacy 旧数据，前端据此打"旧版"徽标（仅提示不阻断）
    generator_version: str | None = None
    generated_at: datetime


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_type: str
    status: str
    error_code: str | None
    error_summary: str | None
    log_path: str | None
    params: dict | None
    # 下载任务实时进度（仅 download 任务有值，解析失败为 NULL，前端据此隐藏）
    progress_done: int | None = None
    progress_total: int | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stock: StockOut | None


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
