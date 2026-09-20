from datetime import datetime
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, DateTime, UniqueConstraint, Text, Boolean, false
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db import Base


class Stock(Base):
    __tablename__ = "stock"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name_cn: Mapped[str | None] = mapped_column(String(128), default=None)
    name_en: Mapped[str | None] = mapped_column(String(256), default=None)
    cik: Mapped[int | None] = mapped_column(Integer, default=None)
    market: Mapped[str] = mapped_column(String(8), default="US")
    exchange: Mapped[str | None] = mapped_column(String(32), default=None)
    # 收藏标记：server_default 保证 SQLite 既有表 ALTER ADD COLUMN 后旧行有值
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # 指数成分标记（同步任务按名单刷新；同样用 server_default 兜底旧行）
    in_sp500: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    in_ndx100: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # 搜索别名（如 GOOGL → ["google", "谷歌"]）；engine 层 json_serializer 保证中文不转义
    aliases: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")

    filings: Mapped[list["Filing"]] = relationship(back_populates="stock", cascade="all,delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="stock", cascade="all,delete-orphan")
    dcf_reports: Mapped[list["DcfReport"]] = relationship(back_populates="stock", cascade="all,delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="stock")

    def symbol(self) -> str:
        return f"{self.market}.{self.ticker}"


class Filing(Base):
    __tablename__ = "filing"
    __table_args__ = (UniqueConstraint("stock_id", "form_type", "period", name="uq_filing"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"))
    form_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    period: Mapped[str | None] = mapped_column(String(16), default=None)
    accession_no: Mapped[str | None] = mapped_column(String(32), default=None)
    local_path: Mapped[str] = mapped_column(String(512))
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    stock: Mapped["Stock"] = relationship(back_populates="filings")


class Analysis(Base):
    __tablename__ = "analysis"
    # quarter 仅季报有值（如 "2024Q3"），年报为 NULL；SQLite 下 NULL 在唯一索引中互不相等，
    # 年报幂等由登记器的内存 existing 集合兜底
    __table_args__ = (UniqueConstraint("stock_id", "form_type", "fiscal_year", "quarter", name="uq_analysis"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"))
    form_type: Mapped[str] = mapped_column(String(16))
    fiscal_year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[str | None] = mapped_column(String(8), default=None)
    local_path: Mapped[str] = mapped_column(String(512))
    metrics: Mapped[dict | None] = mapped_column(JSON, default=None)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    stock: Mapped["Stock"] = relationship(back_populates="analyses")


class DcfReport(Base):
    __tablename__ = "dcf_report"
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"))
    growth: Mapped[float | None] = mapped_column(Float, default=None)
    discount: Mapped[float | None] = mapped_column(Float, default=None)
    years: Mapped[int | None] = mapped_column(Integer, default=None)
    safety: Mapped[float | None] = mapped_column(Float, default=None)
    local_path: Mapped[str] = mapped_column(String(512))
    valuation: Mapped[dict | None] = mapped_column(JSON, default=None)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    stock: Mapped["Stock"] = relationship(back_populates="dcf_reports")


class Task(Base):
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str] = mapped_column(String(32))  # download/analysis/dcf/sync_stocks
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stock.id"), default=None)
    params: Mapped[dict | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_summary: Mapped[str | None] = mapped_column(Text, default=None)
    log_path: Mapped[str | None] = mapped_column(String(512), default=None)
    # 下载任务实时进度（仅 task_type=download）：done=已完成(下载+跳过已存在)，total=各财年报文总数
    # 解析失败/非下载任务为 NULL，前端据此隐藏
    progress_done: Mapped[int | None] = mapped_column(Integer, default=None)
    progress_total: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    stock: Mapped["Stock"] = relationship(back_populates="tasks")
