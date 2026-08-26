#!/usr/bin/env python3
"""Create local development tables from SQLAlchemy metadata.

Usage:
    python scripts/init_db.py

This command is intentionally limited to APP_ENV=development and AUTO_CREATE_SCHEMA=true.
Staging and production deployments must use `python scripts/migrate.py` so the exact Alembic
revision is reviewed and recorded before the application starts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: F401 - register all mapped tables with Base.metadata
from app.config import settings
from app.db import Base, engine


def main() -> None:
    """Create tables for a disposable local database and print no credentials."""
    if settings.environment != "development" or not settings.auto_create_schema:
        raise SystemExit("init_db.py is development-only; use scripts/migrate.py for staging/production")
    Base.metadata.create_all(bind=engine)
    print(f"development database initialized using {engine.url.render_as_string(hide_password=True)}")


if __name__ == "__main__":
    main()
