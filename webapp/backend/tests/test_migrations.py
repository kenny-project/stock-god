"""Migration up/down round-trip test for f9a2b4c6d8e0 (analysis.quarter).

存量库中季报行可能与年报或其他季度共享 (stock_id, form_type, fiscal_year)
（真实库 122 条），downgrade 重建三列唯一约束前必须先删 quarter 非空的
季报行，否则抛 IntegrityError。用临时 SQLite 库跑全链路验证。
"""
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import models  # noqa: F401  (register ORM tables on Base)
from models import Analysis, DcfReport, Stock

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_cfg(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("STOCKGOD_DB", db_url)  # env.py 从该环境变量取库地址
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    return cfg, db_url


def test_analysis_quarter_upgrade_downgrade_upgrade(tmp_path, monkeypatch):
    """含年报+同财年季报的库跑 upgrade→downgrade→upgrade 全链路不抛异常：
    downgrade 后季报行已删、年报保留；再 upgrade 后 quarter 列恢复可重新登记。"""
    cfg, db_url = _alembic_cfg(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")

    # 制造存量冲突数据：10-Q 两季同 (stock, form, fy)，10-K 季报与年报同 (stock, form, fy)
    engine = create_engine(db_url)
    with Session(engine) as s:
        st = Stock(ticker="NKE", market="US")
        s.add(st)
        s.flush()
        s.add_all([
            Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024, quarter=None,
                     local_path="reports/sec_analysis/NKE/10-K_FY2024.md"),
            Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024, quarter="2024Q2",
                     local_path="reports/sec_analysis/NKE/10-K_2024Q2.md"),
            Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=2024, quarter="2024Q1",
                     local_path="reports/sec_analysis/NKE/10-Q_2024Q1.md"),
            Analysis(stock_id=st.id, form_type="10-Q", fiscal_year=2024, quarter="2024Q3",
                     local_path="reports/sec_analysis/NKE/10-Q_2024Q3.md"),
        ])
        s.commit()
    engine.dispose()

    # 修复前：重建三列唯一索引撞 UNIQUE 冲突抛 IntegrityError
    # （显式指定目标版本：f9a2b4c6d8e0 的下一跳；相对 -1 会随 head 前移而漂移）
    command.downgrade(cfg, "e7b3c9d25a41")

    # 降级后 schema 已无 quarter 列，只查剩余字段
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT form_type, fiscal_year FROM analysis")).fetchall()
    assert rows == [("10-K", 2024)]  # 季报行已删，年报保留
    engine.dispose()

    # 再次 upgrade：quarter 列恢复，季报可重新登记
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(analysis)"))}
        n = conn.execute(text("SELECT count(*) FROM analysis")).scalar()
    assert "quarter" in cols
    assert n == 1  # 年报行仍在，且未因再升级重复或丢失
    engine.dispose()


def test_generator_version_upgrade_downgrade_upgrade(tmp_path, monkeypatch):
    """d8c3a5e71b94（analysis/dcf_report 加 generator_version 列）up/down 往返：
    升级后可写版本号，降级删列（SQLite 需 batch_alter_table 重建表）且存量行保留，
    再升级恢复列（旧行该列为 NULL = legacy）。"""
    cfg, db_url = _alembic_cfg(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    with Session(engine) as s:
        st = Stock(ticker="NKE", market="US")
        s.add(st)
        s.flush()
        s.add(Analysis(stock_id=st.id, form_type="10-K", fiscal_year=2024,
                       local_path="reports/sec_analysis/NKE/10-K_FY2024.md",
                       generator_version="v2"))
        s.add(DcfReport(stock_id=st.id, local_path="reports/dcf/US.NKE_DCF_20260920_120000.md",
                        generator_version="v1"))
        s.commit()
    engine.dispose()

    # 显式降级到 d8c3a5e71b94 的下一跳（相对 -1 会随 head 前移而漂移）
    command.downgrade(cfg, "f9a2b4c6d8e0")

    # 降级后两表的 generator_version 列均删除，存量行保留
    engine = create_engine(db_url)
    with engine.connect() as conn:
        cols_a = {r[1] for r in conn.execute(text("PRAGMA table_info(analysis)"))}
        cols_d = {r[1] for r in conn.execute(text("PRAGMA table_info(dcf_report)"))}
        n = conn.execute(text("SELECT count(*) FROM analysis")).scalar()
    assert "generator_version" not in cols_a and "generator_version" not in cols_d
    assert n == 1
    engine.dispose()

    # 再次升级：列恢复，旧行版本号为 NULL（legacy）
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        cols_a = {r[1] for r in conn.execute(text("PRAGMA table_info(analysis)"))}
        vals = [r[0] for r in conn.execute(text(
            "SELECT generator_version FROM analysis"))]
    assert "generator_version" in cols_a
    assert vals == [None]
    engine.dispose()
