"""Database configuration and session management for SQLite."""

import logging
import os
from pathlib import Path
from typing import Generator
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event
from src.core.config import settings

# Import all models to ensure they're registered with SQLModel
from src.models.db_models import Conversation, Message
from src.models.playbook_models import PlaybookEntry, PlaybookOperation

logger = logging.getLogger(__name__)

# Database URL from settings (defaults to SQLite)
DATABASE_URL = settings.database_url

# Ensure data directory exists for SQLite
if DATABASE_URL.startswith("sqlite"):
    # Handle both relative and absolute paths
    db_path = DATABASE_URL.replace("sqlite:///", "")
    
    # Convert to Path object for better path handling
    db_file = Path(db_path)
    
    # Create parent directory if it doesn't exist
    if db_file.parent and str(db_file.parent) != ".":
        db_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created database directory: {db_file.parent}")

# Create engine with appropriate settings for SQLite
# Enable WAL mode for better concurrent access from multiple workers
if DATABASE_URL.startswith("sqlite"):
    # Use WAL mode for better multi-process/worker support
    # WAL allows concurrent reads and writes without blocking
    connect_args = {
        "check_same_thread": False,
        "timeout": 30.0  # Wait up to 30 seconds for locks
    }
    engine = create_engine(
        DATABASE_URL,
        echo=settings.debug,
        connect_args=connect_args,
        poolclass=None
    )
    # Enable WAL mode after engine creation
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        """Enable WAL mode for SQLite to support concurrent access."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # Balance between safety and performance
        cursor.execute("PRAGMA busy_timeout=30000")  # 30 second timeout
        cursor.close()
else:
    engine = create_engine(
        DATABASE_URL,
        echo=settings.debug,
        connect_args={},
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=settings.database_pool_recycle
    )


def create_db_and_tables():
    """Create all database tables."""
    logger.info("Creating database tables...")
    SQLModel.metadata.create_all(engine)
    logger.info("Database tables created successfully")


def get_session() -> Generator[Session, None, None]:
    """
    Get database session.
    Use as dependency in FastAPI endpoints.
    """
    with Session(engine) as session:
        yield session


def get_db_session() -> Session:
    """
    Get database session for non-FastAPI usage.
    Remember to close the session when done.
    """
    return Session(engine)
