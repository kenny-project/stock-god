# stock-god Web 网站设计文档

日期：2026-09-15
状态：已与用户逐节确认

## 1. 背景与目标

为 stock-god 现有的三个 CLI 功能（SEC 财报下载、财报分析、DCF 估值）构建 Web 界面。

**目标：**

- 网页展示全量股票代码列表（SEC EDGAR company_tickers.json，~10000 只美股）及各股票的财报下载/分析/DCF 状态
- 网页可触发财报下载、分析、DCF 估值，采用后台任务 + 状态轮询
- 分析报告、DCF 报告在线渲染 + 可视化图表（财务指标趋势、DCF 敏感性等）
- 为后续扩展留好架构位：股价绘制（K 线图）、更多财报维度分析图表

**非目标（本期不做）：**

- 多用户/认证/公网部署（个人本地工具，localhost 访问）
- 移动端适配
- 前端自动化测试

## 2. 技术选型

| 层 | 选型 | 理由 |
|:---|:---|:---|
| 后端 | FastAPI + uvicorn | Python 原生调用现有脚本；async 与后台任务模型对口；自动 OpenAPI 文档 |
| ORM | SQLAlchemy 2.x + Alembic | 正规数据访问层；迁移管理；将来可平滑迁 PostgreSQL |
| 数据库 | SQLite（`data/stock_god.db`） | 真实数据库（事务/约束/索引），单文件零运维，适配个人本地场景 |
| 前端 | Vue 3 + Vite | SPA，路由/组件化，后续图表扩展模式清晰 |
| 图表 | ECharts | 金融场景支持最全（candlestick K线、dataZoom 等） |
| Markdown 渲染 | markdown-it | 报告正文渲染 |
| 状态管理 | Pinia；任务轮询 | 全局任务状态感知 |

现有脚本（`sec_filings.py`、`sec_analysis.py`、`dcf.py`）**不改动**，后端以 `asyncio.create_subprocess_exec` 进程级隔离调用。

## 3. 总体架构

```
stock-god/
├── webapp/
│   ├── backend/
│   │   ├── main.py          # FastAPI 入口；生产模式托管 frontend/dist
│   │   ├── api/             # 路由层：stocks / tasks / reports / files
│   │   ├── services/        # 业务层：脚本调用封装、结果登记
│   │   ├── tasks.py         # 后台任务执行器（semaphore 并发控制）
│   │   ├── db.py            # engine/session
│   │   ├── models.py        # ORM 模型
│   │   └── schemas.py       # Pydantic 模型
│   └── frontend/            # Vue 3 + Vite + ECharts
│       ├── src/views/       # StockList / StockDetail / Tasks
│       └── src/components/  # MarkdownViewer / 指标图表 / DCF 图 / LogViewer
├── alembic/                 # 数据库迁移
├── data/stock_god.db        # SQLite
└── logs/tasks/              # 任务日志 {task_id}.log
```

**数据权威边界**：所有查询/判断走数据库；文件系统不参与任何查询逻辑，仅在"读原文内容"时按 DB 中 `local_path` 指针读取。产物文件是 DB 的下游，登记时校验文件真实存在且非空。

## 4. 数据模型

| 表 | 字段（主要） | 说明 |
|:---|:---|:---|
| `stock` | id, ticker(唯一), name_cn, name_en, cik, market, exchange | 股票主表，来源 EDGAR company_tickers.json，可手动增删 |
| `filing` | id, stock_id(FK), form_type, period, accession_no, local_path, downloaded_at | 已下载财报；(stock_id, form_type, period) 唯一约束 |
| `analysis` | id, stock_id(FK), form_type, fiscal_year, local_path, metrics(JSON), generated_at | 分析报告；metrics 存任务登记时解析好的财务指标 JSON |
| `dcf_report` | id, stock_id(FK), growth, discount, years, safety, local_path, valuation(JSON), generated_at | DCF 报告；valuation 存估值结果与敏感性表 JSON |
| `task` | id, task_type, stock_id(FK 可空), params(JSON), status, error_code, error_summary, log_path, created_at, started_at, finished_at | 任务记录 |

- `task.status` ∈ `pending / running / success / failed / cancelled`
- `task.task_type` ∈ `download / analysis / dcf / sync_stocks`（可扩展）
- `analysis.metrics`、`dcf_report.valuation` 在任务成功登记时解析入库，图表查询为纯 DB 查询，运行时不解析文件

### 一次性数据导入

现有 `reports/` 目录下 8 只股票（AAPL/GOOGL/MSFT/NKE/PDD/PFE/QCOM/SPCX）的历史产物通过导入脚本入库，导入走与任务登记相同的校验（文件存在且非空），坏数据跳过并输出告警清单。

## 5. API 设计

```
GET  /api/stocks?q=&page=&size=          股票列表（搜索+分页）
POST /api/stocks/sync                    从 EDGAR 刷新股票列表（创建 sync_stocks 任务）
GET  /api/stocks/{ticker}                个股详情（财报/分析/DCF 汇总）
GET  /api/stocks/{ticker}/filings        已下载财报列表
GET  /api/stocks/{ticker}/analyses       分析报告列表
GET  /api/stocks/{ticker}/analyses/{id}  分析报告内容（metrics JSON + Markdown 正文）
GET  /api/stocks/{ticker}/dcf            DCF 报告列表
GET  /api/stocks/{ticker}/dcf/{id}       DCF 报告内容（valuation JSON + Markdown 正文）
GET  /api/filings/{id}/file              按资源 ID 流式返回财报原文件
GET  /api/tasks/{id}/log?offset=         任务日志尾部（分页读取）

POST /api/tasks                          创建任务 {task_type, ticker, params}
GET  /api/tasks?status=                  任务列表
GET  /api/tasks/{id}                     任务详情（前端 2s 轮询）
POST /api/tasks/{id}/cancel              取消 pending/running 任务
```

- 统一错误格式 `{detail, code}`；校验失败 422，资源不存在 404
- 同一股票同类型任务进行中时重复提交 → 409 拒绝
- 文件接口一律按资源 ID 取文件（路径只存在 DB 中），无路径穿越风险

## 6. 后台任务模型

- **执行**：`asyncio.create_subprocess_exec` 调用现有脚本，进程隔离；service 层负责 ticker → 脚本参数格式映射（如 `NKE` → `US.NKE`）
- **并发**：全局 semaphore 限 2 个并发，超出排队（避免打爆 SEC/FMP 限流）
- **状态机**：`pending → running → success | failed | cancelled`，每次流转写 DB
- **取消**：pending 任务直接标记 cancelled；running 任务终止子进程（含子进程组），等待退出后标记 cancelled
- **SQLite 并发**：启用 WAL 模式，避免任务登记写与 API 读互相阻塞
- **日志**：stdout/stderr 实时追加写 `logs/tasks/{task_id}.log`，DB 存 `log_path` 指针
- **成功登记**：脚本退出码 0 → 校验 `reports/` 产物存在且非空 → 解析 metrics/valuation 入库 → 更新 filing/analysis/dcf_report 表；产物缺失/为空视为失败（`EMPTY_OUTPUT`）
- **扩展**：新增任务类型（如股价拉取）注册新 runner，框架不动

## 7. 错误处理

### 任务错误三要素

| 字段 | 内容 |
|:---|:---|
| `error_code` | 机器可读枚举：`SEC_RATE_LIMITED` / `NETWORK_TIMEOUT` / `NO_FILINGS_FOUND` / `PARSE_FAILED` / `SCRIPT_EXIT_NONZERO` / `EMPTY_OUTPUT` |
| `error_summary` | 一句话中文概述，前端直接展示 |
| `log_path` | 完整日志文件，供事后排查 |

- 已知错误：匹配脚本输出特征（限流/超时/404）及产物缺失 → 映射具体 code + summary
- 未知错误：兜底 `SCRIPT_EXIT_NONZERO`，summary 含退出码
- 错误码表随设计文档维护，新增场景补充映射

### 前端错误展示

- 任务失败：详情页对应 Tab 红标 + toast 显示 error_summary
- "查看详情"→ 日志查看页（分页/滚动加载 log 尾部）

### 数据一致性

- 登记前校验产物文件存在且非空；`(stock_id, form_type, period)` 唯一约束，重复下载走更新
- 导入脚本坏数据跳过 + 告警清单

## 8. 前端页面

**技术栈**：Vue 3 + Vite + Vue Router + Pinia + ECharts + markdown-it

### 8.1 股票列表页 `/`

- 顶部搜索框：ticker/公司名实时搜索，后端分页
- 表格：代码、公司名、财报下载状态、报告数量标记；点行进详情
- "同步股票列表"按钮

### 8.2 个股详情页 `/stocks/{ticker}`（核心）

- 页头：公司信息 + 操作按钮【下载财报】【生成分析】【DCF 估值】（DCF 弹参数面板：增长率/折现率/年限/安全边际）
- **Tab 1 财报**：已下载列表（按 form_type + 年份分组），在线预览/下载原文；下方选年份+表单类型发起下载
- **Tab 2 财报分析**：报告列表，点击在线渲染 Markdown 正文
- **Tab 3 图表**：ECharts 消费 metrics/valuation JSON——营收/净利润趋势、ROE/负债率趋势、DCF 估值区间柱状图、敏感性热力表；预留股价图组件位（数据接口后续加）
- **Tab 4 DCF**：DCF 报告列表与渲染，历史估值对比

### 8.3 任务中心 `/tasks`

- 任务表格：类型、股票、状态徽标、开始/结束时间
- 运行中任务展开实时日志尾部（2s 轮询）
- 全局导航栏任务角标（运行中数量）
- 任务提交后跳详情页 + toast，可留页看状态或去任务中心

## 9. 测试策略

- **后端单元测试**（pytest）：ORM 模型、任务状态机、metrics 解析（fixture 用现有 NKE/MSFT 真实产物，不造假数据）
- **API 测试**：FastAPI TestClient，覆盖主要接口成功/404/422 路径
- **手动验收**：完整流程（列表→下载 NKE→分析→DCF→图表）
- 前端不做自动化测试（个人工具）

## 10. 实施要点

- 开发模式：uvicorn + Vite dev server（proxy API）；生产模式：FastAPI 托管 dist
- Alembic 建表，避免手写 SQL 初始化
- EDGAR 股票列表首次启动自动拉取并缓存入库，之后手动/定期刷新

## 11. 后续扩展位（本期仅预留）

- 股价绘制：新增 `GET /api/stocks/{ticker}/prices` 接口（腾讯适配器/Futu K线）+ ECharts candlestick 组件
- 更多财报分析维度图表：扩 `metrics` JSON 字段 + 新图表组件
- 任务类型扩展：注册新 runner
