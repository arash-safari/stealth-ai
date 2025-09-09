
<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit logo" width="100" height="100">
</a>

# Plumber AI Agent (Python, LiveKit)

Voice AI assistant built with [LiveKit Agents for Python](https://github.com/livekit/agents). It books, reschedules, and manages plumbing appointments while recording transcripts and (optionally) audio to S3 for compliance and analytics.

## What’s inside

* **Voice agent** pipeline using LiveKit (+ OpenAI/Deepgram/Cartesia by default)
* **FastAPI backend** with scheduling endpoints (techs, availability, appointments, users)
* **PostgreSQL (async)** via SQLAlchemy/asyncpg
* **Call Recorder**

  * Captures final user transcripts & assistant messages → `call_messages` table
  * Optional **S3 transcript JSONL** upload
  * Optional **LiveKit Egress** → audio recording to S3
  * Optional **artifacts table** (`call_artifacts`) for where/what got stored
* **Tests** (pytest + asyncio) including an optional **real S3** integration test

---

## Prerequisites

* **Docker** (for Postgres and optional API container)
* **Python 3.11+** and **uv**: `pip install uv`
* **Task (go-task)** for dev shortcuts (optional):
  macOS `brew install go-task/tap/go-task` · Linux `sh -c "$(curl -fsSL https://taskfile.dev/install.sh)" -- -d` · Windows (Scoop) `scoop install task`
* (Optional) `psql` for manual DB checks
* (Optional) **AWS S3 bucket & credentials** if you want transcript/audio uploads

---

## Quick start

```bash
# install deps
uv sync

# bring up Postgres (+ API) in Docker
docker compose up --build
```

OpenAPI will be at `http://localhost:8000/docs`.

---

## Configure environment

Create `.env.local` in the repo root. Below is a **minimal template**; fill in your values.

```bash
# LiveKit
LIVEKIT_URL=wss://<your-project>.livekit.cloud
LIVEKIT_API_KEY=<your-livekit-api-key>
LIVEKIT_API_SECRET=<your-livekit-api-secret>

# Providers (pick the ones you actually use)
OPENAI_API_KEY=<your-openai-key>
DEEPGRAM_API_KEY=<your-deepgram-key>
CARTESIA_API_KEY=<your-cartesia-key>

# Database (local dev)
DATABASE_URL=postgresql+asyncpg://plumber:plumber@127.0.0.1:55432/plumbing?ssl=false
# For Neon or other hosted PG, ensure SSL is required:
# DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>/<db>?sslmode=require

# App flags
AGENT_NAME=plumber-ai-agent
INTENT_AUDIO=1
DEBUG_PROMPTS=1

# --- Call Recorder / S3 (REQUIRED if you want transcript/audio uploads) ---
# Bucket + region
S3_BUCKET=<your-bucket-name>
S3_REGION=<aws-region>             # e.g. us-east-1
# Credentials (explicit keys recommended for the agent/test runner)
S3_ACCESS_KEY_ID=<aws-access-key-id>
S3_SECRET_ACCESS_KEY=<aws-secret-access-key>
# Optional extras
S3_PREFIX=recordings/              # prefix/folder for uploads
S3_FORCE_PATH_STYLE=0              # set 1 for MinIO/LocalStack, 0 for AWS S3

# Control audio egress (LiveKit → S3 mp4)
RECORD_AUDIO_EGRESS=0              # set 1 to enable audio recording to S3

# --- Tests ---
RUN_REAL_S3_TEST=0                 # set 1 to enable the real S3 integration test
# Optional test cleanup (only used when RUN_REAL_S3_TEST=1)
S3_DELETE_TEST_OBJECTS=1
```

> Tip: if you deploy on LiveKit Cloud, also set `DB_SSLMODE=require` (or use a `?sslmode=require` on `DATABASE_URL`).

---

## Running the agent locally

**Option A – Console (use your mic/speakers on this machine):**

```bash
# pull any required local model assets (once)
uv run python src/agent.py download-files

# start the console agent
task intent-console
# or:
# uv run -m agents.main console
```

**Option B – Use your own web/mobile or SIP frontend:**
Point it to your LiveKit room and this agent.

---

## Call Recorder (how it works)

* **What it records**

  * Final **user** transcripts emitted by the STT event → stored in DB + JSONL buffer
  * **Agent** messages (all `session.say()` calls) → stored in DB + JSONL buffer
* **Batch DB writes**
  Messages are buffered and written in small batches to `call_messages` for efficiency.
* **S3 uploads**

  * On shutdown, the in-memory JSONL transcript is uploaded to `s3://$S3_BUCKET/$S3_PREFIX/YYYY/MM/DD/<room>/<call_id>/transcript.jsonl`.
  * If `RECORD_AUDIO_EGRESS=1` and LiveKit server creds are configured, audio egress starts at the beginning of the call and stops on shutdown, uploading `audio.mp4` to S3.
* **Artifacts table (optional)**

  * If `db.models_artifacts.CallArtifact` is present, the recorder inserts rows describing uploaded files (type, bucket, key, size, etc.).
* **Credentials**

  * The recorder uses explicit `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` from `.env.local` (you don’t need an AWS profile).
  * If you *do* have an `AWS_PROFILE` in your shell, the code avoids it to prevent `ProfileNotFound` issues during tests.

---

## API overview

With the stack running (`docker compose up`):

* `POST /techs` – create tech
* `POST /techs/{tech_id}/availability` – publish availability (stored as UTC)
* `GET /availability` – get available slots (by skill, duration, priority, range)
* `POST /appointments` – book explicit (tech+time) or “earliest by skill”
* `PATCH /appointments/{appointment_no}` – update time/status
* `GET /users`, `POST /users` – basic CRM

Explore and try requests in `http://localhost:8000/docs`.

---

## Testing

We use `pytest` with `pytest-asyncio`.

### 1) Run all tests

```bash
uv run pytest -q
```

> By default, tests load `.env.local` and may **skip** if some required keys (e.g., `OPENAI_API_KEY`) are missing.

### 2) Run only the call recorder tests

```bash
uv run pytest -vv -s tests/test_call_recorder.py
```

There are two tests:

* **Debug (no S3)** – exercises batching and shutdown without writing to S3
* **Real S3** – **optional**, uploads a small JSONL to your bucket

To enable the **real S3** test:

```bash
export RUN_REAL_S3_TEST=1        # or set in .env.local
# required S3 env must be present in .env.local (see above)
uv run pytest -vv -s tests/test_call_recorder.py
```

Cleanup uploaded test objects automatically:

```bash
export S3_DELETE_TEST_OBJECTS=1
uv run pytest -vv -s tests/test_call_recorder.py
```

> If you see `botocore.exceptions.ProfileNotFound: The config profile (default) could not be found`, make sure no `AWS_PROFILE` is exported in your shell when running tests (the test suite unsets it, but double-check your environment).

---

## Troubleshooting

* **Neon/hosted Postgres SSL**
  Use `?sslmode=require` in `DATABASE_URL`, and ensure the async engine is created with SSL required.
* **422 from `/techs/{id}/availability`**
  Dates must be `YYYY-MM-DD` (zero-padded). Example: `2025-09-08`, not `2025-09-8`.
* **S3 uploads don’t appear**
  Ensure `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` are set. For MinIO/LocalStack set `S3_FORCE_PATH_STYLE=1`.
* **Egress didn’t start**
  Requires `RECORD_AUDIO_EGRESS=1` and LiveKit server creds (`LIVEKIT_WS_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`). The test suite disables egress.

---

## Deploying to LiveKit Cloud

If you deploy the agent to LiveKit Cloud:

```bash
# ensure DATABASE_URL uses SSL (or set DB_SSLMODE=require)
lk agent deploy
lk agent status
lk agent logs
```

Load the same `.env.local` with `lk app env -w .env.local` or configure envs in the LK console.

---

## License

MIT (see `LICENSE`)

---
