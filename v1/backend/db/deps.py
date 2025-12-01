from __future__ import annotations

from typing import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker


def get_session_factory(request: Request) -> sessionmaker:
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise RuntimeError("DB session factory not initialized")
    return factory


def get_db(session_factory: sessionmaker = Depends(get_session_factory)) -> Generator[Session, None, None]:
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
