#!/usr/bin/env python3
"""Apply reviewed Alembic migrations to the configured database.

This script is the only supported production schema-change entrypoint. Run it as a release job
before starting API replicas. It reads DATABASE_URL and other settings from the environment.

Usage:
    python scripts/migrate.py
    python scripts/migrate.py --sql   # render SQL without applying it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config

from app.config import settings


def alembic_config() -> Config:
    """Load alembic.ini and allow the validated application URL to control the target database."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply or render reviewed Lead Generation API database migrations.")
    parser.add_argument("--sql", action="store_true", help="Render the upgrade SQL instead of changing the database.")
    args = parser.parse_args()
    config = alembic_config()
    if args.sql:
        command.upgrade(config, "head", sql=True)
    else:
        command.upgrade(config, "head")
        print("database migrations applied")


if __name__ == "__main__":
    main()
