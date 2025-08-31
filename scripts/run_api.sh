#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Run API server (optionally ensures DB is up via db_init.sh)
# Configurable via env:
#   HOST, PORT, ENV_FILE, RELOAD, AUTO_DB_UP
#   DB_CONTAINER, DB_IMAGE, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, PGDATA_DIR
# ------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# project root
if [[ -d "$SCRIPT_DIR/../api" ]]; then
  PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  PROJECT_ROOT="$SCRIPT_DIR/.."
fi
cd "$PROJECT_ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
ENV_FILE="${ENV_FILE:-.env.local}"
RELOAD="${RELOAD:-1}"
AUTO_DB_UP="${AUTO_DB_UP:-1}"

# (re)define DB_* so we can form a default DATABASE_URL if needed
DB_USER="${DB_USER:-plumber}"
DB_PASSWORD="${DB_PASSWORD:-plumber}"
DB_NAME="${DB_NAME:-plumbing}"
DB_PORT="${DB_PORT:-55432}"

# ---- optionally ensure DB is up first ----
if [[ "$AUTO_DB_UP" == "1" ]]; then
  bash "$SCRIPT_DIR/db_init.sh"
fi

# ---- env file flag (for uvicorn) ----
ENV_FLAG=()
if [[ -f "$ENV_FILE" ]]; then
  ENV_FLAG=(--env-file "$ENV_FILE")
else
  echo "⚠️  $ENV_FILE not found; continuing without it."
fi

# ---- ensure DATABASE_URL (prefer already-set, then .env, else default) ----
DEFAULT_DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${DB_PORT}/${DB_NAME}"
if [[ -z "${DATABASE_URL:-}" ]]; then
  export DATABASE_URL="$DEFAULT_DATABASE_URL"
fi
if [[ -f "$ENV_FILE" ]] && ! grep -qE '^DATABASE_URL=' "$ENV_FILE"; then
  echo "ℹ️  Using DATABASE_URL from environment: $DATABASE_URL"
fi

# ---- ensure python path ----
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

# ---- choose runner ----
if command -v uv >/dev/null 2>&1; then
  CMD=(uv run uvicorn api.main:app "${ENV_FLAG[@]}" --host "$HOST" --port "$PORT" --log-level info)
else
  if ! python -c "import uvicorn" >/dev/null 2>&1; then
    echo "❌ Neither 'uv' nor 'uvicorn' is available. Install one of them first:"
    echo "   pip install uvicorn[standard]    # or"
    echo "   pip install uv"
    exit 1
  fi
  CMD=(python -m uvicorn api.main:app "${ENV_FLAG[@]}" --host "$HOST" --port "$PORT" --log-level info)
fi

# ---- reload flags ----
if [[ "${RELOAD}" == "1" ]]; then
  CMD+=("--reload" "--reload-exclude" ".pgdata/*")
fi

echo "▶️  ${CMD[*]}"
exec "${CMD[@]}"
