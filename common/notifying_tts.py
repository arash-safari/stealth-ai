# common/notifying_tts.py
from __future__ import annotations
import inspect
from typing import Any, Awaitable, Callable, Optional


class NotifyingTTS:
    """
    Wraps a TTS engine and fires a callback when an utterance completes.

    Notes:
    - We proxy all attributes to the inner engine so AgentSession can use it normally.
    - If your TTS engine exposes async methods like `speak`, `say`, or `synthesize`,
      we intercept those names and run the `on_utterance_final` callback *after* they finish.
    - If your engine yields audio frames (async generator), tap them here to push PCM into
      your recorder (not shown by default because LiveKit engines differ).
    """

    def __init__(
        self,
        *,
        inner: Any,
        on_utterance_final: Optional[Callable[[str], Awaitable[None] | None]] = None,
    ):
        self._inner = inner
        self._on_utterance_final = on_utterance_final

    @property
    def proxy(self) -> Any:
        """Return the wrapped engine instance (for AgentSession)."""
        return self

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        # Intercept common synthesis methods by name:
        if name in {"speak", "say", "synthesize"} and callable(target):
            def wrapper(*args, **kwargs):
                # Try to extract the text argument in the common ways
                text = kwargs.get("text")
                if text is None and args:
                    text = args[0] if isinstance(args[0], str) else None

                res = target(*args, **kwargs)

                async def _finish_cb():
                    if self._on_utterance_final and text:
                        maybe = self._on_utterance_final(text)
                        if inspect.isawaitable(maybe):
                            await maybe

                if inspect.isawaitable(res):
                    async def _run():
                        out = await res
                        await _finish_cb()
                        return out
                    return _run()
                else:
                    # best-effort (sync engine)
                    maybe2 = _finish_cb()
                    if inspect.isawaitable(maybe2):
                        # Fire and forget
                        import asyncio
                        asyncio.create_task(maybe2)
                    return res
            return wrapper
        return target
