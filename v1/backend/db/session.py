from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import AppConfig


def get_engine(config: AppConfig):
    """Create a SQLAlchemy engine for the configured SQLite database."""
    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    uri = f"sqlite:///{db_path}"
    return create_engine(uri, connect_args={"check_same_thread": False})


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
