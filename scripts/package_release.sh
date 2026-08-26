#!/usr/bin/env bash
# Build a sanitized, versioned source release.
#
# Usage:
#   RELEASE_VERSION=0.2.0 ./scripts/package_release.sh
#   PYTHON=/path/to/python RELEASE_VERSION=0.1.1 ./scripts/package_release.sh
#
# The script runs compilation and tests, copies application/migration/operations assets into a
# staging directory, removes local databases and environment files, creates a ZIP, and writes a
# SHA-256 checksum. It never packages `.env`, provider credentials, customer data, or backups.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${RELEASE_VERSION:-0.2.0}"
OUT_DIR="${ROOT}/dist"
STAGE="${OUT_DIR}/lead-generation-api-${VERSION}"
ARCHIVE="${OUT_DIR}/lead-generation-api-${VERSION}.zip"

cd "$ROOT"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi
"$PYTHON" -m compileall -q app tests scripts alembic
"$PYTHON" -m pytest -q
DOCX_PYTHON="${DOCX_PYTHON:-$PYTHON}"
if ! "$DOCX_PYTHON" -c 'import docx' >/dev/null 2>&1; then
  if python3 -c 'import docx' >/dev/null 2>&1; then
    DOCX_PYTHON="python3"
  else
    echo "python-docx is required to build the DOCX documentation; install requirements.txt first" >&2
    exit 1
  fi
fi

rm -rf "$OUT_DIR"
mkdir -p "$STAGE"

cp -R app tests scripts .github alembic "$STAGE/"
cp README.md DEPLOYMENT_SETUP.md MARKETPLACE_RELEASE_GUIDE.md MARKETPLACE_LISTING.md ZYLA_MARKETPLACE_LISTING.md ZYLA_DEPLOYMENT_RUNBOOK.md ZYLA_SUBMISSION_CHECKLIST.md COMMERCIAL_SOFTWARE_LICENSE.md RELEASE_NOTES.md TEST_REPORT.md INSTALL_AND_SELL_NEXT_STEPS.md OPERATIONS.md SECURITY.md PRODUCTION_AUDIT.md PRODUCTION_HANDOFF.md \
   requirements.txt pyproject.toml alembic.ini Dockerfile docker-compose.yml .dockerignore .env.example openapi.yaml "$STAGE/"

find "$STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE" -type f -name '*.db' -delete
find "$STAGE" -type f -name '.env' -delete
(
  cd "$STAGE"
  mapfile -t MARKDOWN_FILES < <(find . -type f -name '*.md' -print | sort)
  if (( ${#MARKDOWN_FILES[@]} > 0 )); then
    "$DOCX_PYTHON" scripts/convert_markdown_to_docx.py --output-root . "${MARKDOWN_FILES[@]}"
    find . -type f -name '*.md' -delete
  fi
)
chmod +x "$STAGE/scripts/"*.sh

(
  cd "$OUT_DIR"
  zip -qr "$(basename "$ARCHIVE")" "$(basename "$STAGE")"
  sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256"
)

printf 'release archive: %s\n' "$ARCHIVE"
printf 'checksum file: %s\n' "${ARCHIVE}.sha256"
