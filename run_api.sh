#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Run API + start Postgres in Docker if needed
# ------------------------------------------------------------
# Configurable via env:
#   HOST, PORT, ENV_FILE, RELOAD
#   DB_CONTAINER, DB_IMAGE, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, PGDATA_DIR
# ------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "$SCRIPT_DIR/../api" ]]; then
  PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  PROJECT_ROOT="$SCRIPT_DIR"
fi
cd "$PROJECT_ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
ENV_FILE="${ENV_FILE:-.env.local}"
RELOAD="${RELOAD:-1}"

# ---- Docker / Postgres settings ----
DB_CONTAINER="${DB_CONTAINER:-plumber-pg}"
DB_IMAGE="${DB_IMAGE:-postgres:16}"
DB_USER="${DB_USER:-plumber}"
DB_PASSWORD="${DB_PASSWORD:-plumber}"
DB_NAME="${DB_NAME:-plumbing}"
DB_PORT="${DB_PORT:-55432}"
PGDATA_DIR="${PGDATA_DIR:-$PROJECT_ROOT/.pgdata}"

# ---- sanity: docker available ----
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ Docker is required but not found. Please install/start Docker."
  exit 1
fi

# ---- ensure pgdata dir exists (bind mount) ----
mkdir -p "$PGDATA_DIR"

# ---- start postgres container if not running ----
if ! docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
  echo "🐘 Starting Postgres container: $DB_CONTAINER"
  docker run -d \
    --name "$DB_CONTAINER" \
    -e POSTGRES_USER="$DB_USER" \
    -e POSTGRES_PASSWORD="$DB_PASSWORD" \
    -e POSTGRES_DB="$DB_NAME" \
    -p "$DB_PORT:5432" \
    -v "$PGDATA_DIR":/var/lib/postgresql/data \
    --health-cmd="pg_isready -U $DB_USER -d $DB_NAME || exit 1" \
    --health-interval=5s \
    --health-timeout=5s \
    --health-retries=12 \
    "$DB_IMAGE" >/dev/null
else
  status="$(docker inspect -f '{{.State.Status}}' "$DB_CONTAINER")"
  if [[ "$status" != "running" ]]; then
    echo "🐘 Starting existing Postgres container: $DB_CONTAINER"
    docker start "$DB_CONTAINER" >/dev/null
  fi
fi

# ---- wait until healthy ----
echo "⏳ Waiting for Postgres to become healthy..."
for i in {1..60}; do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || echo "unknown")"
  if [[ "$health" == "healthy" ]]; then
    break
  fi
  sleep 1
done
if [[ "$health" != "healthy" ]]; then
  echo "❌ Postgres did not become healthy in time. Check logs:"
  echo "   docker logs $DB_CONTAINER"
  exit 1
fi
echo "✅ Postgres is healthy."

# ---- optional: quick connectivity check ----
if ! docker exec -e PGPASSWORD="$DB_PASSWORD" "$DB_CONTAINER" \
  psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" >/dev/null 2>&1; then
  echo "❌ Cannot connect to Postgres inside the container as $DB_USER/$DB_NAME."
  echo "   docker logs $DB_CONTAINER"
  exit 1
fi

# ---- env file flag (for FastAPI) ----
ENV_FLAG=()
if [[ -f "$ENV_FILE" ]]; then
  ENV_FLAG=(--env-file "$ENV_FILE")
else
  echo "⚠️  $ENV_FILE not found; continuing without it."
fi

# ---- ensure DATABASE_URL (prefer already-set, then .env, else default) ----
DEFAULT_DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${DB_PORT}/${DB_NAME}"
if [[ -z "${DATABASE_URL:-}" ]]; then
  # Only export a default if DATABASE_URL not provided in the environment.
  # uvicorn will still load from --env-file if present and might override this.
  export DATABASE_URL="$DEFAULT_DATABASE_URL"
fi

# Helpful note if env file present but missing DATABASE_URL
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

if [[ "${RELOAD}" == "1" ]]; then
  CMD+=("--reload")
fi

echo "▶️  ${CMD[*]}"
exec "${CMD[@]}"

if [[ "$RELOAD" == "1" ]]; then
  CMD+=("--reload" "--reload-exclude" ".pgdata/*")
fi

