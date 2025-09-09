# common/call_recorder.py
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, List, Tuple

# --- DB models & session ---
from db.session import Session  # async_sessionmaker
from db.models import Call, CallMessage, CallSender, utcnow

# Optional artifacts table (recommended; safe if missing)
try:
    from db.models_artifacts import CallArtifact  # type: ignore
    _HAS_ARTIFACTS = True
except Exception:  # pragma: no cover
    CallArtifact = None  # type: ignore
    _HAS_ARTIFACTS = False

# --- LiveKit optional egress client ---
try:
    from livekit import egress as lk_egress  # type: ignore
except Exception:  # pragma: no cover
    lk_egress = None

logger = logging.getLogger("plumber-contact-center")


# =========================
# S3 configuration
# =========================
@dataclass
class S3Config:
    bucket: str
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    prefix: str = "recordings/"
    force_path_style: bool = False
    sse: Optional[str] = None            # "AES256" or "aws:kms"
    sse_kms_key_id: Optional[str] = None # if sse == "aws:kms"

    @staticmethod
    def from_env() -> Optional["S3Config"]:
        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            return None
        return S3Config(
            bucket=bucket,
            region=os.getenv("S3_REGION"),
            endpoint_url=os.getenv("S3_ENDPOINT"),
            prefix=os.getenv("S3_PREFIX", "recordings/"),
            force_path_style=os.getenv("S3_FORCE_PATH_STYLE") == "1",
            sse=os.getenv("S3_SSE"),  # "AES256" or "aws:kms"
            sse_kms_key_id=os.getenv("S3_SSE_KMS_KEY_ID"),
        )


# =========================
# Recorder
# =========================
def _unset(d, *keys):
    saved = {}
    for k in keys:
        saved[k] = d.pop(k, None)
    return saved

class CallRecorder:
    """
    Attaches to a LiveKit AgentSession to:
      - Persist final user transcripts & assistant messages to DB (CallMessage).
      - Optionally start/stop egress to S3.
      - Upload a JSONL transcript to S3 at shutdown, and (optionally) register artifacts.

    Env (read):
      S3_BUCKET, S3_REGION, [S3_ENDPOINT], [S3_PREFIX], [S3_FORCE_PATH_STYLE=1]
      S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, [S3_SESSION_TOKEN]  (or AWS_* equivalents)
      LIVEKIT_WS_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
      [RECORD_AUDIO_EGRESS=1]

    Usage:
      rec = await CallRecorder.enable(session, call_id=<uuid_str>)
      ...
      await rec.shutdown()   # on hangup/cleanup
    """

    # ~0.5MB before JSONL flush (optional rotation)
    _MAX_JSONL_BYTES = 512_000

    def __init__(self, session, call_id: str, s3cfg: Optional[S3Config]) -> None:
        self.session = session
        self.call_id = call_id
        self.s3cfg = s3cfg

        self._started = False
        self._closed = False

        # message batching
        self._queue: "asyncio.Queue[Tuple[CallSender, str, datetime]]" = asyncio.Queue()
        self._consumer_task: Optional[asyncio.Task] = None

        # assistant say wrapper
        self._orig_say = None  # type: ignore

        # dedupe hashes
        self._last_user_text: Optional[str] = None
        self._last_agent_text: Optional[str] = None

        # egress state
        self._egress_id: Optional[str] = None
        self._audio_key: Optional[str] = None

        # rolling transcript (for JSONL upload)
        self._jsonl_buffer: List[dict] = []

    # -------- public API --------
    @classmethod
    async def enable(cls, session, *, call_id: str) -> "CallRecorder":
        """
        Idempotent. Attaches event listeners and starts batch consumer. Optionally starts egress.
        """
        existing = getattr(session, "_call_recorder", None)
        if isinstance(existing, CallRecorder):
            logger.debug("CallRecorder.enable(): already attached (call_id=%s)", existing.call_id)
            return existing

        s3cfg = S3Config.from_env()
        rec = cls(session=session, call_id=call_id, s3cfg=s3cfg)
        session._call_recorder = rec  # attach
        await rec._start()
        return rec

    async def shutdown(self) -> None:
        """Flush queues, stop egress, upload transcript JSONL, and mark closed."""
        if self._closed:
            return
        self._closed = True

        # Stop consumer
        await self._drain_and_stop_consumer()

        # Stop egress (best-effort)
        await self._stop_egress_safe()

        # Upload transcript JSONL to S3 (best-effort)
        await self._upload_transcript_jsonl()

        # restore say
        self._unpatch_say()

        # Mark call ended
        try:
            async with Session() as db:
                call = await db.get(Call, uuid.UUID(self.call_id))
                if call and not call.ended_at:
                    call.ended_at = utcnow()
                    await db.commit()
        except Exception:
            logger.exception("Failed to set Call.ended_at")

        logger.info("CallRecorder shutdown complete (call_id=%s)", self.call_id)

    # -------- internals --------
    async def _start(self) -> None:
        if self._started:
            return
        self._started = True

        # Patch say to capture assistant messages
        self._patch_say()

        # Attach transcript event (final only)
        try:
            @self.session.on("user_input_transcribed")
            def _on_user(evt):
                try:
                    is_final = bool(getattr(evt, "is_final", False))
                    text = getattr(evt, "transcript", "") or ""
                    if is_final and text.strip():
                        self._on_user_text(text.strip())
                except Exception:
                    logger.exception("user_input_transcribed handler failed")
        except Exception:
            # SDK may not expose; non-fatal
            logger.debug("Session does not expose user_input_transcribed; recorder continues without")

        # Best-effort auto-shutdown on disconnect
        try:
            @self.session.on("disconnected")
            def _on_disc(_evt):
                asyncio.create_task(self.shutdown())
        except Exception:
            pass

        # Start batch consumer
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="callrec-consumer")

        # Optionally start egress
        await self._start_egress_safe()

        logger.info("CallRecorder started (call_id=%s)", self.call_id)

    # ---- say patch
    def _patch_say(self) -> None:
        if self._orig_say is not None:
            return
        self._orig_say = self.session.say

        async def wrapped_say(text: str, *args, **kwargs):
            # Record assistant message *before* say; more reliable if TTS fails
            if isinstance(text, str) and text.strip():
                self._on_agent_text(text.strip())
            return await self._orig_say(text, *args, **kwargs)

        self.session.say = wrapped_say  # type: ignore

    def _unpatch_say(self) -> None:
        if self._orig_say is not None:
            self.session.say = self._orig_say  # type: ignore
            self._orig_say = None

    # ---- message capture
    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(s.split()).strip().lower()

    def _on_user_text(self, text: str) -> None:
        norm = self._norm(text)
        if norm and norm == self._last_user_text:
            return
        self._last_user_text = norm
        self._jsonl_buffer.append({"ts": _now_iso(), "sender": "user", "text": text})
        self._queue.put_nowait((CallSender.user, text, utcnow()))
        asyncio.create_task(self._maybe_flush_jsonl())

    def _on_agent_text(self, text: str) -> None:
        norm = self._norm(text)
        if norm and norm == self._last_agent_text:
            return
        self._last_agent_text = norm
        self._jsonl_buffer.append({"ts": _now_iso(), "sender": "agent", "text": text})
        self._queue.put_nowait((CallSender.agent, text, utcnow()))
        asyncio.create_task(self._maybe_flush_jsonl())

    # Optional: log system events (handoffs, warnings, etc.)
    def record_system(self, text: str) -> None:
        if not text:
            return
        norm = self._norm(text)
        if not norm:
            return
        self._jsonl_buffer.append({"ts": _now_iso(), "sender": "system", "text": text})
        self._queue.put_nowait((CallSender.system, text, utcnow()))

    # ---- batch writer
    async def _consumer_loop(self) -> None:
        BATCH_MS = 0.4
        MAX_BATCH = 50
        try:
            while True:
                items: List[Tuple[CallSender, str, datetime]] = []
                item = await self._queue.get()  # wait for first or cancellation
                items.append(item)
                # small window to batch more
                try:
                    while len(items) < MAX_BATCH:
                        nxt = await asyncio.wait_for(self._queue.get(), timeout=BATCH_MS)
                        items.append(nxt)
                except asyncio.TimeoutError:
                    pass

                await self._insert_messages(items)
        except asyncio.CancelledError:
            # flush remaining
            await self._drain_remaining()
            raise
        except Exception:
            logger.exception("CallRecorder consumer crashed; restarting")
            await asyncio.sleep(0.5)
            if not self._closed:
                self._consumer_task = asyncio.create_task(self._consumer_loop(), name="callrec-consumer")

    async def _insert_messages(self, items: List[Tuple[CallSender, str, datetime]]) -> None:
        if not items:
            return
        try:
            async with Session() as db:
                call = await db.get(Call, uuid.UUID(self.call_id))
                if not call:
                    logger.warning("Call %s not found when writing CallMessage; skipping batch", self.call_id)
                    return
                for sender, content, created_at in items:
                    db.add(CallMessage(
                        call_id=call.id,
                        sender=sender,
                        content=content,
                        created_at=created_at,
                    ))
                await db.commit()
        except Exception:
            logger.exception("Failed to write CallMessage batch")

    async def _drain_remaining(self) -> None:
        items: List[Tuple[CallSender, str, datetime]] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if items:
            await self._insert_messages(items)

    async def _drain_and_stop_consumer(self) -> None:
        if self._consumer_task:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
            self._consumer_task = None
        await self._drain_remaining()

    # ---------- Egress ----------
    async def _start_egress_safe(self) -> None:
        if not lk_egress:
            return
        if not os.getenv("RECORD_AUDIO_EGRESS", "1") == "1":
            return

        # Requires LiveKit server credentials + egress ws url
        lk_url = os.getenv("LIVEKIT_WS_URL")
        lk_api_key = os.getenv("LIVEKIT_API_KEY")
        lk_api_secret = os.getenv("LIVEKIT_API_SECRET")
        if not (lk_url and lk_api_key and lk_api_secret):
            logger.debug("Egress not started: missing LIVEKIT_{WS_URL,API_KEY,API_SECRET}")
            return

        # Build S3 upload path
        if self.s3cfg:
            base = self.s3cfg.prefix.rstrip("/") + "/"
            date = datetime.now(timezone.utc).strftime("%Y/%m/%d")
            room = getattr(getattr(self.session, "room", None), "name", "room")
            key = f"{base}{date}/{room}/{self.call_id}/audio.mp4"
        else:
            key = None

        # Prepare output (S3 recommended)
        outputs: list[Any] = []
        if self.s3cfg and key:
            # include session token if present
            s3_kwargs: dict[str, Any] = dict(
                access_key=os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID"),
                secret=os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
                bucket=self.s3cfg.bucket,
                region=self.s3cfg.region,
                endpoint=self.s3cfg.endpoint_url,
                key=key,
                force_path_style=self.s3cfg.force_path_style,
            )
            sess_tok = os.getenv("S3_SESSION_TOKEN") or os.getenv("AWS_SESSION_TOKEN")
            if sess_tok:
                s3_kwargs["session_token"] = sess_tok  # LiveKit egress supports this

            s3_output = lk_egress.S3Upload(**s3_kwargs)
            outputs.append(s3_output)
            self._audio_key = key
        else:
            # fallback: file output to the server running egress (only if configured)
            outfile = f"/tmp/{self.call_id}.mp4"
            outputs.append(lk_egress.FileOutput(filename=outfile))
            self._audio_key = None

        # Start room composite egress (best for whole conversation)
        try:
            cli = lk_egress.EgressClient(lk_url, lk_api_key, lk_api_secret)
            res = await cli.start_room_composite_egress(
                room_name=getattr(getattr(self.session, "room", None), "name", "room"),
                layout="speaker",  # or "grid"
                audio_only=True,
                video_only=False,
                outputs=outputs,
            )
            self._egress_id = res.egress_id
            logger.info("Started egress: id=%s key=%s", self._egress_id, self._audio_key)
            # record artifact row early (without size/etag)
            await self._upsert_audio_artifact()
        except Exception:
            logger.exception("Failed to start LiveKit egress")

    async def _stop_egress_safe(self) -> None:
        if not lk_egress or not self._egress_id:
            return
        lk_url = os.getenv("LIVEKIT_WS_URL")
        lk_api_key = os.getenv("LIVEKIT_API_KEY")
        lk_api_secret = os.getenv("LIVEKIT_API_SECRET")
        try:
            cli = lk_egress.EgressClient(lk_url, lk_api_key, lk_api_secret)
            await cli.stop_egress(self._egress_id)
            logger.info("Stopped egress id=%s", self._egress_id)
        except Exception:
            logger.exception("Failed to stop LiveKit egress")

    async def _upsert_audio_artifact(self) -> None:
        if not _HAS_ARTIFACTS or not self.s3cfg or not self._audio_key:
            return
        try:
            async with Session() as db:
                db.add(CallArtifact(
                    call_id=uuid.UUID(self.call_id),
                    type="audio_recording",
                    provider="s3",
                    bucket=self.s3cfg.bucket,
                    object_key=self._audio_key,
                    region=self.s3cfg.region,
                    endpoint=self.s3cfg.endpoint_url,
                    egress_id=self._egress_id,
                    content_type="video/mp4",  # audio-only mp4 container
                ))
                await db.commit()
        except Exception:
            logger.exception("Failed to write audio artifact row")

    # ---------- Transcript upload ----------
    async def _upload_transcript_jsonl(self) -> None:
        if not self.s3cfg:
            return
        if not self._jsonl_buffer:
            return

        # Build key
        base = self.s3cfg.prefix.rstrip("/") + "/"
        date = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        room = getattr(getattr(self.session, "room", None), "name", "room")
        key = f"{base}{date}/{room}/{self.call_id}/transcript.jsonl"

        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in self._jsonl_buffer)
        try:
            import boto3
            from botocore.config import Config as BotoConfig

            session = boto3.session.Session(
                aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
                aws_session_token=os.getenv("S3_SESSION_TOKEN") or os.getenv("AWS_SESSION_TOKEN"),
                region_name=self.s3cfg.region or "us-east-1",
            )
            s3 = session.client(
                "s3",
                endpoint_url=self.s3cfg.endpoint_url,
                config=BotoConfig(
                    s3={"addressing_style": "path" if self.s3cfg.force_path_style else "auto"}
                ),
            )
            extra: dict[str, Any] = {"ContentType": "application/json"}
            if self.s3cfg.sse:
                extra["ServerSideEncryption"] = self.s3cfg.sse
            if self.s3cfg.sse_kms_key_id:
                extra["SSEKMSKeyId"] = self.s3cfg.sse_kms_key_id

            s3.put_object(Bucket=self.s3cfg.bucket, Key=key, Body=body.encode("utf-8"), **extra)
            logger.info("Uploaded transcript JSONL to s3://%s/%s", self.s3cfg.bucket, key)

            # artifact row
            if _HAS_ARTIFACTS:
                async with Session() as db:
                    db.add(CallArtifact(
                        call_id=uuid.UUID(self.call_id),
                        type="transcript_json",
                        provider="s3",
                        bucket=self.s3cfg.bucket,
                        object_key=key,
                        region=self.s3cfg.region,
                        endpoint=self.s3cfg.endpoint_url,
                        content_type="application/json",
                        size_bytes=len(body.encode("utf-8")),
                    ))
                    await db.commit()
        except Exception:
            logger.exception("Failed to upload transcript JSONL to S3")

    # ---------- Buffer rotation ----------
    def _jsonl_size_estimate(self) -> int:
        # cheap estimate; good enough for rotation gating
        return sum(len(json.dumps(x, ensure_ascii=False)) + 1 for x in self._jsonl_buffer)

    async def _maybe_flush_jsonl(self) -> None:
        try:
            if self._jsonl_buffer and self._jsonl_size_estimate() >= self._MAX_JSONL_BYTES:
                await self._upload_transcript_jsonl()
                self._jsonl_buffer.clear()
        except Exception:
            logger.exception("Failed rotating transcript JSONL")

# =========================
# Helpers
# =========================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
