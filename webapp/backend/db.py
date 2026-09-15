import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.environ.get("STOCKGOD_DB", os.path.join(DATA_DIR, "stock_god.db"))
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Base(DeclarativeBase):
    pass


def make_engine(db_path=None):
    raw = db_path or DB_PATH
    url = raw if raw.startswith("sqlite") else f"sqlite:///{raw}"
    if url == "sqlite:///:memory:":
        return create_engine(url, connect_args={"check_same_thread": False})
    fs_path = url.replace("sqlite:///", "", 1)
    os.makedirs(os.path.dirname(os.path.abspath(fs_path)), exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
