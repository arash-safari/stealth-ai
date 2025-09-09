# tests/test_call_recorder.py
from __future__ import annotations

import os
import sys
import uuid
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import logging

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.call_recorder import CallRecorder, S3Config
import common.call_recorder as cr_mod  # for monkeypatching globals inside the module

# ---------- very loud logging ----------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TEST")

def _wrap_async(cls, name):
    orig = getattr(cls, name)
    async def wrapper(self, *args, **kwargs):
        t0 = time.perf_counter()
        log.debug(">> %s()", name)
        try:
            return await orig(self, *args, **kwargs)
        finally:
            log.debug("<< %s() [%.3fs]", name, time.perf_counter() - t0)
    return wrapper

def _instrument_core(monkeypatch):
    # Wrap selected methods for timing/trace
    for m in [
        "_drain_and_stop_consumer",
        "_upload_transcript_jsonl",
        "shutdown",
    ]:
        if hasattr(CallRecorder, m):
            monkeypatch.setattr(CallRecorder, m, _wrap_async(CallRecorder, m), raising=True)
            log.debug("instrumented CallRecorder.%s", m)

# ---------- minimal fake LiveKit session ----------
class FakeRoom:
    def __init__(self, name="room"):
        self.name = name

class FakeLKSession:
    def __init__(self, room_name="room"):
        self._listeners = {}
        self.room = FakeRoom(room_name)
        self._said = []

        async def _say(text: str, *args, **kwargs):
            log.debug("[FakeLK] say(): %r", text)
            self._said.append((text, kwargs))
            await asyncio.sleep(0)
        self.say = _say

    def on(self, event: str):
        def reg(fn):
            self._listeners.setdefault(event, []).append(fn)
            log.debug("[FakeLK] registered %s", event)
            return fn
        return reg

    def fire(self, event: str, evt):
        log.debug("[FakeLK] fire(%s)", event)
        for fn in self._listeners.get(event, []):
            fn(evt)

# ---------- fake DB session factory ----------
class _FakeAsyncSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False
    async def get(self, *args, **kwargs):
        log.debug("FakeDB.get(%s, %s)", args, kwargs)
        return None
    def add(self, *args, **kwargs):
        log.debug("FakeDB.add(%s, %s)", args, kwargs)
    async def commit(self):
        log.debug("FakeDB.commit()")

class _FakeSessionFactory:
    def __call__(self, *args, **kwargs):
        return _FakeAsyncSession()

# ---------- lightweight _start that does NOT spawn consumer/egress ----------
async def _fake_start_no_consumer(self: CallRecorder) -> None:
    if getattr(self, "_started", False):
        return
    self._started = True
    log.debug("[_fake_start_no_consumer] begin")

    # patch say
    self._patch_say()

    # attach user transcript handler (final only)
    try:
        @self.session.on("user_input_transcribed")
        def _on_user(evt):
            try:
                is_final = bool(getattr(evt, "is_final", False))
                text = getattr(evt, "transcript", "") or ""
                if is_final and text.strip():
                    self._on_user_text(text.strip())
            except Exception:
                logging.getLogger("plumber-contact-center").exception("user_input_transcribed handler failed")
    except Exception:
        logging.getLogger("plumber-contact-center").debug(
            "Session does not expose user_input_transcribed; recorder continues without"
        )

    # auto-shutdown on disconnect (not used in tests)
    try:
        @self.session.on("disconnected")
        def _on_disc(_evt):
            asyncio.create_task(self.shutdown())
    except Exception:
        pass

    # DO NOT: start consumer loop
    self._consumer_task = None

    # DO NOT: start egress
    log.debug("[_fake_start_no_consumer] done")

# ---------- FAST TEST: no S3, no DB, no consumer ----------
@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_call_recorder_debug_no_s3(monkeypatch):
    monkeypatch.setenv("RECORD_AUDIO_EGRESS", "0")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    # Disable S3 completely
    monkeypatch.setattr(S3Config, "from_env", staticmethod(lambda: None), raising=True)

    # Stub DB (shutdown() and artifacts write paths)
    monkeypatch.setattr(cr_mod, "Session", _FakeSessionFactory(), raising=True)
    # Ensure artifacts are ignored even if table exists
    monkeypatch.setattr(cr_mod, "_HAS_ARTIFACTS", False, raising=True)

    # Prevent consumer loop creation
    monkeypatch.setattr(CallRecorder, "_start", _fake_start_no_consumer, raising=True)

        # Instrument a few core methods for timing
    _instrument_core(monkeypatch)

    # Also stub batch writer so we can assert what would be written
    stored = []
    async def fake_insert(self, items):
        log.debug("fake_insert_messages: %d item(s)", len(items))
        stored.extend(items)
    monkeypatch.setattr(CallRecorder, "_insert_messages", fake_insert, raising=True)

    sess = FakeLKSession(room_name="dbg-room")
    rec = await CallRecorder.enable(sess, call_id=str(uuid.uuid4()))

    class Evt:
        is_final = True
        transcript = "Hello there"
    sess.fire("user_input_transcribed", Evt())
    await sess.say("We can help with that.", allow_interruptions=False)

    await rec.shutdown()

    # Validate batch order/content
    assert len(stored) == 2
    assert stored[0][0].name == "user" and stored[0][1] == "Hello there"
    assert stored[1][0].name == "agent" and "We can help" in stored[1][1]

# ---------- REAL S3 (opt-in): uploads JSONL, but still no DB/consumer ----------
run_real_s3 = os.getenv("RUN_REAL_S3_TEST") == "1"
has_min_s3_env = bool(os.getenv("S3_BUCKET")) and (
    (os.getenv("S3_ACCESS_KEY_ID") and os.getenv("S3_SECRET_ACCESS_KEY"))
    or (os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))
    or os.getenv("AWS_PROFILE")
)

@pytest.mark.skipif(not run_real_s3, reason="Set RUN_REAL_S3_TEST=1 to enable")
@pytest.mark.skipif(not has_min_s3_env, reason="S3 env not configured")
@pytest.mark.timeout(25)
@pytest.mark.asyncio
async def test_call_recorder_real_s3_with_debug(monkeypatch):
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError

    monkeypatch.setenv("RECORD_AUDIO_EGRESS", "0")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("AWS_SDK_LOAD_CONFIG", "0")

    # Prevent consumer loop
    monkeypatch.setattr(CallRecorder, "_start", _fake_start_no_consumer, raising=True)

    # Still stub DB; skip artifacts to avoid .add()/.commit()
    monkeypatch.setattr(cr_mod, "Session", _FakeSessionFactory(), raising=True)
    monkeypatch.setattr(cr_mod, "_HAS_ARTIFACTS", False, raising=True)

    _instrument_core(monkeypatch)

    sess = FakeLKSession(room_name="it-room")
    call_id = str(uuid.uuid4())
    rec = await CallRecorder.enable(sess, call_id=call_id)

    class Evt:
        is_final = True
        transcript = "Hello from IT test"
    sess.fire("user_input_transcribed", Evt())
    await sess.say("Acknowledged. Proceeding to confirm.", allow_interruptions=False)

    await rec.shutdown()

    # Expected key (same logic as recorder)
    prefix = (os.getenv("S3_PREFIX") or "recordings/").rstrip("/") + "/"
    date_path = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    room = sess.room.name
    expected_key = f"{prefix}{date_path}/{room}/{call_id}/transcript.jsonl"

    region = (
        os.getenv("S3_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    endpoint = os.getenv("S3_ENDPOINT")
    force_path_style = os.getenv("S3_FORCE_PATH_STYLE") == "1"

    # Prefer env creds; avoid default profile stalls
    aws_access_key_id = os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_session_token = os.getenv("S3_SESSION_TOKEN") or os.getenv("AWS_SESSION_TOKEN")

    session = boto3.session.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_session_token=aws_session_token,
        region_name=region,
    )
    s3 = session.client(
        "s3",
        endpoint_url=endpoint,
        config=BotoConfig(
            s3={"addressing_style": "path" if force_path_style else "auto"},
            connect_timeout=5,
            read_timeout=5,
            retries={"total_max_attempts": 2, "mode": "standard"},
        ),
    )

    bucket = os.getenv("S3_BUCKET")
    log.debug("Checking S3 for s3://%s/%s", bucket, expected_key)
    try:
        obj = s3.get_object(Bucket=bucket, Key=expected_key)
    except ClientError as e:
        pytest.fail(f"Expected transcript not found at s3://{bucket}/{expected_key} ({e})")

    body = obj["Body"].read().decode("utf-8", errors="replace")
    log.debug("S3 body length=%d", len(body))
    assert "Hello from IT test" in body
    assert "Acknowledged. Proceeding to confirm." in body

    if os.getenv("S3_DELETE_TEST_OBJECTS") == "1":
        try:
            s3.delete_object(Bucket=bucket, Key=expected_key)
            log.debug("Deleted test object s3://%s/%s", bucket, expected_key)
        except Exception:
            log.warning("Failed to delete test object", exc_info=True)
