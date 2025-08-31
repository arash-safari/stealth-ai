# common/call_recorder.py
from __future__ import annotations
import io
import time
import wave
from dataclasses import dataclass


@dataclass
class WavResult:
    wav_bytes: bytes
    stats: dict
    meta: dict


class MemoryCallRecorder:
    """
    Minimal in-memory call recorder.
    - Append 16-bit little-endian PCM frames while the call runs (if/when you can tap them).
    - At the end, wrap all PCM into a single WAV and return bytes + metadata.
    - If you can't tap audio yet, you can still call note_turn() to record dialogue stats
      and produce an (empty) WAV; the structure remains stable for later.
    """
    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        assert channels in (1, 2), "channels must be 1 or 2"
        self._sr = sample_rate
        self._ch = channels
        self._pcm = io.BytesIO()        # raw PCM16 (little-endian)
        self._started = time.time()
        self._agent_turns = 0
        self._user_turns = 0

    # ---- optional hooks if you can tap PCM from your pipeline ----
    def append_pcm16(self, chunk: bytes) -> None:
        """Append raw PCM16. Call this if you can tap audio frames."""
        if not chunk:
            return
        self._pcm.write(chunk)

    def note_turn(self, agent: bool = True) -> None:
        if agent:
            self._agent_turns += 1
        else:
            self._user_turns += 1

    # ---- finalize ----
    def finalize_wav(self) -> WavResult:
        dur = max(0.0, time.time() - self._started)
        raw = self._pcm.getvalue()

        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(self._ch)
            w.setsampwidth(2)  # PCM16
            w.setframerate(self._sr)
            if raw:
                w.writeframes(raw)
        wav_bytes = out.getvalue()

        stats = {
            "duration_sec": round(dur, 3),
            "agent_turns": self._agent_turns,
            "user_turns": self._user_turns,
            "bytes_pcm16": len(raw),
        }
        meta = {
            "mime": "audio/wav",
            "sample_rate": self._sr,
            "channels": self._ch,
        }
        return WavResult(wav_bytes=wav_bytes, stats=stats, meta=meta)
