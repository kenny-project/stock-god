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
