#!/usr/bin/env python3
"""Verify that the configured database is at the current Alembic revision.

Use this command in CI, deployment gates, and readiness checks after migrations run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.config import settings


def main() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    script = ScriptDirectory.from_config(config)
    expected = script.get_current_head()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        from alembic.runtime.migration import MigrationContext

        actual = MigrationContext.configure(connection).get_current_revision()
    if actual != expected:
        raise SystemExit(f"database revision mismatch: actual={actual!r} expected={expected!r}")
    print(f"database revision verified: {actual}")


if __name__ == "__main__":
    main()
