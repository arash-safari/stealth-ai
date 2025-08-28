#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Ensure Postgres (Docker) is up, healthy, and reachable.
# Re-uses env vars if set; otherwise falls back to sane defaults.
# ------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# project root (handles both ./scripts and repo root)
if [[ -d "$SCRIPT_DIR/../api" ]]; then
  PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  PROJECT_ROOT="$SCRIPT_DIR/.."
fi
cd "$PROJECT_ROOT"

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
  else
    echo "🐘 Postgres container already running: $DB_CONTAINER"
  fi
fi

# ---- wait until healthy ----
echo "⏳ Waiting for Postgres to become healthy..."
for _ in {1..60}; do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || echo "unknown")"
  if [[ "$health" == "healthy" ]]; then
    break
  fi
  sleep 1
done
if [[ "${health:-unhealthy}" != "healthy" ]]; then
  echo "❌ Postgres did not become healthy in time. Logs:"
  docker logs "$DB_CONTAINER" || true
  exit 1
fi
echo "✅ Postgres is healthy."

# ---- quick connectivity check ----
if ! docker exec -e PGPASSWORD="$DB_PASSWORD" "$DB_CONTAINER" \
  psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" >/dev/null 2>&1; then
  echo "❌ Cannot connect to Postgres as $DB_USER/$DB_NAME."
  docker logs "$DB_CONTAINER" || true
  exit 1
fi

# ---- echo the effective DATABASE_URL for convenience ----
DEFAULT_DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${DB_PORT}/${DB_NAME}"
echo "📦 DB ready at: ${DATABASE_URL:-$DEFAULT_DATABASE_URL}"

# Optional: run migrations if you want (uncomment when applicable)
# if [[ -f "alembic.ini" ]]; then
#   if command -v uv >/dev/null 2>&1; then
#     echo "🛠  Running Alembic migrations..."
#     DATABASE_URL="${DATABASE_URL:-$DEFAULT_DATABASE_URL}" uv run alembic upgrade head
#   else
#     echo "ℹ️  Skipping migrations (install 'uv' to auto-run)."
#   fi
# fi
