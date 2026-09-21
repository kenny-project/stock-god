import os
import shutil
from datetime import datetime
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


def test_register_download_dir_form_from_representative_filename(tmp_path):
    """PFE 式误判根治：顶层主文档 pfe-20201226x10q.htm 先登记 (10-Q, 2020-12-26)，
    同期目录 pfe-20201226/ 目录名无 form token，但代表文件名含 10q → 提取出 10-Q，
    (10-Q, 2020-12-30 期) 槽位已被占据 → 目录不再重复登记，只入一行正确类型。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_filings" / "NKE"
    d = base / "pfe-20201226"  # 目录名无 form token
    d.mkdir(parents=True)
    (d / "pfe-exh101x3292026x10q.htm").write_bytes(b"<html>exhibit main</html>")
    (d / "pfe-20201226.xsd").write_bytes(b"<xsd/>")
    (base / "pfe-10q_20201226.htm").write_bytes(b"<html>main doc</html>")
    n = register_download(s, st, base_dir=str(base))
    rows = s.scalars(select(Filing)).all()
    assert len(rows) == 1
    f = rows[0]
    assert f.form_type == "10-Q"  # 顶层文件名含 token
    assert f.period == "2020-12-26"
    assert f.local_path.endswith("pfe-10q_20201226.htm")
    assert n == 1


def test_register_download_dir_representative_skips_exh(tmp_path):
    """无顶层主文档的目录：exh/xex/ex10 缩写 exhibit 不得被选为代表文件，
    应跳过它们选中真正主文档，并从代表文件名（而非目录名）提取 form token。
    修复前 abc-exh101_*.htm 会被误登记为主文档（form=UNKNOWN，带错 token 风险）。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_filings" / "NKE"
    d = base / "abc-20240331"  # 目录名无 form token
    d.mkdir(parents=True)
    # 字母序 exhibit 附件排在主文档之前，保证修复前会被误选为代表文件
    (d / "abc-exh101_20240331.htm").write_bytes(b"<html>exhibit 10.1</html>")
    (d / "abcxex991_20240331.htm").write_bytes(b"<html>exhibit 99.1</html>")
    (d / "tm247654d1_ex10_20240331.htm").write_bytes(b"<html>exhibit 10</html>")
    (d / "tm247654d1_10q.htm").write_bytes(b"<html>main doc</html>")
    n = register_download(s, st, base_dir=str(base))
    rows = s.scalars(select(Filing)).all()
    assert n == 1 and len(rows) == 1
    f = rows[0]
    assert f.form_type == "10-Q"  # 从代表文件名 tm247654d1_10q.htm 提取
    assert f.period == "2024-03-31"
    assert f.local_path.endswith("abc-20240331/tm247654d1_10q.htm")


def test_register_download_unknown_blocked_by_known_period(tmp_path):
    """目录名与代表文件名都提取不到 form token，且该申报期已有已知类型行
    （顶层 10-Q 主文档）→ 不再登记 UNKNOWN 行（杜绝成对重复）；
    另一个无同期已知行的独立目录仍正常登记 UNKNOWN。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_filings" / "NKE"
    d1 = base / "aapl-20201226"  # 无 token 目录，同期已有已知行
    d1.mkdir(parents=True)
    (d1 / "aapl-20201226.htm").write_bytes(b"<html/>")  # 代表文件名也无 token
    d2 = base / "spcx-20210331"  # 独立 UNKNOWN：无同期已知行
    d2.mkdir(parents=True)
    (d2 / "spcx-20210331.htm").write_bytes(b"<html/>")
    s.add(Filing(stock_id=st.id, form_type="10-Q", period="2020-12-26",
                 local_path="reports/sec_filings/NKE/aapl-20201226x10q.htm"))
    s.commit()
    n = register_download(s, st, base_dir=str(base))
    rows = s.scalars(select(Filing)).all()
    assert n == 1  # 只登记独立 UNKNOWN，同期已有已知行的目录被挡掉
    assert len(rows) == 2
    by_period = {f.period: f.form_type for f in rows}
    assert by_period["2020-12-26"] == "10-Q"  # 未被 UNKNOWN 挤出重复行
    assert by_period["2021-03-31"] == "UNKNOWN"  # 独立 UNKNOWN 正常登记


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


def test_register_dcf_timestamped_filename_new_row_per_run(tmp_path):
    """dcf.py 文件名带秒级时间戳后（{TICKER}_DCF_{YYYYMMDD_HHMMSS}.md），
    每次估值落独立文件、新建一条记录：前缀过滤仍命中带时间戳的名字，
    generated_at 取各自文件 mtime；同一文件重复登记仍幂等不新增。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "dcf"
    base.mkdir(parents=True)
    mt1, mt2 = 1000000000, 1700000000
    f1 = base / "US.NKE_DCF_20260919_100000.md"
    f2 = base / "US.NKE_DCF_20260920_115903.md"
    for f, mt in ((f1, mt1), (f2, mt2)):
        shutil.copy(os.path.join(FIXTURES, "US.NKE_DCF.md"), f)
        os.utime(f, (mt, mt))
    n = register_dcf(s, st, base_dir=str(base))
    assert n == 2
    rows = {os.path.basename(r.local_path): r for r in s.scalars(select(DcfReport)).all()}
    assert len(rows) == 2
    assert rows[f1.name].generated_at == datetime.fromtimestamp(mt1)
    assert rows[f2.name].generated_at == datetime.fromtimestamp(mt2)
    assert rows[f1.name].valuation["intrinsic_value_musd"] == 47099
    # 同一文件重复登记仍不重复（幂等保留）
    assert register_dcf(s, st, base_dir=str(base)) == 0
    assert s.scalar(select(func.count(DcfReport.id))) == 2


def test_register_analysis_generator_version(tmp_path):
    """md 头部 `生成器版本: analysis-v2` 行 → generator_version="v2"；
    无该行（旧产物）→ NULL（legacy），前端据此打"旧版"徽标。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "sec_analysis" / "NKE"
    base.mkdir(parents=True)
    # 新版产物：fixture 头部插入版本行（fixture 本身无版本行）
    fresh = base / "10-K_FY2030.md"
    src = open(os.path.join(FIXTURES, "10-K_FY2024.md"), encoding="utf-8").read()
    fresh.write_text(src.replace(
        "提取时间:", "生成器版本: analysis-v2\n\n提取时间:", 1), encoding="utf-8")
    # legacy 产物：原样拷贝（无版本行）
    shutil.copy(os.path.join(FIXTURES, "10-K_FY2024.md"), base / "10-K_FY2024.md")
    n = register_analysis(s, st, base_dir=str(base))
    assert n == 2
    rows = {a.fiscal_year: a for a in s.scalars(select(Analysis)).all()}
    assert rows[2030].generator_version == "v2"
    assert rows[2024].generator_version is None


def test_register_dcf_generator_version(tmp_path):
    """DCF md 头部 `生成器版本: dcf-v1` 行 → generator_version="v1"；无该行 → NULL。"""
    s = _session()
    st = s.scalar(select(Stock).where(Stock.ticker == "NKE"))
    base = tmp_path / "reports" / "dcf"
    base.mkdir(parents=True)
    src = open(os.path.join(FIXTURES, "US.NKE_DCF.md"), encoding="utf-8").read()
    f1 = base / "US.NKE_DCF_20260920_120000.md"
    f1.write_text(src.replace(
        "生成时间:", "生成器版本: dcf-v1\n生成时间:", 1), encoding="utf-8")
    shutil.copy(os.path.join(FIXTURES, "US.NKE_DCF.md"), base / "US.NKE_DCF_legacy.md")
    n = register_dcf(s, st, base_dir=str(base))
    assert n == 2
    rows = {os.path.basename(r.local_path): r for r in s.scalars(select(DcfReport)).all()}
    assert rows["US.NKE_DCF_20260920_120000.md"].generator_version == "v1"
    assert rows["US.NKE_DCF_legacy.md"].generator_version is None
