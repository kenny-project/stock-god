"""GET /api/stocks/{ticker}/financials 契约测试：columns/rows/ttm/shares/price。"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db import Base
import models  # noqa
from main import app, set_engine_for_test
from api import tasks_api
from models import Analysis, Stock


class DummyExecutor:
    async def run_one(self, task):
        return

    async def cancel(self, task):
        return False


def _m(**kw):
    """构造 metrics（默认带全 12 行指标中会用到的键 + 流通股数）。"""
    base = {"营收": 10000.0, "净利润": 2000.0, "净利率": 20.0, "毛利润": 5000.0,
            "毛利率": 50.0, "每股收益": 2.5, "现金及等价物": 800.0, "自由现金流": 1500.0,
            "总资产": 50000.0, "总负债": 25000.0, "资产负债率": 50.0, "净资产收益率": 15.0,
            "流通股数": 1068.1, "折旧摊销": 600.0, "资本支出": -400.0}
    base.update(kw)
    return base


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, expire_on_commit=False)
    set_engine_for_test(engine, Factory)
    tasks_api.set_executor(DummyExecutor())
    # 默认屏蔽真实腾讯行情（测试不打外网）；需要现价的用例自行覆盖
    monkeypatch.setattr("api.reports.get_price", lambda symbol: None)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, Factory


def _seed_qcom(Factory):
    """QCOM 形态：10-K FY2025 + 2025/2026 各三季（10-Q，现金流 YTD 累计）。"""
    with Factory() as s:
        st = Stock(ticker="QCOM", market="US")
        s.add(st)
        s.flush()
        s.add(Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2025,
                       local_path="reports/sec_analysis/QCOM/10-K_FY2025.md",
                       metrics=_m(营收=41616.0, 净利润=5541.0, 净利率=13.3,
                                  折旧摊销=1602.0, 资本支出=-1192.0, 流通股数=1071.0)))
        qdata = {
            (2025, "2025Q1"): (11669.0, 3180.0, 27.3, 436.0, -277.0, 1106.0),
            (2025, "2025Q2"): (10979.0, 2812.0, 25.6, 833.0, -491.0, 1098.0),
            (2025, "2025Q3"): (10365.0, 2666.0, 25.7, 1231.0, -785.0, 1079.0),
            (2026, "2026Q1"): (12252.0, 3004.0, 24.5, 393.0, -549.0, 1067.0),
            (2026, "2026Q2"): (10599.0, 7370.0, 69.5, 806.0, -1082.0, 1054.0),
            (2026, "2026Q3"): (9947.0, 2002.0, 20.1, 1202.0, -1578.0, 1050.0),
        }
        for (fy, q), (rev, ni, nm, dep, capex, sh) in qdata.items():
            s.add(Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=fy, quarter=q,
                           local_path=f"reports/sec_analysis/QCOM/10-Q_{q}.md",
                           metrics=_m(营收=rev, 净利润=ni, 净利率=nm, 毛利率=55.0,
                                      折旧摊销=dep, 资本支出=capex, 流通股数=sh)))
        s.commit()


async def test_financials_contract_and_units(env):
    """契约形状 + 单位换算：money ÷100 一位小数、ratio/eps 原值、缺数据 null。"""
    client, Factory = env
    _seed_qcom(Factory)
    async with client as c:
        r = await c.get("/api/stocks/QCOM/financials")
        assert r.status_code == 200
        body = r.json()
        # columns：年报全量（1）+ 最近 4 季，各自倒序；年报在前季度在后
        assert [(c["kind"], c["label"]) for c in body["columns"]] == [
            ("annual", "FY2025"), ("quarter", "2026Q3"), ("quarter", "2026Q2"),
            ("quarter", "2026Q1"), ("quarter", "2025Q3")]
        assert all(c["analysis_id"] > 0 for c in body["columns"])
        # rows：12 指标固定顺序
        assert [row["key"] for row in body["rows"]] == [
            "revenue", "net_income", "net_margin", "gross_profit", "gross_margin",
            "eps", "cash", "fcf", "total_assets", "total_liabilities",
            "debt_ratio", "roe"]
        revenue = body["rows"][0]
        assert revenue["label"] == "营收（亿$）" and revenue["type"] == "money_yi"
        # money_yi = M ÷100 一位小数：年报 41616M → 416.2亿；2026Q3 9947M → 99.5亿
        assert revenue["values"]["FY2025"] == 416.2
        assert revenue["values"]["2026Q3"] == 99.5
        assert "2025Q2" not in revenue["values"]  # 最近 4 季之外的季度列不存在
        # ratio 原值（不除 100）
        net_margin = body["rows"][2]
        assert net_margin["values"]["FY2025"] == 13.3
        assert net_margin["values"]["2026Q3"] == 20.1
        # eps 原值
        assert body["rows"][5]["values"]["FY2025"] == 2.5
        # 年报缺 存货 之外的键不编造：_m 全键齐备，此处验证列上缺键 → null
        with Factory() as s:
            st = s.scalar(select(Stock).where(Stock.ticker == "QCOM"))
            a = s.scalar(select(Analysis).where(
                Analysis.stock_id == st.id, Analysis.form_type == "10-K"))
            a.metrics = {"营收": 41616.0}  # 只有营收
            s.commit()
        body2 = (await c.get("/api/stocks/QCOM/financials")).json()
        ni = body2["rows"][1]
        assert ni["values"]["FY2025"] is None  # 缺数据 → null
        assert ni["values"]["2026Q3"] == 20.0  # 季度列不受影响（2002/100 一位小数）


async def test_financials_ttm_and_shares(env, monkeypatch):
    """TTM 基期数值（与 dcf.py 公式一致）+ 股数取最近一期 + 现价降级。"""
    client, Factory = env
    _seed_qcom(Factory)
    # 现价：monkeypatch 行情适配器返回固定价，验证透传
    monkeypatch.setattr("api.reports.get_price", lambda symbol: 177.72)
    async with client as c:
        body = (await c.get("/api/stocks/QCOM/financials")).json()
        ttm = body["ttm"]
        assert ttm["base_period"] == "TTM 截至 2026Q3"
        # 净利润：5541 + (3004−3180)+(7370−2812)+(2002−2666) = 9259
        assert ttm["net_income"] == 9259
        # 折旧摊销 YTD：1602 + 1202 − 1231 = 1573
        assert ttm["depreciation"] == 1573
        # CapEx YTD 取绝对值：1192 + 1578 − 785 = 1985
        assert ttm["capex"] == 1985
        assert ttm["maintenance_capex"] == 1985 * 0.6
        assert ttm["owner_earnings"] == round(9259 + 1573 - 1985 * 0.6, 1)
        assert "components" in ttm and "5,541" in ttm["components"]
        # 股数：最近一期（2026Q3）的流通股数
        assert body["shares_outstanding"] == 1050.0
        assert body["price"] == 177.72


async def test_financials_price_degrades_to_null(env, monkeypatch):
    """行情异常/缺失 → price=null，接口不报错、其余数据完整返回。"""
    client, Factory = env
    _seed_qcom(Factory)
    monkeypatch.setattr("api.reports.get_price",
                        lambda symbol: (_ for _ in ()).throw(RuntimeError("network down")))
    async with client as c:
        r = await c.get("/api/stocks/QCOM/financials")
        assert r.status_code == 200
        body = r.json()
        assert body["price"] is None
        assert body["ttm"]["owner_earnings"] is not None  # 其余数据不受影响


async def test_financials_empty_stock(env):
    """无任何分析数据：columns/rows 空 values、ttm/shares/price 全 null。"""
    client, Factory = env
    with Factory() as s:
        s.add(Stock(ticker="NEW", market="US"))
        s.commit()
    async with client as c:
        r = await c.get("/api/stocks/NEW/financials")
        assert r.status_code == 200
        body = r.json()
        assert body["columns"] == []
        assert body["rows"][0]["values"] == {}
        assert body["ttm"] is None
        assert body["shares_outstanding"] is None
        assert body["price"] is None


async def test_financials_duplicate_fy_labels(env):
    """同 FY 双 form（10-K + 20-F 两条年报）→ 第二条 label 追加 form_type 消歧，
    columns 无重复 label（前端 :key 不冲突），rows[].values 键不互相覆盖。"""
    client, Factory = env
    with Factory() as s:
        st = Stock(ticker="DUAL", market="US")
        s.add(st)
        s.flush()
        s.add(Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2025,
                       local_path="reports/sec_analysis/DUAL/10-K_FY2025.md",
                       metrics=_m(营收=41616.0)))
        s.add(Analysis(stock_id=st.id, form_type="20-F", fiscal_year=2025,
                       local_path="reports/sec_analysis/DUAL/20-F_FY2025.md",
                       metrics=_m(营收=12300.0)))
        s.commit()
    async with client as c:
        body = (await c.get("/api/stocks/DUAL/financials")).json()
        labels = [col["label"] for col in body["columns"]]
        assert sorted(labels) == ["FY2025", "FY2025 (20-F)"]
        assert len(set(labels)) == len(labels)  # label 全局唯一
        revenue = body["rows"][0]["values"]
        # 两条记录各自成列：M÷100 一位小数，键不互相覆盖
        assert sorted(v for v in revenue.values() if v is not None) == [123.0, 416.2]


async def test_financials_404(env):
    client, _ = env
    async with client as c:
        assert (await c.get("/api/stocks/XXXX/financials")).status_code == 404
