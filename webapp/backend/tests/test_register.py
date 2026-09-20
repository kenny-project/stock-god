import os
import shutil
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from db import Base, ROOT
from models import Stock, Filing, Analysis, DcfReport
from services.register import register_download, register_analysis, register_dcf

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Stock(ticker="NKE", market="US"))
    s.commit()
    return s


def _build_download_tree(tmp_path):
    """在 tmp_path 复刻两种真实磁盘布局（内容无关紧要，只看文件名文法）：
    - NKE 式：申报期目录里只有 exhibit/XBRL 附件，主文档在顶层（nke-20220531.htm）
    - MSFT 式：目录名带 form token + 下划线日期（msft-10k_20220630/），主文档在目录内
    """
    base = tmp_path / "reports" / "sec_filings" / "NKE"
    nke_dir = base / "nke-20220531"
    nke_dir.mkdir(parents=True)
    (nke_dir / "nke-20220531.xsd").write_bytes(b"<xsd/>")
    (nke_dir / "nke-20220531_g10.jpg").write_bytes(b"jpg")
    (base / "nke-20220531.htm").write_bytes(b"<html>main doc</html>")
    msft_dir = base / "msft-10k_20220630"
    msft_dir.mkdir(parents=True)
    for fn, content in [
        ("msft-10k_20220630.htm", b"<html>10-K main doc</html>"),
        ("msft-10k_20220630.xsd", b"<xsd/>"),
        ("msft-10k_20220630_g1.jpg", b"jpg1"),
        ("msft-10k_20220630_g2.jpg", b"jpg2"),
        ("msft-10k_20220630_g3.jpg", b"jpg3"),
        ("msft-10k_20220630_exhibit21.htm", b"<html>exhibit 21</html>"),
        ("msft-10k_20220630_exhibit32.htm", b"<html>exhibit 32</html>"),
    ]:
        (msft_dir / fn).write_bytes(content)
    return base


def test_register_download_msft_style_main_doc(tmp_path):
    """MSFT 布局：目录内 7 个文件共享 (10-K, 2022-06-30)，只登记 1 行且指向主文档 .htm。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = _build_download_tree(tmp_path)
    n = register_download(s, st, base_dir=str(base))
    msft = [f for f in s.scalars(select(Filing)).all() if f.period == "2022-06-30"]
    assert len(msft) == 1
    f = msft[0]
    assert f.form_type == "10-K"
    assert f.local_path.endswith("msft-10k_20220630/msft-10k_20220630.htm")
    assert os.path.exists(os.path.join(ROOT, f.local_path))
    assert n == 2  # NKE 顶层主文档 1 行 + MSFT 目录 1 行


def test_register_download_nke_style_top_level_doc(tmp_path):
    """NKE 布局：顶层 nke-20220531.htm 是主文档，先于同名申报期目录登记，
    占据 (UNKNOWN, 2022-05-31) 槽位；目录里只有 exhibit/XBRL 附件，不再重复占位。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_filings" / "NKE"
    d = base / "nke-20220531"
    d.mkdir(parents=True)
    (d / "nke-20220531.xsd").write_bytes(b"<xsd/>")
    (d / "nke-20220531_g10.jpg").write_bytes(b"jpg")
    (base / "nke-20220531.htm").write_bytes(b"<html>main doc</html>")
    n = register_download(s, st, base_dir=str(base))
    rows = s.scalars(select(Filing)).all()
    assert len(rows) == 1
    f = rows[0]
    assert f.form_type == "UNKNOWN"  # 顶层名无 form token
    assert f.period == "2022-05-31"  # 扩展名剥离后取到申报期
    assert f.local_path.endswith("nke-20220531.htm")


def test_register_download_idempotent_synthetic(tmp_path):
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = _build_download_tree(tmp_path)
    n1 = register_download(s, st, base_dir=str(base))
    n2 = register_download(s, st, base_dir=str(base))
    assert n1 == 2 and n2 == 0
    assert s.scalar(select(func.count(Filing.id))) == 2


def test_register_download_real_files():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n = register_download(s, st)  # 扫描真实 reports/sec_filings/NKE/
    assert n > 0
    assert s.scalar(select(func.count(Filing.id))) == n
    for f in s.scalars(select(Filing)).all():
        assert os.path.exists(os.path.join(ROOT, f.local_path))


def test_register_download_idempotent():
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    n1 = register_download(s, st)
    n2 = register_download(s, st)
    assert n1 > 0 and n2 == 0  # 二次登记不重复


def test_register_analysis_from_fixture(tmp_path, capsys):
    """10-K_FY2024.md → (10-K, 2024, quarter=NULL)；真实 10-Q_2024Q3.md → (10-Q, 2024, "2024Q3")，
    两者同财年可共存；无 FY/季度标签的文件名显式告警跳过，不静默丢弃。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_analysis" / "NKE"
    base.mkdir(parents=True)
    shutil.copy(os.path.join(FIXTURES, "10-K_FY2024.md"), base / "10-K_FY2024.md")
    shutil.copy(os.path.join(FIXTURES, "10-Q_2024Q3.md"), base / "10-Q_2024Q3.md")
    # form token 有、FY/季度标签都没有 → 告警跳过
    (base / "10-K_draft.md").write_text("# NKE 10-K draft\n", encoding="utf-8")
    n1 = register_analysis(s, st, base_dir=str(base))
    assert n1 == 2
    rows = {a.form_type: a for a in s.scalars(select(Analysis)).all()}
    assert rows["10-K"].fiscal_year == 2024 and rows["10-K"].quarter is None
    assert rows["10-Q"].fiscal_year == 2024 and rows["10-Q"].quarter == "2024Q3"
    # 季度报告正文同样含 财务指标/现金流 表，metrics 必须解析出来
    assert rows["10-Q"].metrics["净利润"] == 21448
    assert rows["10-Q"].metrics["营收"] == 85777
    n2 = register_analysis(s, st, base_dir=str(base))
    assert n2 == 0  # 二次登记不重复（含季度行）
    err = capsys.readouterr().err
    assert "10-K_draft.md" in err and "no fiscal year/quarter" in err


def test_register_analysis_fy_quarter_variant(tmp_path):
    """10-Q_FY2024Q3.md 变体：文件名同时含 FY 与季度字样，必须按季度解析
    （quarter="2024Q3"），不能被 _FY 先命中误判成年报槽位（quarter=None）。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_analysis" / "NKE"
    base.mkdir(parents=True)
    shutil.copy(os.path.join(FIXTURES, "10-Q_2024Q3.md"), base / "10-Q_FY2024Q3.md")
    n = register_analysis(s, st, base_dir=str(base))
    assert n == 1
    a = s.scalars(select(Analysis)).first()
    assert a.form_type == "10-Q"
    assert a.fiscal_year == 2024
    assert a.quarter == "2024Q3"


def test_register_dcf_from_fixture(tmp_path):
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "dcf"
    base.mkdir(parents=True)
    shutil.copy(os.path.join(FIXTURES, "US.NKE_DCF.md"), base / "US.NKE_DCF.md")
    n1 = register_dcf(s, st, base_dir=str(base))
    assert n1 == 1
    d = s.scalars(select(DcfReport)).first()
    assert d.valuation["intrinsic_value_musd"] == 47099
    n2 = register_dcf(s, st, base_dir=str(base))
    assert n2 == 0  # 幂等：同一 local_path 不重复登记
    assert s.scalar(select(func.count(DcfReport.id))) == 1
