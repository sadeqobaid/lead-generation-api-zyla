#!/usr/bin/env bash
# Restore a PostgreSQL custom-format backup into a target database.
#
# Required: DATABASE_URL, BACKUP_FILE, and CONFIRM_RESTORE=YES
#
# This replaces objects in the target database. Restore into a separate instance first,
# verify migrations and the application smoke test, and only then consider production use.
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" || -z "${BACKUP_FILE:-}" ]]; then
  echo "DATABASE_URL and BACKUP_FILE are required" >&2
  exit 2
fi
if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "refusing restore: set CONFIRM_RESTORE=YES after reviewing the target" >&2
  exit 2
fi
if [[ ! -r "$BACKUP_FILE" ]]; then
  echo "backup file is not readable: $BACKUP_FILE" >&2
  exit 2
fi

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DATABASE_URL" "$BACKUP_FILE"
echo "restore completed; run scripts/check_migrations.py and scripts/smoke_test.py"
