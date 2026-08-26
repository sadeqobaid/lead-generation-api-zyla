#!/usr/bin/env bash
# Run local/CI quality gates for the Lead Generation API.
#
# Usage:
#   ./scripts/run_checks.sh
#   RUN_LIVE_SMOKE=1 BASE_URL=http://127.0.0.1:8000 ./scripts/run_checks.sh
#   CHECK_MIGRATIONS=1 DATABASE_URL=... ./scripts/run_checks.sh
#
# Always runs Python compilation and the test suite. Optional gates check linting, the live HTTP
# flow, and the target Alembic revision. Set PYTHON to override automatic interpreter selection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

"$PYTHON" -m compileall -q app tests scripts alembic
"$PYTHON" -m pytest -vv

if command -v ruff >/dev/null 2>&1; then
  ruff check app tests scripts
else
  echo "ruff not installed; skipping lint gate"
fi

if [[ "${CHECK_MIGRATIONS:-0}" == "1" ]]; then
  "$PYTHON" scripts/check_migrations.py
fi

if [[ "${RUN_LIVE_SMOKE:-0}" == "1" ]]; then
  "$PYTHON" scripts/smoke_test.py
fi

echo "all checks passed"
