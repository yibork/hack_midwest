"""One-off migration. Stop the app first; run with its env file and SQLite volume.

python scripts/migrate_sqlite_to_postgres.py --sqlite /app/data/db/closure_call_v2.db
Creates a consistent, retained SQLite backup, requires an empty Postgres destination,
copies in foreign-key order in one transaction, verifies counts and resets sequences.
Never prints connection strings or changes/deletes the source SQLite file.
"""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CLOSURECALL_DOTENV"] = "0"

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from app import models  # noqa: F401
from app.db import Base, add_missing_columns
from app.config import settings


def migrate(source_path):
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql" or url.query.get("sslmode") != "require":
        raise RuntimeError("DATABASE_URL must use PostgreSQL with sslmode=require")
    source_path = Path(source_path).resolve(strict=True)
    backup = source_path.with_name(source_path.name + datetime.now(timezone.utc).strftime(".%Y%m%dT%H%M%S%fZ.backup"))
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as src:
        with sqlite3.connect(backup) as dst:
            src.backup(dst)
    print(f"Retained SQLite backup: {backup}", flush=True)
    source = create_engine(f"sqlite:///{backup}")
    target = create_engine(url, connect_args={"connect_timeout": 10}, hide_parameters=True)
    source_inspector = inspect(source)
    tables = Base.metadata.sorted_tables
    unknown = set(source_inspector.get_table_names()) - set(Base.metadata.tables)
    if unknown:
        raise RuntimeError("Unmodeled source tables; refusing incomplete migration: " + ", ".join(sorted(unknown)))
    for table in tables:
        if not source_inspector.has_table(table.name):
            continue
        have = {c["name"] for c in source_inspector.get_columns(table.name)}
        if have != set(table.columns.keys()):
            raise RuntimeError(f"Source schema differs for {table.name}; reconcile additively before copying")
    Base.metadata.create_all(target)
    add_missing_columns(target)
    counts = []
    with source.connect() as src, target.begin() as dst:
        dst.execute(text("SELECT pg_advisory_xact_lock(731004221)"))
        for table in tables:
            if dst.scalar(select(func.count()).select_from(table)):
                raise RuntimeError(f"Destination {table.name} is not empty; refusing overwrite")
        for table in tables:
            count = 0
            if source_inspector.has_table(table.name):
                result = src.execute(select(table))
                while rows := result.fetchmany(500):
                    dst.execute(table.insert(), [dict(row._mapping) for row in rows])
                    count += len(rows)
            actual = dst.scalar(select(func.count()).select_from(table))
            if count != actual:
                raise RuntimeError(f"Count mismatch for {table.name}")
            counts.append((table.name, count, actual))
            for column in table.primary_key:
                if column.autoincrement is True:
                    sequence = dst.scalar(text("SELECT pg_get_serial_sequence(:table, :column)"),
                                          {"table": table.name, "column": column.name})
                    if sequence:
                        maximum = dst.scalar(select(func.max(column)))
                        dst.execute(text("SELECT setval(:sequence, :value, :called)"),
                                    {"sequence": sequence, "value": maximum or 1, "called": maximum is not None})
    # Verify committed counts using a new connection before authorizing the switch.
    with target.connect() as dst:
        for name, expected, _ in counts:
            if dst.scalar(select(func.count()).select_from(Base.metadata.tables[name])) != expected:
                raise RuntimeError(f"Committed count mismatch for {name}")
    print("table sqlite postgres")
    for name, expected, actual in counts:
        print(f"{name} {expected} {actual}")
    print("VERIFIED: every table count matches; original SQLite and backup retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True)
    args = parser.parse_args()
    try:
        migrate(args.sqlite)
    except Exception as exc:
        # Driver exceptions can contain credentials or row contents; suppress them.
        print(f"Migration failed ({type(exc).__name__}); app must remain on SQLite. No source data deleted.", file=sys.stderr)
        sys.exit(1)
