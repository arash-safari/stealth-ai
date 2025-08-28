#!/usr/bin/env bash
set -euo pipefail

# Minimal orchestrator:
# - Ensures Postgres (via scripts/run_init.sh or scripts/db_init.sh)
# - Starts API (via scripts/run_api.sh)
# - Runs Taskfile tasks (intent-console/dev/sip or any task you pass)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Prefer run_init.sh, fall back to db_init.sh
DB_INIT="${DB_INIT:-$ROOT/scripts/run_init.sh}"
[[ -x "$DB_INIT" ]] || DB_INIT="$ROOT/scripts/db_init.sh"

API_RUN="${API_RUN:-$ROOT/scripts/run_api.sh}"

CMD="${1:-console}"  # console|dev|api|sip|all|task <name>

need_task() {
  command -v task >/dev/null 2>&1 || {
    echo "❌ 'task' not found. Install go-task: https://taskfile.dev/#/installation"
    exit 1
  }
}

case "$CMD" in
  api)
    bash "$DB_INIT"
    exec bash "$API_RUN"
    ;;

  console)
    need_task
    bash "$DB_INIT"
    exec task intent-console
    ;;

  dev)
    need_task
    bash "$DB_INIT"
    exec task intent-dev
    ;;

  sip)
    need_task
    bash "$DB_INIT"
    exec task sip-lifecycle-dev
    ;;

  all)
    # Bring up DB, run API in background, then start talking via intent-console
    need_task
    bash "$DB_INIT"
    bash "$API_RUN" &
    API_PID=$!
    trap 'kill "$API_PID" 2>/dev/null || true' EXIT
    task intent-console
    ;;

  task)
    # Run any Taskfile target after ensuring DB is up
    need_task
    bash "$DB_INIT"
    shift || true
    exec task "${@:-intent-console}"
    ;;

  *)
    echo "Usage: $(basename "$0") [api|console|dev|sip|all|task <task-name>]" >&2
    exit 1
    ;;
esac
    exit 0