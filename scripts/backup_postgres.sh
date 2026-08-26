#!/usr/bin/env bash
# Create an encrypted-storage-ready PostgreSQL custom-format backup.
#
# Required: DATABASE_URL
# Optional: BACKUP_DIR (default: ./backups)
#
# The script never prints DATABASE_URL. Store the resulting file in encrypted storage,
# apply an appropriate retention policy, and test restoration on a separate database.
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required" >&2
  exit 2
fi

backup_dir="${BACKUP_DIR:-./backups}"
mkdir -p "$backup_dir"
umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$backup_dir/lead_generation_${timestamp}.dump"

pg_dump --format=custom --no-owner --no-privileges --dbname="$DATABASE_URL" --file="$backup_path"
sha256sum "$backup_path" > "$backup_path.sha256"
echo "backup created: $backup_path"
