from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    # Agent hooks write from worker threads; wait for SQLite's write lock instead of failing.
    # Postgres: give up quickly so /api/health can report "unreachable" instead of hanging.
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {"connect_timeout": 5},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Stores aware datetimes as UTC and always returns them aware (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use timezone-aware UTC datetimes")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)
    add_missing_columns()
    from .record import backfill_chain
    with engine.begin() as conn:
        backfill_chain(conn)
    from .snapshots import backfill
    with SessionLocal() as db:
        backfill(db)


def add_missing_columns(eng=None) -> list[str]:
    """Additive migration: ALTER TABLE ADD COLUMN for new *nullable* model columns, so existing
    databases (production SQLite/Postgres) keep working after a model gains a field. Anything else
    (renames, type changes, non-null columns) still needs a manual migration."""
    from sqlalchemy import inspect

    added = []
    eng = eng or engine
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                if not col.nullable:
                    raise RuntimeError(f"{table.name}.{col.name} is new and NOT NULL: needs a manual migration")
                ddl = col.type.compile(dialect=eng.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))
                added.append(f"{table.name}.{col.name}")
    return added


def check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
