"""
Database connection and session management using SQLite
"""
import time
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.pool import StaticPool

from src.config import settings

# Create engine with SQLite specific settings
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite
    poolclass=StaticPool,  # Use single connection for SQLite
    echo=settings.DEBUG,
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Database logger for query logging
db_logger = None


def _setup_db_logging():
    """Setup database query logging"""
    global db_logger
    try:
        from src.main import get_db_logger
        db_logger = get_db_logger()
    except ImportError:
        import logging
        db_logger = logging.getLogger("db")
        db_logger.setLevel(logging.INFO)


_setup_db_logging()


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log SQL query before execution"""
    conn.info.setdefault('query_start_time', []).append(time.time())
    if db_logger:
        db_logger.debug(f"[db_query] Executing: {statement[:200]}...")


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log SQL query after execution with duration"""
    total = time.time() - conn.info['query_start_time'].pop()
    if db_logger:
        db_logger.debug(f"[db_query] Completed in {total*1000:.1f}ms: {statement[:200]}...")


# Register event listeners
event.listen(engine, "before_cursor_execute", _before_cursor_execute)
event.listen(engine, "after_cursor_execute", _after_cursor_execute)


def init_db() -> None:
    """Initialize database tables"""
    # Import models to register them with Base
    from src.models.models import Client, Device, PendingDevice
    Base.metadata.create_all(bind=engine)
    if db_logger:
        db_logger.info("[db_init] Database tables initialized")


def get_db() -> Generator[Session, None, None]:
    """Get database session dependency"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Get database session as context manager"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
