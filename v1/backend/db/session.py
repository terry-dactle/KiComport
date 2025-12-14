from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import AppConfig

SQLITE_TIMEOUT_SEC = float(os.getenv("KICOMPORT_SQLITE_TIMEOUT_SEC", "30"))
SQLITE_WAL_ENABLED = os.getenv("KICOMPORT_SQLITE_WAL", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_engine(config: AppConfig):
    """Create a SQLAlchemy engine for the configured SQLite database."""
    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    uri = f"sqlite:///{db_path}"
    engine = create_engine(uri, connect_args={"check_same_thread": False, "timeout": SQLITE_TIMEOUT_SEC})

    if SQLITE_WAL_ENABLED:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):  # type: ignore[no-redef]
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
            except Exception:
                # Best-effort: don't block startup on filesystem/driver limitations.
                pass
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass

    return engine


def get_session_factory(config: AppConfig):
    engine = get_engine(config)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
