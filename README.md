<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit logo" width="100" height="100">
</a>

# LiveKit Agents Starter - Python

A complete starter project for building voice AI apps with [LiveKit Agents for Python](https://github.com/livekit/agents).

The starter project includes:

* A simple voice AI assistant based on the [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai/)
* Voice AI pipeline based on [OpenAI](https://docs.livekit.io/agents/integrations/llm/openai/), [Cartesia](https://docs.livekit.io/agents/integrations/tts/cartesia/), and [Deepgram](https://docs.livekit.io/agents/integrations/llm/deepgram/)

  * Easily integrate your preferred [LLM](https://docs.livekit.io/agents/integrations/llm/), [STT](https://docs.livekit.io/agents/integrations/stt/), and [TTS](https://docs.livekit.io/agents/integrations/tts/) instead, or swap to a realtime model like the [OpenAI Realtime API](https://docs.livekit.io/agents/integrations/realtime/openai)
* Eval suite based on the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/build/testing/)
* [LiveKit Turn Detector](https://docs.livekit.io/agents/build/turns/turn-detector/) for contextually-aware speaker detection, with multilingual support
* [LiveKit Cloud enhanced noise cancellation](https://docs.livekit.io/home/cloud/noise-cancellation/)
* Integrated [metrics and logging](https://docs.livekit.io/agents/build/metrics/)

This starter app is compatible with any [custom web/mobile frontend](https://docs.livekit.io/agents/start/frontend/) or [SIP-based telephony](https://docs.livekit.io/agents/start/telephony/).

---

## Prerequisites

* **Docker Desktop** (for Postgres + API container). Open it so the daemon is running.

  * Check: `docker --version`
* **uv** (Python package manager): `pip install uv`
* **Task (go-task)** for the console/worker shortcuts:
  macOS: `brew install go-task/tap/go-task` · Linux: `sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d` · Windows (Scoop): `scoop install task`
* (Optional) `psql` client for manual DB checks

> Not using Docker? You can run a native PostgreSQL 16+ and skip Compose—see **Without Docker** below.

---

## Dev Setup

```bash
cd agent-starter-python
uv sync
```

Create `.env.local` (edit placeholders):

```bash
cat > .env.local <<'EOF'
LIVEKIT_URL=wss://<your-project>.livekit.cloud
LIVEKIT_API_KEY=<your-livekit-api-key>
LIVEKIT_API_SECRET=<your-livekit-api-secret>

# Optional/dev flags
INTENT_AUDIO=1
DEBUG_PROMPTS=1

# Providers
CARTESIA_API_KEY=<your-cartesia-key>
DEEPGRAM_API_KEY=<your-deepgram-key>
OPENAI_API_KEY=<your-openai-key>

# App/test settings
AGENT_NAME=plumber-ai-agent
TEST_USER_MODEL=gpt-4o-mini
TEST_MAX_TURNS=8

# Local Postgres (adjust if needed)
DATABASE_URL=postgresql+asyncpg://plumber:plumber@127.0.0.1:55432/plumbing?ssl=false
EOF
```

> You can also load these into your LiveKit app envs with `lk app env -w .env.local`.

---

## Run the agent (Option A: talk from your machine)

This mode uses Docker **only** for Postgres (and optionally the API). You’ll speak through your local mic/speakers.

### 1) Start Postgres (+ API) in Docker

In one terminal, bring the stack up (Postgres waits until healthy; API serves on `http://localhost:8000`).

```bash
# build and run containers
docker compose up --build
```

* Hitting `GET /` on the API may return **404** (normal). Try `/docs` for the OpenAPI UI.

### 2) Start the live console on your host

In a second terminal on your machine:

```bash
# download required models once
uv run python src/agent.py download-files

# start talking (uses your mic/speakers)
# via Taskfile shortcut:
task intent-console

# or directly without Task:
# uv run -m agents.main console
```

> Want a single helper command? You can also use `scripts/run_all.sh` (default = console). Make it executable once with `chmod +x scripts/run_all.sh` then run `scripts/run_all.sh`.

---

## Without Docker (native Postgres)

If you have PostgreSQL installed locally:

```sql
-- Run in psql as a superuser
CREATE ROLE plumber WITH LOGIN PASSWORD 'plumber';
CREATE DATABASE plumbing OWNER plumber;
```

Set your `DATABASE_URL` (often port `5432`):

```
DATABASE_URL=postgresql+asyncpg://plumber:plumber@127.0.0.1:5432/plumbing?ssl=false
```

Then skip Compose and just run the console:

```bash
uv run python src/agent.py download-files
task intent-console
```

