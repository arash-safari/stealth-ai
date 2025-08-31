# common/event_bus.py
from __future__ import annotations
import asyncio
from typing import Awaitable, Callable, Dict, List, Any


class EventBus:
    """
    Small async event bus for per-call notifications.
    Usage:
        bus = EventBus()
        async def on_ev(payload): ...
        bus.on("agent_speech_final", on_ev)
        await bus.emit("agent_speech_final", {"text": "hello"})
    """
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[dict], Awaitable[None] | None]]] = {}

    def on(self, name: str, handler: Callable[[dict], Awaitable[None] | None]) -> None:
        self._handlers.setdefault(name, []).append(handler)

    async def emit(self, name: str, payload: dict) -> None:
        for h in self._handlers.get(name, []):
            try:
                res = h(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                # don't crash the bus
                print(f"[EventBus] handler error for {name}: {e}")
