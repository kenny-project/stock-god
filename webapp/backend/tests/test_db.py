"""Tests for db.make_engine URL normalization (review findings):

1. make_engine(":memory:") must not crash (memory-guard must check the
   *final* URL, not the raw input).
2. Bare relative filenames ("t.db") must not crash on os.makedirs.
3. Passing a full sqlite:/// URL must not create a literal "sqlite:"
   directory tree.
"""

from db import Base, make_engine
import models  # noqa: F401  (register ORM tables on Base)


def test_make_engine_memory():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # no crash, usable engine


def test_make_engine_bare_memory():
    # bare ":memory:" input must be normalized to sqlite:///:memory:
    engine = make_engine(":memory:")
    Base.metadata.create_all(engine)


def test_make_engine_bare_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = make_engine("t.db")
    Base.metadata.create_all(engine)
    assert (tmp_path / "t.db").exists()


def test_make_engine_sqlite_url_no_stray_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = make_engine(f"sqlite:///{tmp_path}/sub/t.db")
    Base.metadata.create_all(engine)
    assert (tmp_path / "sub" / "t.db").exists()
    # must not create a literal "sqlite:" directory tree in cwd
    assert not (tmp_path / "sqlite:").exists()
