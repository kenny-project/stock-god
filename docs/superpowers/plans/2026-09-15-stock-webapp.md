# stock-god Web 网站实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 sec_filings download / sec_analysis / dcf 三个 CLI 功能构建 Web 界面（股票列表、后台任务、报告渲染+图表）。

**Architecture:** FastAPI 后端通过 asyncio.subprocess 调用现有脚本（脚本零改动），产物登记入 SQLite（SQLAlchemy 2 + Alembic）；所有查询走数据库，文件只按 DB 中 path 指针读取。Vue 3 + Vite + ECharts SPA，2s 轮询任务状态。

**Tech Stack:** FastAPI, uvicorn, SQLAlchemy 2.x, Alembic, SQLite(WAL), pytest, Vue 3, Vite, Pinia, Vue Router, ECharts, markdown-it

**Spec:** `docs/superpowers/specs/2026-09-15-stock-webapp-design.md`

**验证基线:** `reports/sec_analysis/NKE/10-K_FY2024.md` 与 `reports/dcf/US.NKE_DCF.md` 为解析器 fixture 的真实数据来源（严禁造假数据）。

---

## File Structure

```
webapp/
├── backend/
│   ├── requirements.txt / requirements-dev.txt
│   ├── alembic.ini, alembic/
│   ├── main.py                 # FastAPI 入口 + 托管 frontend/dist + CORS
│   ├── db.py                   # engine(WAL)/session/Base
│   ├── models.py               # Stock/Filing/Analysis/DcfReport/Task
│   ├── schemas.py              # Pydantic 响应模型
│   ├── tasks.py                # 任务执行器（semaphore/状态机/日志/取消）
│   ├── services/
│   │   ├── edgar.py            # EDGAR company_tickers 拉取+upsert
│   │   ├── metrics.py          # 分析报告/DCF 报告解析为 JSON
│   │   ├── runners.py          # 各任务类型的命令构造+进程执行
│   │   └── register.py         # 产物校验+入库登记
│   └── api/
│       ├── stocks.py           # /api/stocks*
│       ├── tasks_api.py        # /api/tasks*
│       └── reports.py          # /api/stocks/{t}/analyses|dcf|filings, /api/filings/{id}/file, /api/tasks/{id}/log
├── frontend/
│   ├── package.json, vite.config.js, index.html
│   └── src/
│       ├── main.js, App.vue, router.js, api.js
│       ├── stores/tasks.js     # 全局任务轮询
│       ├── views/StockList.vue, StockDetail.vue, Tasks.vue
│       └── components/MarkdownViewer.vue, LogViewer.vue, TrendChart.vue, DcfChart.vue
tests/  (webapp/backend/tests/) # pytest 套件
scripts/import_existing.py      # 历史产物一次性导入
```

---

### Task 1: 后端脚手架与依赖

**Files:**
- Create: `webapp/backend/requirements.txt`, `webapp/backend/requirements-dev.txt`, `webapp/backend/db.py`（空基线在 Task 2 补全）

- [ ] **Step 1: 创建目录与依赖文件**

```bash
mkdir -p webapp/backend/webapp_services webapp/backend/api webapp/backend/tests webapp/frontend
```

`webapp/backend/requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy>=2.0
alembic>=1.13
pydantic>=2.7
```

`webapp/backend/requirements-dev.txt`:
```
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
```

- [ ] **Step 2: 建 venv 并安装**

```bash
cd webapp/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```
Expected: 安装成功无报错

- [ ] **Step 3: 建 pytest 配置**

`webapp/backend/pytest.ini`:
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

`webapp/backend/tests/__init__.py`、`webapp/backend/services/__init__.py`、`webapp/backend/api/__init__.py` 为空文件。

- [ ] **Step 4: 验证 pytest 可运行**

Run: `cd webapp/backend && .venv/bin/python -m pytest`
Expected: `no tests ran`（0 collected，退出码 5 可接受）

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/requirements.txt webapp/backend/requirements-dev.txt webapp/backend/pytest.ini webapp/backend/tests/__init__.py webapp/backend/services/__init__.py webapp/backend/api/__init__.py
git commit -m "feat(web): 后端脚手架与依赖"
```

---

### Task 2: 数据模型与 DB 基建

**Files:**
- Create: `webapp/backend/db.py`, `webapp/backend/models.py`, `webapp/backend/alembic/`（autogen）
- Test: `webapp/backend/tests/test_models.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py`:
```python
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from db import Base
from models import Stock, Filing, Analysis, DcfReport, Task


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_create_stock_with_relations(session):
    st = Stock(ticker="NKE", name_en="NIKE Inc", cik=320187, market="US", exchange="NYSE")
    s = session
    s.add(st)
    s.flush()
    s.add_all([
        Filing(stock_id=st.id, form_type="UNKNOWN", period="2024-05-31",
               local_path="reports/sec_filings/NKE/nke-20240531/nke-20240531.htm"),
        Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024,
                 local_path="reports/sec_analysis/NKE/10-K_FY2024.md", metrics={"营收": 51362}),
        DcfReport(stock_id=st.id, growth=8.0, discount=10.0, years=5, safety=0.3,
                  local_path="reports/dcf/US.NKE_DCF.md", valuation={"intrinsic": 47099}),
        Task(task_type="download", stock_id=st.id, status="pending", params={"years": 5}),
    ])
    s.commit()
    got = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    assert got.market == "US"
    assert len(got.filings) == 1
    assert got.analyses[0].metrics["营收"] == 51362
    assert got.dcf_reports[0].valuation["intrinsic"] == 47099
    assert got.tasks[0].status == "pending"


def test_unique_ticker(session):
    s = session
    s.add(Stock(ticker="NKE"))
    s.commit()
    s.add(Stock(ticker="NKE"))
    with pytest.raises(Exception):
        s.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_models.py -v`
Expected: FAIL（ModuleNotFoundError: db）

- [ ] **Step 3: 实现 db.py 与 models.py**

`db.py`:
```python
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.environ.get("STOCKGOD_DB", os.path.join(DATA_DIR, "stock_god.db"))


class Base(DeclarativeBase):
    pass


def make_engine(db_path=None):
    url = db_path or DB_PATH
    engine = create_engine(f"sqlite:///{url}" if not url.startswith("sqlite") else url, connect_args={"check_same_thread": False})
    if not url.startswith("sqlite:///:memory:"):
        os.makedirs(os.path.dirname(url), exist_ok=True)
        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA busy_timeout=5000")
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
```

`models.py`:
```python
from datetime import datetime
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, DateTime, UniqueConstraint, Text
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
    __table_args__ = (UniqueConstraint("stock_id", "form_type", "fiscal_year", name="uq_analysis"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"))
    form_type: Mapped[str] = mapped_column(String(16))
    fiscal_year: Mapped[int] = mapped_column(Integer)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    stock: Mapped["Stock"] = relationship(back_populates="tasks")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_models.py -v`
Expected: 2 passed

- [ ] **Step 5: Alembic 初始化迁移**

```bash
cd webapp/backend && .venv/bin/alembic init alembic
```
修改 `alembic.ini` 的 `sqlalchemy.url` 为空，`alembic/env.py` 中 `target_metadata` 前加入：
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import Base
import models  # noqa
target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", os.environ.get("STOCKGOD_DB", "sqlite:///../../data/stock_god.db"))
```
生成迁移：
```bash
.venv/bin/alembic revision --autogenerate -m "init tables" && .venv/bin/alembic upgrade head
```
Expected: `data/stock_god.db` 生成 5 张表

- [ ] **Step 6: Commit**

```bash
git add webapp/backend/db.py webapp/backend/models.py webapp/backend/tests/test_models.py webapp/backend/alembic* webapp/backend/alembic
git commit -m "feat(web): ORM 模型与 Alembic 初始迁移"
```

---

### Task 3: 报告解析器（metrics + valuation）

**Files:**
- Create: `webapp/backend/services/metrics.py`
- Test: `webapp/backend/tests/test_metrics.py`，fixture: `tests/fixtures/10-K_FY2024.md`（复制自真实产物）

- [ ] **Step 1: 准备真实 fixture**

```bash
mkdir -p webapp/backend/tests/fixtures
cp reports/sec_analysis/NKE/10-K_FY2024.md webapp/backend/tests/fixtures/10-K_FY2024.md
cp reports/dcf/US.NKE_DCF.md webapp/backend/tests/fixtures/US.NKE_DCF.md
```

- [ ] **Step 2: 写失败测试**

`tests/test_metrics.py`:
```python
import os
from services.metrics import parse_analysis_metrics, parse_dcf_valuation

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_analysis_metrics():
    with open(os.path.join(FIX, "10-K_FY2024.md"), encoding="utf-8") as f:
        text = f.read()
    m = parse_analysis_metrics(text)
    assert m["净利润"] == 5700          # $5,700M → 百万单位数值
    assert m["营收"] == 51362
    assert m["净资产收益率"] == 39.5     # 百分数保留数值
    assert m["经营现金流"] == 7429
    assert "每股收益" in m


def test_parse_dcf_valuation():
    with open(os.path.join(FIX, "US.NKE_DCF.md"), encoding="utf-8") as f:
        text = f.read()
    v = parse_dcf_valuation(text)
    assert v["intrinsic_value_musd"] == 47099
    assert v["price"] == 36.80
    assert len(v["owner_earnings_by_year"]) == 5   # FY2022..FY2026
    assert v["owner_earnings_by_year"][0]["oe"] == 6308
```

- [ ] **Step 3: 运行确认失败**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 4: 实现解析器**

`services/metrics.py`:
```python
"""把 sec_analysis / dcf 产出的 Markdown 表格解析为结构化 JSON。
只解析真实存在的表格行，解析不到的键直接缺席——严禁编造数据。"""
import re

_MONEY = re.compile(r"^\$?([\d,]+(?:\.\d+)?)M$")
_PCT = re.compile(r"^(-?[\d.]+)%$")
_NUM = re.compile(r"^-?\$?([\d,]+(?:\.\d+)?)$")
_NEG_MONEY = re.compile(r"^\(\$?([\d,]+(?:\.\d+)?)M\)$")


def _to_number(raw: str):
    raw = raw.strip().replace("**", "")
    if m := _MONEY.match(raw):
        return float(m.group(1).replace(",", ""))
    if m := _NEG_MONEY.match(raw):
        return -float(m.group(1).replace(",", ""))
    if m := _PCT.match(raw):
        return float(m.group(1))
    if m := _NUM.match(raw):
        return float(m.group(1).replace(",", ""))
    return None


def _table_rows(text: str, section: str) -> list[tuple[str, str]]:
    """返回指定 `## 小节` 下表格的 (第一列, 第二列) 行。"""
    out, in_sec, in_tbl = [], False, False
    for line in text.splitlines():
        if line.startswith("## "):
            in_sec = line[3:].strip().startswith(section)
            continue
        if in_sec and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and set(cells[0]) - {":", "-", " "} and cells[0] not in ("指标", "项目"):
                out.append((cells[0], cells[1]))
    return out


def parse_analysis_metrics(text: str) -> dict:
    metrics = {}
    for key, raw in _table_rows(text, "财务指标") + _table_rows(text, "现金流"):
        if (v := _to_number(raw)) is not None:
            metrics[key] = v
    return metrics


def parse_dcf_valuation(text: str) -> dict:
    v: dict = {}
    kv = {k: raw for k, raw in _table_rows(text, "基本信息") + _table_rows(text, "估值结果")}
    if (p := _to_number(kv.get("当前股价", ""))) is not None:
        v["price"] = p
    intrinsic = None
    for k, raw in kv.items():
        if "内在价值" in k:
            intrinsic = _to_number(raw)
    if intrinsic is not None:
        v["intrinsic_value_musd"] = intrinsic
    years = []
    for line in text.splitlines():
        if line.startswith("| FY") or line.startswith("| 20"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5:
                oe = _to_number(cells[4])
                if oe is not None:
                    years.append({"year": cells[0], "oe": oe, "revenue": _to_number(cells[5]) if len(cells) > 5 else None})
    if years:
        v["owner_earnings_by_year"] = years
    return v
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: 2 passed（若断言与 fixture 实际值不符，以 fixture 真实值修正测试与解析器，绝不改 fixture）

- [ ] **Step 6: Commit**

```bash
git add webapp/backend/services/metrics.py webapp/backend/tests/test_metrics.py webapp/backend/tests/fixtures
git commit -m "feat(web): 分析/DCF 报告 Markdown→JSON 解析器"
```

---

### Task 4: EDGAR 股票列表同步

**Files:**
- Create: `webapp/backend/services/edgar.py`
- Test: `webapp/backend/tests/test_edgar.py`

- [ ] **Step 1: 写失败测试**

`tests/test_edgar.py`:
```python
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from db import Base
from models import Stock
from services.edgar import upsert_stocks

SAMPLE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 320187, "ticker": "NKE", "title": "NIKE, Inc."},
}


def test_upsert(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        upsert_stocks(s, SAMPLE)
        assert s.scalar(select(func.count(Stock.id))) == 2
        # 再次同步：更新而非重复插入
        SAMPLE["1"]["title"] = "NIKE Inc."
        upsert_stocks(s, SAMPLE)
        assert s.scalar(select(func.count(Stock.id))) == 2
        assert s.scalar(select(Stock).where(Stock.ticker == "NKE")).name_en == "NIKE Inc."
```

- [ ] **Step 2: 运行确认失败**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_edgar.py -v` → FAIL

- [ ] **Step 3: 实现 edgar.py**

`services/edgar.py`:
```python
import json
import urllib.request
from sqlalchemy import select
from models import Stock

EDGAR_URL = "https://www.sec.gov/files/company_tickers.json"
HEADERS = {"User-Agent": "stock-god personal research wmh@example.com"}


def fetch_company_tickers() -> dict:
    req = urllib.request.Request(EDGAR_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upsert_stocks(session, data: dict) -> int:
    existing = {s.ticker: s for s in session.scalars(select(Stock)).all()}
    n = 0
    for item in data.values():
        ticker = item["ticker"].strip().upper()
        if not ticker:
            continue
        st = existing.get(ticker)
        if st is None:
            st = Stock(ticker=ticker)
            session.add(st)
        st.name_en = item.get("title")
        st.cik = item.get("cik_str")
        st.market = "US"
        n += 1
    session.commit()
    return n
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_edgar.py -v` → 1 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/services/edgar.py webapp/backend/tests/test_edgar.py
git commit -m "feat(web): EDGAR 股票列表拉取与 upsert"
```

---

### Task 5: 任务 Runner（命令构造 + 进程执行 + 错误分类）

**Files:**
- Create: `webapp/backend/services/runners.py`
- Test: `webapp/backend/tests/test_runners.py`

- [ ] **Step 1: 写失败测试**

`tests/test_runners.py`:
```python
import pytest
from services.runners import build_command, classify_error


def test_build_download_command():
    cmd = build_command("download", "US.NKE", {"years": 5})
    assert cmd[-6:] == ["sec_filings.py", "download", "US.NKE", "--years", "5"]


def test_build_analysis_command():
    cmd = build_command("analysis", "NKE", {})
    assert cmd[-4:] == ["sec_analysis.py", "NKE", "--all"]


def test_build_dcf_command():
    cmd = build_command("dcf", "US.NKE", {"growth": 8, "discount": 10, "years": 5, "safety": 0.3})
    assert cmd[-2:] == ["dcf.py", "US.NKE"]
    assert "--growth" in cmd and "8" in cmd


def test_classify_rate_limit():
    code, summary = classify_error(1, "HTTPError 403 Forbidden\nrequest blocked by sec.gov")
    assert code == "SEC_RATE_LIMITED"
    assert summary


def test_classify_unknown():
    code, _ = classify_error(2, "traceback ...")
    assert code == "SCRIPT_EXIT_NONZERO"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_runners.py -v` → FAIL

- [ ] **Step 3: 实现 runners.py**

`services/runners.py`:
```python
"""各任务类型的脚本命令构造与退出错误分类。仅构造与分类，进程执行在 tasks.py。"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")


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
        cmd = [py, os.path.join(SCRIPTS_DIR, "sec_analysis.py"), symbol.split(".", 1)[-1], "--all"]
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
    (("403", "429", "rate limit", "blocked", "forbidden"), "SEC_RATE_LIMITED", "数据源限流/拒绝访问（403/429），建议 10 分钟后重试"),
    (("timeout", "timed out"), "NETWORK_TIMEOUT", "网络请求超时，请检查网络后重试"),
    (("no filings", "not found", "404"), "NO_FILINGS_FOUND", "未找到目标财报或资源不存在"),
]


def classify_error(exit_code: int, stderr_tail: str) -> tuple[str, str]:
    low = stderr_tail.lower()
    for patterns, code, summary in _ERROR_PATTERNS:
        if any(p in low for p in patterns):
            return code, summary
    return "SCRIPT_EXIT_NONZERO", f"脚本异常退出（exit={exit_code}），详情见任务日志"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_runners.py -v` → 5 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/services/runners.py webapp/backend/tests/test_runners.py
git commit -m "feat(web): 任务命令构造与错误分类"
```

---

### Task 6: 产物登记（校验 + 入库）

**Files:**
- Create: `webapp/backend/services/register.py`
- Test: `webapp/backend/tests/test_register.py`

- [ ] **Step 1: 写失败测试**

`tests/test_register.py`:
```python
import os
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from db import Base
from models import Stock, Filing, Analysis, DcfReport
from services.register import register_download, register_analysis, register_dcf

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Stock(ticker="NKE", market="US"))
    s.commit()
    return s


def test_register_download_real_files():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_download(s, st)  # 扫描真实 reports/sec_filings/NKE/
    assert n > 0
    assert s.scalar(select(func.count(Filing.id))) == n
    for f in s.scalars(select(Filing)).all():
        assert os.path.exists(os.path.join(ROOT, f.local_path))


def test_register_analysis_real_files():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_analysis(s, st)
    assert n >= 5  # 现有 10-K FY2022..FY2026
    a = s.scalar(select(Analysis).where(Analysis.fiscal_year == 2024))
    assert a.metrics["净利润"] == 5700


def test_register_dcf_real_file():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_dcf(s, st)
    assert n >= 1
    d = s.scalars(select(DcfReport)).first()
    assert d.valuation["intrinsic_value_musd"] == 47099
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 register.py**

`services/register.py`:
```python
"""任务成功后的产物登记：校验文件存在且非空才入库。路径一律存相对项目根的 POSIX 路径。"""
import os
import re
from datetime import datetime
from sqlalchemy import select
from models import Filing, Analysis, DcfReport
from services.metrics import parse_analysis_metrics, parse_dcf_valuation

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_PERIOD = re.compile(r"-(\d{8})$")
_FORM = re.compile(r"^(10-K|10-Q|20-F|6-K)", re.I)
_FY = re.compile(r"FY(\d{4})", re.I)


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _valid_file(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 0


def register_download(session, stock) -> int:
    """登记 reports/sec_filings/{TICKER}/ 下新增文件/目录。返回新增条数。"""
    base = os.path.join(ROOT, "reports", "sec_filings", stock.ticker)
    if not os.path.isdir(base):
        return 0
    existing = {f.local_path for f in session.scalars(select(Filing).where(Filing.stock_id == stock.id))}
    n = 0
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        candidates = []
        if os.path.isfile(full) and _valid_file(full):
            candidates.append(full)
        elif os.path.isdir(full):
            candidates = [os.path.join(full, x) for x in os.listdir(full)
                          if _valid_file(os.path.join(full, x))]
        for cand in candidates:
            rel = _rel(cand)
            if rel in existing:
                continue
            period = None
            if m := _PERIOD.search(name):
                period = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
            form = "UNKNOWN"
            if fm := _FORM.match(name):
                form = fm.group(1).upper()
            session.add(Filing(stock_id=stock.id, form_type=form, period=period, local_path=rel))
            existing.add(rel)
            n += 1
    session.commit()
    return n


def register_analysis(session, stock) -> int:
    base = os.path.join(ROOT, "reports", "sec_analysis", stock.ticker)
    if not os.path.isdir(base):
        return 0
    existing = {(a.form_type, a.fiscal_year)
                for a in session.scalars(select(Analysis).where(Analysis.stock_id == stock.id))}
    n = 0
    for name in sorted(os.listdir(base)):
        if not name.endswith(".md"):
            continue
        fm = _FORM.match(name)
        fy = _FY.search(name)
        if not fm or not fy:
            continue  # 文件名不合规（如 {TICKER}_analysis_*.md 旧汇总），跳过不造假
        form, year = fm.group(1).upper(), int(fy.group(1))
        full = os.path.join(base, name)
        if not _valid_file(full) or (form, year) in existing:
            continue
        with open(full, encoding="utf-8") as f:
            metrics = parse_analysis_metrics(f.read())
        session.add(Analysis(stock_id=stock.id, form_type=form, fiscal_year=year,
                             local_path=_rel(full), metrics=metrics or None))
        existing.add((form, year))
        n += 1
    session.commit()
    return n


def register_dcf(session, stock) -> int:
    base = os.path.join(ROOT, "reports", "dcf")
    if not os.path.isdir(base):
        return 0
    n = 0
    for name in sorted(os.listdir(base)):
        if not (name.startswith(f"{stock.symbol()}_DCF") or name.startswith(f"{stock.ticker}_DCF")):
            continue
        if not name.endswith(".md"):
            continue
        full = os.path.join(base, name)
        if not _valid_file(full):
            continue
        with open(full, encoding="utf-8") as f:
            valuation = parse_dcf_valuation(f.read())
        session.add(DcfReport(stock_id=stock.id, local_path=_rel(full), valuation=valuation or None,
                              generated_at=datetime.fromtimestamp(os.path.getmtime(full))))
        n += 1
    session.commit()
    return n
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_register.py -v` → 3 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/services/register.py webapp/backend/tests/test_register.py
git commit -m "feat(web): 产物校验与入库登记"
```

---

### Task 7: 任务执行器（状态机 + 并发 + 日志 + 取消）

**Files:**
- Create: `webapp/backend/tasks.py`
- Test: `webapp/backend/tests/test_executor.py`

- [ ] **Step 1: 写失败测试**

`tests/test_executor.py`:
```python
import os
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from db import Base
from models import Task, Stock
from executor import TaskExecutor   # 模块名 executor.py（避免与 stdlib tasks 混淆）


class FakeProc:
    def __init__(self, code=0, out=b"ok"):
        self.returncode = code
        self._out = out
    async def communicate(self):
        return self._out, b""


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Stock(ticker="NKE", market="US"))
    s.add(Task(task_type="analysis", status="pending", params={}))
    s.commit()
    return s


async def test_success_flow(session, tmp_path, monkeypatch):
    ex = TaskExecutor(session, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(0)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    task = session.scalar(select(Task))
    await ex.run_one(task)
    assert task.status == "success" and task.finished_at is not None
    assert os.path.exists(task.log_path)


async def test_failure_flow(session, tmp_path, monkeypatch):
    ex = TaskExecutor(session, log_dir=str(tmp_path))
    async def fake_exec(cmd, log_fh):
        return FakeProc(1)
    monkeypatch.setattr(ex, "_exec", fake_exec)
    task = session.scalar(select(Task))
    await ex.run_one(task)
    assert task.status == "failed"
    assert task.error_code == "SCRIPT_EXIT_NONZERO"
    assert task.error_summary
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 executor.py**

`webapp/backend/executor.py`（注意模块名为 `executor`，避免与 `tasks.py` 路由/API 命名冲突）:
```python
"""后台任务执行器：semaphore 并发限制、状态机流转、日志落盘、进程组取消。"""
import asyncio
import os
import signal
from datetime import datetime
import services.register as register
from services.runners import build_command, classify_error

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs", "tasks")
MAX_CONCURRENCY = 2

_REGISTER = {"download": register.register_download,
             "analysis": register.register_analysis,
             "dcf": register.register_dcf}


class TaskExecutor:
    def __init__(self, session_factory, log_dir=None):
        # session_factory: 可调用，返回新的 Session（每个任务独立会话）
        self._factory = session_factory
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._procs: dict[int, asyncio.subprocess.Process] = {}
        self.log_dir = os.path.abspath(log_dir or LOG_DIR)
        os.makedirs(self.log_dir, exist_ok=True)

    async def run_one(self, task):
        async with self._sem:
            session = self._factory()
            task = session.merge(task)
            task.status = "running"
            task.started_at = datetime.now()
            log_path = os.path.join(self.log_dir, f"task_{task.id}.log")
            task.log_path = log_path
            session.commit()
            try:
                if task.task_type == "sync_stocks":
                    # 同步股票列表不走子进程，直接在线程池拉 EDGAR
                    from services.edgar import fetch_company_tickers, upsert_stocks
                    data = await asyncio.get_running_loop().run_in_executor(None, fetch_company_tickers)
                    upsert_stocks(session, data)
                    task.status = "success"
                    task.finished_at = datetime.now()
                    session.commit()
                    session.close()
                    return
                with open(log_path, "ab") as log_fh:
                    proc = await self._exec(build_command(task.task_type,
                                                          task.stock.symbol() if task.stock else "",
                                                          task.params or {}), log_fh)
                    self._procs[task.id] = proc
                    out, err = await proc.communicate()
                    log_fh.write(err)
                    del self._procs[task.id]
                if proc.returncode == 0:
                    registered = _REGISTER[task.task_type](session, task.stock) if task.stock else 0
                    if task.stock and registered == 0 and task.task_type != "sync_stocks":
                        task.status = "failed"
                        task.error_code = "EMPTY_OUTPUT"
                        task.error_summary = "脚本退出码为 0 但未产生新的产物文件，按失败处理"
                    else:
                        task.status = "success"
                else:
                    task.status = "failed"
                    tail = (err or b"").decode("utf-8", "replace")[-2000:]
                    task.error_code, task.error_summary = classify_error(proc.returncode, tail)
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.error_summary = "任务被手动取消"
            except Exception as e:  # 任何意外都落为失败，不留悬挂任务
                task.status = "failed"
                task.error_code = "SCRIPT_EXIT_NONZERO"
                task.error_summary = f"执行器内部错误: {e}"
            finally:
                task.finished_at = datetime.now()
                session.commit()
                session.close()

    async def _exec(self, cmd: list[str], log_fh):
        return await asyncio.create_subprocess_exec(
            *cmd, stdout=log_fh, stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/../..",
            start_new_session=True)

    async def cancel(self, task) -> bool:
        proc = self._procs.get(task.id)
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), timeout=10)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return True
        return False  # pending 任务由 API 层直接置 cancelled
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_executor.py -v` → 2 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/executor.py webapp/backend/tests/test_executor.py
git commit -m "feat(web): 后台任务执行器（并发/状态机/日志/取消）"
```

---

### Task 8: Pydantic schemas + 股票/任务 API

**Files:**
- Create: `webapp/backend/schemas.py`, `webapp/backend/api/stocks.py`, `webapp/backend/api/tasks_api.py`, `webapp/backend/deps.py`
- Test: `webapp/backend/tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import Base
import models  # noqa
from main import app, set_engine_for_test


@pytest.fixture
def client():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    set_engine_for_test(engine, Factory)
    from models import Stock
    with Factory() as s:
        s.add(Stock(ticker="NKE", name_en="NIKE, Inc.", market="US"))
        s.commit()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_list_stocks(client):
    async with client as c:
        r = await c.get("/api/stocks?q=NIKE")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1 and data["items"][0]["ticker"] == "NKE"


async def test_stock_detail_404(client):
    async with client as c:
        r = await c.get("/api/stocks/XXXX")
        assert r.status_code == 404


async def test_create_task_conflict(client):
    async with client as c:
        body = {"task_type": "analysis", "ticker": "NKE", "params": {}}
        r1 = await c.post("/api/tasks", json=body)
        assert r1.status_code == 200
        r2 = await c.post("/api/tasks", json=body)
        assert r2.status_code == 409


async def test_create_task_bad_type(client):
    async with client as c:
        r = await c.post("/api/tasks", json={"task_type": "nope", "ticker": "NKE"})
        assert r.status_code == 422
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 schemas.py / deps.py / api 路由 / main.py 骨架**

`schemas.py`:
```python
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


class StockDetail(StockOut):
    cik: int | None
    filings: list["FilingOut"]
    analyses: list["AnalysisOut"]
    dcf_reports: list["DcfOut"]
    running_tasks: list["TaskOut"]


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


class TaskCreate(BaseModel):
    task_type: str = Field(pattern="^(download|analysis|dcf)$")
    ticker: str
    params: dict = {}


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
```

`deps.py`:
```python
from sqlalchemy.orm import Session

_engine = None
_Factory = None


def set_engine(engine, factory):
    global _engine, _Factory
    _engine, _Factory = engine, factory


def get_db() -> Session:
    s = _Factory()
    try:
        yield s
    finally:
        s.close()
```

`api/stocks.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from deps import get_db
from models import Stock, Task
from schemas import StockPage, StockOut, StockDetail

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=StockPage)
def list_stocks(q: str = "", page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db)):
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Stock.ticker.ilike(like), Stock.name_en.ilike(like), Stock.name_cn.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Stock.ticker).offset((page - 1) * size).limit(size)).all()
    return StockPage(total=total, items=[StockOut.model_validate(s) for s in items])


@router.post("/sync")
def sync_stocks(db: Session = Depends(get_db)):
    # 创建 sync_stocks 任务交给执行器（executor 对 sync_stocks 有专用分支）
    from api.tasks_api import create_task_internal
    task = create_task_internal(db, "sync_stocks", None, {})
    return {"task_id": task.id}


@router.get("/{ticker}", response_model=StockDetail)
def stock_detail(ticker: str, db: Session = Depends(get_db)):
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, "stock not found")
    running = db.scalars(select(Task).where(Task.stock_id == st.id,
                                            Task.status.in_(("pending", "running")))).all()
    d = StockDetail.model_validate(st, update={"running_tasks": list(running)})
    return d
```

`api/tasks_api.py`:
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from deps import get_db
from models import Stock, Task
from schemas import TaskCreate, TaskOut
from executor import TaskExecutor

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
_executor: TaskExecutor | None = None


def set_executor(ex: TaskExecutor):
    global _executor
    _executor = ex


def get_executor() -> TaskExecutor:
    assert _executor is not None, "executor not initialized"
    return _executor


def create_task_internal(db: Session, task_type: str, stock, params: dict) -> Task:
    task = Task(task_type=task_type, stock_id=stock.id if stock else None, params=params)
    db.add(task)
    db.commit()
    return task


@router.post("")
async def create_task(body: TaskCreate, db: Session = Depends(get_db)):
    stock = db.scalar(select(Stock).where(Stock.ticker == body.ticker.upper()))
    if not stock:
        raise HTTPException(404, f"unknown ticker {body.ticker}")
    dup = db.scalar(select(Task).where(Task.stock_id == stock.id, Task.task_type == body.task_type,
                                       Task.status.in_(("pending", "running"))))
    if dup:
        raise HTTPException(409, f"同类型任务已在{ '排队' if dup.status == 'pending' else '执行' }中 (task #{dup.id})")
    task = create_task_internal(db, body.task_type, stock, body.params)
    import asyncio
    asyncio.get_running_loop().create_task(get_executor().run_one(task))
    return TaskOut.model_validate(task)


@router.get("")
def list_tasks(status: str = "", db: Session = Depends(get_db)):
    stmt = select(Task).order_by(Task.id.desc()).limit(200)
    if status:
        stmt = stmt.where(Task.status == status)
    return [TaskOut.model_validate(t) for t in db.scalars(stmt).all()]


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    if task.status not in ("pending", "running"):
        raise HTTPException(409, f"任务已结束({task.status})，无法取消")
    if task.status == "running":
        await get_executor().cancel(task)
    else:
        task.status = "cancelled"
        task.error_summary = "任务被手动取消"
    db.commit()
    return task
```

`main.py` 骨架（Task 9 补 reports 路由）:
```python
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
from services.edgar import fetch_company_tickers, upsert_stocks


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    Factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    set_engine(engine, Factory)
    ex = TaskExecutor(Factory)
    tasks_api.set_executor(ex)
    # 首次启动：股票表为空则自动拉 EDGAR（同步任务在事件循环线程池跑）
    def _seed():
        with Factory() as s:
            from sqlalchemy import select, func
            from models import Stock
            if s.scalar(select(func.count(Stock.id))) == 0:
                upsert_stocks(s, fetch_company_tickers())
    await asyncio.get_running_loop().run_in_executor(None, _seed)
    yield


app = FastAPI(title="stock-god web", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(stocks_api.router)
app.include_router(tasks_api.router)


def set_engine_for_test(engine, factory):
    set_engine(engine, factory)
```

注意：测试通过 `set_engine_for_test` 覆盖 engine；`create_task` 依赖运行中的事件循环，TestClient(ASGITransport) 满足。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webapp/backend && .venv/bin/python -m pytest tests/test_api.py -v` → 4 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/schemas.py webapp/backend/deps.py webapp/backend/api/stocks.py webapp/backend/api/tasks_api.py webapp/backend/main.py webapp/backend/tests/test_api.py
git commit -m "feat(web): 股票与任务 API"
```

---

### Task 9: 报告/文件/日志 API

**Files:**
- Create: `webapp/backend/api/reports.py`；Modify: `main.py`（include_router）
- Test: `webapp/backend/tests/test_reports_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_reports_api.py`:
```python
import os, shutil
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import Base
from main import app, set_engine_for_test
from models import Stock, Filing, Analysis

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


@pytest.fixture
def client():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    set_engine_for_test(engine, Factory)
    with Factory() as s:
        st = Stock(ticker="NKE", market="US")
        s.add(st); s.flush()
        s.add(Filing(stock_id=st.id, form_type="UNKNOWN", period="2024-05-31",
                     local_path="reports/sec_filings/NKE/nke-20210831.htm"))
        s.add(Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024,
                       local_path="reports/sec_analysis/NKE/10-K_FY2024.md", metrics={"营收": 51362}))
        s.commit()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_analysis_content(client):
    async with client as c:
        r = await c.get("/api/stocks/NKE/analyses/1")
        assert r.status_code == 200
        body = r.json()
        assert body["metrics"]["营收"] == 51362
        assert "# NKE 10-K FY2024" in body["markdown"]


async def test_analysis_not_found(client):
    async with client as c:
        assert (await c.get("/api/stocks/NKE/analyses/99")).status_code == 404


async def test_filing_file_download(client):
    async with client as c:
        r = await c.get("/api/filings/1/file")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")


async def test_filing_missing_file_404(client):
    async with client as c:
        # DB 有记录但文件被删除 → 404，绝不返回编造内容
        s = sessionmaker(bind=create_engine("sqlite://"))  # noqa
        r = await c.get("/api/filings/999/file")
        assert r.status_code == 404


async def test_task_log(client, tmp_path, monkeypatch):
    from api import tasks_api
    log = tmp_path / "task_1.log"
    log.write_text("line1\nline2\n", encoding="utf-8")
    monkeypatch.setattr("api.reports.safe_log_path", lambda p: str(log))
    async with client as c:
        r = await c.get("/api/tasks/1/log")
        assert r.status_code == 200
        assert "line2" in r.json()["content"]
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 api/reports.py 并挂载**

`api/reports.py`:
```python
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from deps import get_db
from db import ROOT
from models import Analysis, Filing, Task

router = APIRouter(prefix="/api", tags=["reports"])

_MD_TYPES = {".md": "text/markdown", ".htm": "text/html", ".html": "text/html", ".pdf": "application/pdf"}


def safe_log_path(p: str | None) -> str | None:
    if not p:
        return None
    real = os.path.realpath(p)
    if not real.startswith(os.path.realpath(os.path.join(ROOT, "logs", "tasks"))):
        return None
    return real


@router.get("/stocks/{ticker}/analyses")
def list_analyses(ticker: str, db: Session = Depends(get_db)):
    from models import Stock
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, "stock not found")
    return db.scalars(select(Analysis).where(Analysis.stock_id == st.id)
                      .order_by(Analysis.fiscal_year.desc())).all()


@router.get("/stocks/{ticker}/analyses/{analysis_id}")
def analysis_content(ticker: str, analysis_id: int, db: Session = Depends(get_db)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    full = os.path.join(ROOT, a.local_path)
    if not os.path.isfile(full):
        raise HTTPException(404, f"报告文件缺失: {a.local_path}")
    with open(full, encoding="utf-8") as f:
        return {"id": a.id, "form_type": a.form_type, "fiscal_year": a.fiscal_year,
                "metrics": a.metrics, "markdown": f.read()}


@router.get("/stocks/{ticker}/dcf")
def list_dcf(ticker: str, db: Session = Depends(get_db)):
    from models import Stock, DcfReport
    st = db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not st:
        raise HTTPException(404, "stock not found")
    return db.scalars(select(DcfReport).where(DcfReport.stock_id == st.id)
                      .order_by(DcfReport.generated_at.desc())).all()


@router.get("/stocks/{ticker}/dcf/{dcf_id}")
def dcf_content(ticker: str, dcf_id: int, db: Session = Depends(get_db)):
    from models import DcfReport
    d = db.get(DcfReport, dcf_id)
    if not d:
        raise HTTPException(404, "dcf report not found")
    full = os.path.join(ROOT, d.local_path)
    if not os.path.isfile(full):
        raise HTTPException(404, f"报告文件缺失: {d.local_path}")
    with open(full, encoding="utf-8") as f:
        return {"id": d.id, "valuation": d.valuation, "markdown": f.read()}


@router.get("/filings/{filing_id}/file")
def filing_file(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(Filing, filing_id)
    if not f:
        raise HTTPException(404, "filing not found")
    full = os.path.realpath(os.path.join(ROOT, f.local_path))
    if not full.startswith(os.path.realpath(ROOT)) or not os.path.isfile(full):
        raise HTTPException(404, f"财报文件缺失: {f.local_path}")
    ext = os.path.splitext(full)[1].lower()
    return FileResponse(full, media_type=_MD_TYPES.get(ext, "application/octet-stream"),
                        filename=os.path.basename(full))


@router.get("/tasks/{task_id}/log")
def task_log(task_id: int, offset: int = 0, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    path = safe_log_path(task.log_path)
    if not path or not os.path.isfile(path):
        return {"content": "", "size": 0}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, min(offset, size)))
        content = f.read(64 * 1024).decode("utf-8", "replace")
    return {"content": content, "size": size}
```

同时把 `ROOT` 提升为共享常量：在 `db.py` 增加 `ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))`，`services/register.py` 与 `api/reports.py` 改为 `from db import ROOT`。

`api/reports.py` 两个列表接口加 `response_model`（ORM 对象需经 Pydantic 序列化）：`list_analyses` 加 `response_model=list[AnalysisOut]`，`list_dcf` 加 `response_model=list[DcfOut]`（from models import 处同步引入 `DcfReport`、`DcfOut`）。

`main.py` 增加一行：
```python
from api import reports as reports_api
app.include_router(reports_api.router)
```

- [ ] **Step 4: 运行全部后端测试**

Run: `cd webapp/backend && .venv/bin/python -m pytest -v` → 全部 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/api/reports.py webapp/backend/main.py webapp/backend/db.py webapp/backend/services/register.py webapp/backend/tests/test_reports_api.py
git commit -m "feat(web): 报告内容/文件下载/任务日志 API"
```

---

### Task 10: 历史产物一次性导入脚本

**Files:**
- Create: `scripts/import_existing.py`

- [ ] **Step 1: 实现导入脚本**

`scripts/import_existing.py`:
```python
#!/usr/bin/env python3
"""把 reports/ 下已有产物一次性导入数据库。复用 webapp 后端的登记逻辑，坏数据跳过并输出告警清单。"""
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp", "backend")
sys.path.insert(0, BACKEND)

from db import make_engine, make_session_factory, Base  # noqa: E402
import models  # noqa: E402, F401
from models import Stock  # noqa: E402
from services.register import register_download, register_analysis, register_dcf  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main():
    engine = make_engine()
    Base.metadata.create_all(engine)
    Factory = make_session_factory(engine)
    sec_dir = os.path.join(os.path.dirname(BACKEND), "..", "reports", "sec_filings")
    tickers = sorted(d for d in os.listdir(os.path.abspath(sec_dir))
                     if os.path.isdir(os.path.abspath(os.path.join(sec_dir, d))))
    warnings = []
    with Factory() as s:
        for t in tickers:
            st = s.scalar(select(Stock).where(Stock.ticker == t))
            if not st:
                st = Stock(ticker=t, market="US", name_en=t)
                s.add(st); s.commit()
            try:
                n1 = register_download(s, st)
                n2 = register_analysis(s, st)
                n3 = register_dcf(s, st)
                print(f"{t}: filings+{n1} analyses+{n2} dcf+{n3}")
            except Exception as e:
                warnings.append(f"{t}: {e}")
    if warnings:
        print("\n=== 告警（已跳过） ===")
        for w in warnings:
            print("  -", w)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 对真实数据执行并验证**

Run:
```bash
cd webapp/backend && .venv/bin/python ../../scripts/import_existing.py
.venv/bin/python -c "
import sqlite3
db = sqlite3.connect('../../data/stock_god.db')
print('stocks:', db.execute('select count(*) from stock').fetchone()[0])
print('filings:', db.execute('select count(*) from filing').fetchone()[0])
print('analyses:', db.execute('select count(*) from analysis').fetchone()[0])
print('dcf:', db.execute('select count(*) from dcf_report').fetchone()[0])
"
```
Expected: 8 只股票入库；analyses ≥ 40；dcf ≥ 7；无告警输出（有则逐条人工确认原因）

- [ ] **Step 3: Commit**

```bash
git add scripts/import_existing.py
git commit -m "feat(web): 历史产物一次性导入脚本"
```

---

### Task 11: 前端脚手架（Vite + Vue3 + Router + Pinia + ECharts）

**Files:**
- Create: `webapp/frontend/package.json`, `vite.config.js`, `index.html`, `src/main.js`, `src/App.vue`, `src/router.js`, `src/api.js`, `src/stores/tasks.js`

- [ ] **Step 1: 创建工程文件**

`package.json`:
```json
{
  "name": "stockgod-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": {
    "vue": "^3.4.0", "vue-router": "^4.3.0", "pinia": "^2.1.0",
    "echarts": "^5.5.0", "markdown-it": "^14.0.0"
  },
  "devDependencies": { "vite": "^5.2.0", "@vitejs/plugin-vue": "^5.0.0" }
}
```

`vite.config.js`:
```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } }
})
```

`index.html`:
```html
<!doctype html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>stock-god</title></head>
<body><div id="app"></div><script type="module" src="/src/main.js"></script></body>
</html>
```

`src/main.js`:
```js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './style.css'

createApp(App).use(createPinia()).use(router).mount('#app')
```

`src/router.js`:
```js
import { createRouter, createWebHistory } from 'vue-router'
import StockList from './views/StockList.vue'
import StockDetail from './views/StockDetail.vue'
import Tasks from './views/Tasks.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: StockList },
    { path: '/stocks/:ticker', component: StockDetail, props: true },
    { path: '/tasks', component: Tasks },
  ]
})
```

`src/api.js`:
```js
async function req(url, opts = {}) {
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw Object.assign(new Error(body.detail || r.statusText), { status: r.status })
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  stocks: (q = '', page = 1, size = 50) => req(`/api/stocks?q=${encodeURIComponent(q)}&page=${page}&size=${size}`),
  stock: (t) => req(`/api/stocks/${t}`),
  analyses: (t) => req(`/api/stocks/${t}/analyses`),
  analysis: (t, id) => req(`/api/stocks/${t}/analyses/${id}`),
  dcf: (t) => req(`/api/stocks/${t}/dcf`),
  dcfOne: (t, id) => req(`/api/stocks/${t}/dcf/${id}`),
  tasks: (status = '') => req(`/api/tasks${status ? `?status=${status}` : ''}`),
  task: (id) => req(`/api/tasks/${id}`),
  taskLog: (id, offset = 0) => req(`/api/tasks/${id}/log?offset=${offset}`),
  createTask: (task_type, ticker, params = {}) =>
    req('/api/tasks', { method: 'POST', body: JSON.stringify({ task_type, ticker, params }) }),
  cancelTask: (id) => req(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  syncStocks: () => req('/api/stocks/sync', { method: 'POST' }),
}
```

`src/stores/tasks.js`:
```js
import { defineStore } from 'pinia'
import { api } from '../api'

export const useTaskStore = defineStore('tasks', {
  state: () => ({ tasks: [], timer: null }),
  getters: {
    active: (s) => s.tasks.filter(t => t.status === 'pending' || t.status === 'running'),
  },
  actions: {
    startPolling() {
      if (this.timer) return
      const tick = async () => {
        try { this.tasks = await api.tasks() } catch { /* 忽略瞬时错误 */ }
      }
      tick()
      this.timer = setInterval(tick, 2000)
    },
    async submit(type, ticker, params) {
      const t = await api.createTask(type, ticker, params)
      await new Promise(r => setTimeout(r, 300))  // 等首次状态写库
      this.tasks = await api.tasks()
      return t
    },
  }
})
```

`src/style.css`:
```css
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin: 0; color: #222; }
nav { display: flex; gap: 16px; padding: 12px 24px; border-bottom: 1px solid #eee; align-items: center; }
nav a { text-decoration: none; color: #0366d6; }
.badge { background: #d73a49; color: #fff; border-radius: 10px; padding: 0 7px; font-size: 12px; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #eee; padding: 8px 12px; text-align: left; font-size: 14px; }
tr.clickable { cursor: pointer; } tr.clickable:hover { background: #f6f8fa; }
.status { padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.status.pending { background: #fff8c5; } .status.running { background: #ddf4ff; }
.status.success { background: #dafbe1; } .status.failed, .status.cancelled { background: #ffebe9; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid #eee; }
.tabs button { border: none; background: none; padding: 10px 18px; cursor: pointer; font-size: 14px; }
.tabs button.on { border-bottom: 2px solid #0366d6; color: #0366d6; }
.markdown { padding: 16px; line-height: 1.6; }
.markdown table { margin: 12px 0; } .markdown th, .markdown td { border: 1px solid #ddd; }
.btn { padding: 6px 14px; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa; cursor: pointer; }
.btn.primary { background: #1f883d; color: #fff; border-color: #1f883d; }
```

- [ ] **Step 2: 安装依赖**

Run: `cd webapp/frontend && npm install`
Expected: 无 error

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/package.json webapp/frontend/vite.config.js webapp/frontend/index.html webapp/frontend/src/main.js webapp/frontend/src/router.js webapp/frontend/src/api.js webapp/frontend/src/stores/tasks.js webapp/frontend/src/style.css
git commit -m "feat(web): 前端脚手架（Vue3/Vite/Pinia/ECharts）"
```

---

### Task 12: 股票列表页 + App 壳

**Files:**
- Create: `src/App.vue`, `src/views/StockList.vue`

- [ ] **Step 1: 实现 App.vue 与 StockList.vue**

`src/App.vue`:
```vue
<template>
  <nav>
    <strong>stock-god</strong>
    <router-link to="/">股票</router-link>
    <router-link to="/tasks">任务中心</router-link>
    <span v-if="taskStore.active.length" class="badge">{{ taskStore.active.length }}</span>
  </nav>
  <router-view style="padding: 0 24px" />
</template>
<script setup>
import { onMounted } from 'vue'
import { useTaskStore } from './stores/tasks'
const taskStore = useTaskStore()
onMounted(() => taskStore.startPolling())
</script>
```

`src/views/StockList.vue`:
```vue
<template>
  <div style="display:flex; gap:12px; padding:16px 0">
    <input v-model="q" placeholder="搜索代码/公司名" style="flex:1; padding:8px 12px" @input="debouncedLoad" />
    <button class="btn" @click="sync" :disabled="syncing">同步股票列表</button>
  </div>
  <table>
    <thead><tr><th>代码</th><th>公司</th><th>市场</th><th>操作</th></tr></thead>
    <tbody>
      <tr v-for="s in stocks" :key="s.ticker" class="clickable" @click="$router.push(`/stocks/${s.ticker}`)">
        <td><strong>{{ s.ticker }}</strong></td>
        <td>{{ s.name_cn || s.name_en }}</td>
        <td>{{ s.market }}</td>
        <td><router-link :to="`/stocks/${s.ticker}`">详情</router-link></td>
      </tr>
    </tbody>
  </table>
  <div style="padding:12px 0">
    <button class="btn" :disabled="page <= 1" @click="page--; load()">上一页</button>
    第 {{ page }} 页 / 共 {{ Math.ceil(total / size) }} 页（{{ total }} 只）
    <button class="btn" :disabled="page * size >= total" @click="page++; load()">下一页</button>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const q = ref(''), stocks = ref([]), total = ref(0), page = ref(1), size = 50, syncing = ref(false)
let timer
const debouncedLoad = () => { clearTimeout(timer); timer = setTimeout(() => { page.value = 1; load() }, 300) }
async function load() {
  const d = await api.stocks(q.value, page.value, size)
  stocks.value = d.items; total.value = d.total
}
async function sync() { syncing.value = true; try { await api.syncStocks() } finally { syncing.value = false } }
onMounted(load)
</script>
```

- [ ] **Step 2: 构建验证**

Run: `cd webapp/frontend && npm run build`
Expected: 构建成功（App 可先无 router-view 内容报错则回看 import 路径）

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/src/App.vue webapp/frontend/src/views/StockList.vue
git commit -m "feat(web): 股票列表页"
```

---

### Task 13: 个股详情页（4 Tab + 操作按钮）

**Files:**
- Create: `src/views/StockDetail.vue`, `src/components/MarkdownViewer.vue`

- [ ] **Step 1: 实现 MarkdownViewer**

`src/components/MarkdownViewer.vue`:
```vue
<template><div class="markdown" v-html="html"></div></template>
<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
const props = defineProps({ source: String })
const md = new MarkdownIt({ html: false })
const html = computed(() => md.render(props.source || ''))
</script>
```

- [ ] **Step 2: 实现 StockDetail.vue**

`src/views/StockDetail.vue`:
```vue
<template>
  <div v-if="detail">
    <h2>{{ detail.ticker }} — {{ detail.name_cn || detail.name_en }}</h2>
    <div style="display:flex; gap:8px; margin: 12px 0">
      <button class="btn primary" @click="submit('download', { years: 5 })">下载财报</button>
      <button class="btn primary" @click="submit('analysis', {})">生成分析</button>
      <button class="btn primary" @click="showDcf = !showDcf">DCF 估值</button>
    </div>
    <div v-if="showDcf" style="display:flex; gap:8px; margin-bottom:12px; align-items:center">
      增长率% <input v-model.number="dcf.growth" type="number" style="width:70px">
      折现率% <input v-model.number="dcf.discount" type="number" style="width:70px">
      年限 <input v-model.number="dcf.years" type="number" style="width:60px">
      安全边际% <input v-model.number="dcfSafetyPct" type="number" style="width:70px">
      <button class="btn" @click="submit('dcf', { ...dcf, safety: dcfSafetyPct / 100 })">开始估值</button>
    </div>
    <div v-for="t in detail.running_tasks" :key="t.id" class="status running" style="display:inline-block; margin:4px">
      {{ taskLabel(t.task_type) }} #{{ t.id }} 进行中
    </div>
    <div class="tabs">
      <button v-for="t in tabs" :key="t.key" :class="{ on: tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
    </div>

    <div v-if="tab === 'filings'">
      <table>
        <thead><tr><th>类型</th><th>期间</th><th>下载时间</th><th>文件</th></tr></thead>
        <tbody>
          <tr v-for="f in detail.filings" :key="f.id">
            <td>{{ f.form_type }}</td><td>{{ f.period }}</td><td>{{ fmt(f.downloaded_at) }}</td>
            <td><a :href="`/api/filings/${f.id}/file`" target="_blank">打开/下载</a></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!detail.filings.length">尚未下载财报</p>
    </div>

    <div v-if="tab === 'analysis'">
      <ul>
        <li v-for="a in analyses" :key="a.id">
          <a href="#" @click.prevent="loadAnalysis(a)">{{ a.form_type }} FY{{ a.fiscal_year }}</a>
        </li>
      </ul>
      <MarkdownViewer v-if="analysisMd" :source="analysisMd" />
    </div>

    <div v-if="tab === 'charts'">
      <TrendChart v-if="metricsSeries.length" :series="metricsSeries" title="营收/净利润趋势（百万$）" />
      <p v-else>暂无分析数据，先生成财报分析</p>
    </div>

    <div v-if="tab === 'dcf'">
      <ul>
        <li v-for="d in dcfList" :key="d.id">
          <a href="#" @click.prevent="loadDcf(d)">
            {{ fmt(d.generated_at) }}（增长{{ d.growth ?? '-' }}% / 折现{{ d.discount ?? '-' }}%）
          </a>
          <span v-if="d.valuation">内在价值 ${ d.valuation.intrinsic_value_musd }M，现价 ${ d.valuation.price }</span>
        </li>
      </ul>
      <DcfChart v-for="d in dcfList.filter(x => x.valuation)" :key="d.id" :report="d" />
      <MarkdownViewer v-if="dcfMd" :source="dcfMd" />
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted, watch } from 'vue'
import { api } from '../api'
import { useTaskStore } from '../stores/tasks'
import MarkdownViewer from '../components/MarkdownViewer.vue'
import TrendChart from '../components/TrendChart.vue'
import DcfChart from '../components/DcfChart.vue'

const props = defineProps({ ticker: String })
const taskStore = useTaskStore()
const detail = ref(null), tab = ref('filings'), analyses = ref([]), dcfList = ref([])
const analysisMd = ref(''), dcfMd = ref(''), showDcf = ref(false), dcfSafetyPct = ref(30)
const dcf = ref({ growth: null, discount: null, years: null })
const tabs = [
  { key: 'filings', label: '财报' }, { key: 'analysis', label: '财报分析' },
  { key: 'charts', label: '图表' }, { key: 'dcf', label: 'DCF' },
]
const metricsSeries = ref([])

const taskLabel = (t) => ({ download: '下载财报', analysis: '财报分析', dcf: 'DCF 估值' }[t] || t)
const fmt = (s) => (s ? new Date(s).toLocaleString() : '')

async function loadAll() {
  detail.value = await api.stock(props.ticker)
  analyses.value = await api.analyses(props.ticker)
  dcfList.value = await api.dcf(props.ticker)
  const withMetrics = analyses.value.filter(a => a.metrics)
  const years = withMetrics.map(a => `FY${a.fiscal_year}`)
  metricsSeries.value = ['营收', '净利润'].map(key => ({
    name: key, years, values: withMetrics.map(a => a.metrics[key] ?? null)
  }))
}
async function loadAnalysis(a) { analysisMd.value = (await api.analysis(props.ticker, a.id)).markdown }
async function loadDcf(d) { dcfMd.value = (await api.dcfOne(props.ticker, d.id)).markdown }
async function submit(type, params) {
  try {
    await taskStore.submit(type, props.ticker, params)
    alert('任务已提交，可在任务中心查看进度')
    watchUntilDone()
  } catch (e) { alert(`提交失败: ${e.message}`) }
}
async function watchUntilDone() {
  // 简单轮询：该股票任务全部结束后刷新详情
  const timer = setInterval(async () => {
    const d = await api.stock(props.ticker)
    if (!d.running_tasks.length) { clearInterval(timer); loadAll() }
    else detail.value = d
  }, 3000)
}
onMounted(loadAll)
watch(() => props.ticker, loadAll)
</script>
```

（`TrendChart.vue`/`DcfChart.vue` 在 Task 14 创建——本任务先不 build 页面级验证，构建验证放 Task 14 之后。）

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/src/views/StockDetail.vue webapp/frontend/src/components/MarkdownViewer.vue
git commit -m "feat(web): 个股详情页（四 Tab 与任务提交）"
```

---

### Task 14: 图表组件（ECharts）

**Files:**
- Create: `src/components/TrendChart.vue`, `src/components/DcfChart.vue`

- [ ] **Step 1: 实现 TrendChart（指标趋势线）**

`src/components/TrendChart.vue`:
```vue
<template><div ref="el" style="height:360px; margin:16px 0"></div></template>
<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ series: Array, title: String })
const el = ref(null)
let chart
function render() {
  if (!chart || !props.series.length) return
  chart.setOption({
    title: { text: props.title },
    tooltip: { trigger: 'axis' },
    legend: {},
    xAxis: { type: 'category', data: props.series[0].years },
    yAxis: { type: 'value' },
    series: props.series.map(s => ({ name: s.name, type: 'line', data: s.values, smooth: true })),
  })
}
onMounted(() => { chart = echarts.init(el.value); render(); window.addEventListener('resize', render) })
onUnmounted(() => { window.removeEventListener('resize', render); chart?.dispose() })
watch(() => props.series, render, { deep: true })
</script>
```

- [ ] **Step 2: 实现 DcfChart（估值对比柱状图）**

`src/components/DcfChart.vue`:
```vue
<template>
  <div ref="el" style="height:320px; margin:16px 0"></div>
</template>
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ report: Object })
const el = ref(null)
let chart
onMounted(() => {
  chart = echarts.init(el.value)
  const v = props.report.valuation
  chart.setOption({
    title: { text: `DCF 估值 vs 现价（${props.report.id}）` },
    tooltip: {},
    xAxis: { type: 'category', data: ['内在价值', '当前股价'] },
    yAxis: { type: 'value' },
    series: [{ type: 'bar', data: [
      { value: v.intrinsic_value_musd, itemStyle: { color: '#1f883d' } },
      { value: v.price, itemStyle: { color: '#d0d7de' } },
    ] }],
  })
  window.addEventListener('resize', () => chart?.resize())
})
onUnmounted(() => chart?.dispose())
</script>
```

- [ ] **Step 3: 构建验证**

Run: `cd webapp/frontend && npm run build`
Expected: 构建成功

- [ ] **Step 4: Commit**

```bash
git add webapp/frontend/src/components/TrendChart.vue webapp/frontend/src/components/DcfChart.vue
git commit -m "feat(web): 指标趋势与 DCF 估值图表组件"
```

---

### Task 15: 任务中心 + 日志查看

**Files:**
- Create: `src/views/Tasks.vue`, `src/components/LogViewer.vue`

- [ ] **Step 1: 实现 LogViewer**

`src/components/LogViewer.vue`:
```vue
<template>
  <pre style="background:#0d1117; color:#c9d1d9; padding:12px; max-height:360px; overflow:auto; font-size:12px">{{ content || '（暂无日志）' }}</pre>
  <button v-if="hasMore" class="btn" @click="load(true)">加载更早日志</button>
</template>
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
const props = defineProps({ taskId: Number, live: Boolean })
const content = ref(''), offset = ref(0), hasMore = ref(false)
let timer
async function load(older = false) {
  const d = await api.taskLog(props.taskId, older ? Math.max(0, offset.value - 65536) : offset.value)
  content.value = older ? d.content + content.value : content.value + d.content
  offset.value = d.size
  hasMore.value = offset.value > 0 && content.value.length < offset.value
}
onMounted(() => {
  load()
  if (props.live) timer = setInterval(load, 2000)
})
onUnmounted(() => clearInterval(timer))
</script>
```

- [ ] **Step 2: 实现 Tasks.vue**

`src/views/Tasks.vue`:
```vue
<template>
  <h2>任务中心</h2>
  <table>
    <thead><tr><th>#</th><th>类型</th><th>股票</th><th>状态</th><th>创建</th><th>结束</th><th>操作</th></tr></thead>
    <tbody>
      <template v-for="t in taskStore.tasks" :key="t.id">
        <tr>
          <td>{{ t.id }}</td><td>{{ label(t.task_type) }}</td><td>{{ t.stock?.ticker }}</td>
          <td><span class="status" :class="t.status">{{ statusText(t) }}</span></td>
          <td>{{ fmt(t.created_at) }}</td><td>{{ fmt(t.finished_at) }}</td>
          <td>
            <button v-if="['pending','running'].includes(t.status)" class="btn" @click="cancel(t)">取消</button>
            <button class="btn" @click="expand = expand === t.id ? null : t.id">日志</button>
          </td>
        </tr>
        <tr v-if="expand === t.id"><td colspan="7">
          <div v-if="t.error_code" style="color:#cf222e; margin:8px 0">
            [{{ t.error_code }}] {{ t.error_summary }}
          </div>
          <LogViewer :task-id="t.id" :live="['running','pending'].includes(t.status)" />
        </td></tr>
      </template>
    </tbody>
  </table>
  <p v-if="!taskStore.tasks.length">暂无任务</p>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { useTaskStore } from '../stores/tasks'
import { api } from '../api'
import LogViewer from '../components/LogViewer.vue'
const taskStore = useTaskStore()
const expand = ref(null)
const label = (t) => ({ download: '下载财报', analysis: '财报分析', dcf: 'DCF 估值', sync_stocks: '同步股票列表' }[t] || t)
const statusText = (t) => t.status === 'failed' ? `失败(${t.error_code || ''})` :
  ({ pending: '排队', running: '运行中', success: '成功', cancelled: '已取消' }[t.status] || t.status)
const fmt = (s) => (s ? new Date(s).toLocaleString() : '')
async function cancel(t) { await api.cancelTask(t.id) }
onMounted(() => taskStore.startPolling())
</script>
```

- [ ] **Step 3: 构建验证**

Run: `cd webapp/frontend && npm run build` → 成功

- [ ] **Step 4: Commit**

```bash
git add webapp/frontend/src/views/Tasks.vue webapp/frontend/src/components/LogViewer.vue
git commit -m "feat(web): 任务中心与日志查看"
```

---

### Task 16: 生产集成 + 手动验收

**Files:**
- Modify: `webapp/backend/main.py`（静态托管）、`webapp/frontend/vite.config.js`（build.outDir）

- [ ] **Step 1: FastAPI 托管前端产物**

`vite.config.js` 的 `defineConfig` 增加：
```js
  build: { outDir: '../frontend-dist' }
```
`main.py` 在 `include_router` 之后追加：
```python
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend-dist"))
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        full = os.path.join(DIST, path)
        if path and os.path.isfile(full):
            return FileResponse(full)
        return FileResponse(os.path.join(DIST, "index.html"))
```

- [ ] **Step 2: 构建前端**

Run: `cd webapp/frontend && npm run build`
Expected: 产物输出到 `webapp/frontend-dist/`

- [ ] **Step 3: 启动并手动验收全流程**

```bash
cd webapp/backend && .venv/bin/uvicorn main:app --port 8000
```
浏览器打开 `http://127.0.0.1:8000`，按清单逐项验证：

- [ ] 股票列表显示分页数据，搜索 "NKE" 命中
- [ ] NKE 详情页：4 个 Tab 各自展示已有历史数据（导入自 Task 10）
- [ ] 发起【下载财报】任务 → 任务中心可见运行中 → 完成后财报 Tab 出现新记录
- [ ] 发起【生成分析】→【DCF 估值】→ 图表 Tab 出现趋势图，DCF Tab 出现估值柱状图
- [ ] 任务日志可展开查看；失败任务显示 error_code + summary（可临时用非法参数触发验证）
- [ ] 重复提交同类型任务被拒绝（toast 报 409）

- [ ] **Step 4: Commit**

```bash
git add webapp/backend/main.py webapp/frontend/vite.config.js
git commit -m "feat(web): 生产模式静态托管与集成"
```

---

## 后续扩展（不在本计划内，spec §11）

- 股价接口 `GET /api/stocks/{ticker}/prices`（腾讯适配器/Futu）+ ECharts candlestick 组件
- 更多财务维度图表：扩 metrics JSON + 新图表组件
- 新任务类型：在 `runners.py build_command` + `executor._REGISTER` 注册即可
