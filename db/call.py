"""
Call handling service (async)
----------------------------
Functions:
  - create_call(user_id: Optional[str], phone: Optional[str], channel='phone', issue_category: Optional[str], notes: Optional[str])
  - add_call_message(call_id: str, sender: CallSender, content: str)
  - list_call_messages(call_id: str)
  - search_calls_by_issue(query: str, limit: int = 50)
  - close_call(call_id: str)

Depends on models.py (SQLAlchemy async).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, or_, text as sql_text

from models import Session, Call, CallMessage, CallSender


async def create_call(
    *,
    user_id: Optional[str] = None,
    phone: Optional[str] = None,
    channel: str = "phone",
    issue_category: Optional[str] = None,
    notes: Optional[str] = None,
):
    async with Session() as db:
        c = Call(
            user_id=uuid.UUID(user_id) if user_id else None,
            phone=phone,
            channel=channel,
            issue_category=issue_category,
            notes=notes,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return {
            "id": str(c.id),
            "user_id": str(c.user_id) if c.user_id else None,
            "phone": c.phone,
            "channel": c.channel,
            "issue_category": c.issue_category,
            "started_at": c.started_at,
        }


async def add_call_message(*, call_id: str, sender: str, content: str):
    if sender not in (CallSender.user, CallSender.agent, CallSender.system):
        raise ValueError("sender must be 'user' | 'agent' | 'system'")
    async with Session() as db:
        m = CallMessage(call_id=uuid.UUID(call_id), sender=sender, content=content)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return {"id": str(m.id), "call_id": call_id, "sender": m.sender, "content": m.content, "created_at": m.created_at}


async def list_call_messages(call_id: str) -> List[dict]:
    async with Session() as db:
        rows = (
            await db.execute(
                select(CallMessage).where(CallMessage.call_id == uuid.UUID(call_id)).order_by(CallMessage.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in rows
        ]


async def search_calls_by_issue(query: str, limit: int = 50) -> List[dict]:
    """Find calls with issue_category matching query OR message content matching query (ILIKE)."""
    q = f"%{query}%"
    async with Session() as db:
        calls = (
            await db.execute(
                select(Call).where(Call.issue_category.ilike(q)).order_by(Call.started_at.desc()).limit(limit)
            )
        ).scalars().all()
        if len(calls) < limit:
            # supplement with message-content search for calls not already included
            call_ids = {c.id for c in calls}
            msg_calls = (
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
            for row in msg_calls:
                c = row[0]
                if c.id not in call_ids:
                    calls.append(c)
                if len(calls) >= limit:
                    break
        return [
            {
                "id": str(c.id),
                "user_id": str(c.user_id) if c.user_id else None,
                "phone": c.phone,
                "channel": c.channel,
                "issue_category": c.issue_category,
                "started_at": c.started_at,
                "ended_at": c.ended_at,
            }
            for c in calls
        ]


async def close_call(call_id: str):
    async with Session() as db:
        c = await db.get(Call, uuid.UUID(call_id))
        if not c:
            raise RuntimeError("Call not found")
        c.ended_at = datetime.now(timezone.utc)
        await db.commit()
        return {"ok": True}
