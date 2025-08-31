# services/call_service.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Union, Dict, Any

from sqlalchemy import select, text as sql_text, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Session, Call, CallMessage, CallSender

# ------------ creators / updaters ------------

async def create_call(
    *,
    user_id: Optional[str] = None,
    phone: Optional[str] = None,
    channel: str = "phone",
    issue_category: Optional[str] = None,
    notes: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    instructions: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
    audio_path: Optional[str] = None,
    bundle_path: Optional[str] = None,
) -> dict:
    async with Session() as db:
        c = Call(
            user_id=user_id,
            phone=phone,
            channel=channel,
            issue_category=issue_category,
            notes=notes,
            config_json=config or {},
            instructions_json=instructions or {},
            meta_json=meta or {},
            audio_path=audio_path,
            bundle_path=bundle_path,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return call_to_dict(c)

async def add_call_message(*, call_id: str, sender: Union[str, CallSender], content: str) -> dict:
    if isinstance(sender, str):
        try:
            sender = CallSender(sender)
        except Exception:
            raise ValueError("sender must be 'user' | 'agent' | 'system'")
    async with Session() as db:
        m = CallMessage(call_id=call_id, sender=sender, content=content)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return message_to_dict(m)

async def set_call_artifacts(
    *,
    call_id: str,
    audio_path: Optional[str] = None,
    bundle_path: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> dict:
    async with Session() as db:
        c = await db.get(Call, call_id)
        if not c:
            raise RuntimeError("Call not found")
        if audio_path is not None:
            c.audio_path = audio_path
        if bundle_path is not None:
            c.bundle_path = bundle_path
        if stats:
            c.stats_json.update(stats)
        await db.commit()
        await db.refresh(c)
        return call_to_dict(c)

async def close_call(call_id: str, *, ended_at: Optional[datetime] = None) -> dict:
    async with Session() as db:
        c = await db.get(Call, call_id)
        if not c:
            raise RuntimeError("Call not found")
        c.ended_at = ended_at or datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(c)
        return {"ok": True, "id": call_id, "ended_at": c.ended_at}

# ------------ readers / search ------------

async def list_call_messages(call_id: str) -> List[dict]:
    async with Session() as db:
        rows = (
            await db.execute(
                select(CallMessage)
                .where(CallMessage.call_id == call_id)
                .order_by(CallMessage.created_at.asc())
            )
        ).scalars().all()
        return [message_to_dict(m) for m in rows]

async def search_calls_by_issue(query: str, limit: int = 50) -> List[dict]:
    """Issue-category ILIKE + fallback to message content search (simple)."""
    q = f"%{query}%"
    async with Session() as db:
        calls = (
            await db.execute(
                select(Call)
                .where(or_(Call.issue_category.ilike(q), Call.notes.ilike(q)))
                .order_by(Call.started_at.desc())
                .limit(limit)
            )
        ).scalars().all()

        if len(calls) < limit:
            # Supplement by message content matches
            rows = (
                await db.execute(
                    sql_text(
                        """
                        SELECT DISTINCT c.*
                        FROM call_messages cm
                        JOIN calls c ON c.id = cm.call_id
                        WHERE cm.content ILIKE :q
                        ORDER BY c.started_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"q": q, "lim": limit},
                )
            ).all()
            seen = {c.id for c in calls}
            for row in rows:
                c = row[0]  # row is Row(c,)
                if c.id not in seen:
                    calls.append(c)
                    seen.add(c.id)
                if len(calls) >= limit:
                    break

        return [call_to_dict(c) for c in calls]

# ------------ serializers ------------

def call_to_dict(c: Call) -> dict:
    return {
        "id": str(c.id),
        "user_id": c.user_id,
        "phone": c.phone,
        "channel": c.channel,
        "issue_category": c.issue_category,
        "notes": c.notes,
        "audio_path": c.audio_path,
        "bundle_path": c.bundle_path,
        "config": c.config_json,
        "instructions": c.instructions_json,
        "meta": c.meta_json,
        "stats": c.stats_json,
        "started_at": c.started_at,
        "ended_at": c.ended_at,
    }

def message_to_dict(m: CallMessage) -> dict:
    return {
        "id": str(m.id),
        "call_id": str(m.call_id),
        "sender": m.sender.value if isinstance(m.sender, CallSender) else m.sender,
        "content": m.content,
        "created_at": m.created_at,
    }
