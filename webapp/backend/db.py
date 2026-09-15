import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.environ.get("STOCKGOD_DB", os.path.join(DATA_DIR, "stock_god.db"))
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


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
