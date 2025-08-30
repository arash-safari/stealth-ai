FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install uv (no -y flag; install to /usr/local/bin)
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY . .

# Use uv to install deps
ENV UV_SYSTEM_PYTHON=1
RUN uv sync --frozen || uv sync

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app



EXPOSE 8000
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
