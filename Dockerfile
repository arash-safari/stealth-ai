FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app

# deps first for cache
COPY pyproject.toml uv.lock* ./
ENV UV_SYSTEM_PYTHON=1
RUN uv sync --frozen || uv sync

# app code
COPY . .

# make sure Python can import your packages from /app and /app/src
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src

# 👉 Start the LiveKit worker (joins rooms when dispatched/auto-dispatched)
CMD ["uv", "run", "-m", "agents.main", "dev"]
# If your entry is actually src/agent.py, use:
# CMD ["uv", "run", "python", "src/agent.py", "dev"]
