"""
User data access layer (async)
------------------------------
Functions:
  - create_user(full_name, phone, email=None)
  - get_user(user_id)
  - get_user_by_phone(phone)
  - update_user(user_id, **fields)
  - add_address(user_id, *, line1, line2=None, city=None, state=None, postal_code=None, label=None, is_default=False)
  - set_default_address(user_id, address_id)
  - list_addresses(user_id)
  - update_address(address_id, **fields)
  - delete_address(address_id)
  - get_default_address(user_id)

Notes:
  - All timestamps are UTC-aware.
  - Setting an address default is transactional: others are toggled off within the same tx.
  - Timestamps in responses are ISO 8601 strings for JSON-friendliness.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError

from db.models import Session, User, Address


# ---------- helpers ----------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _normalize_email(email: Optional[str]) -> Optional[str]:
    return email.lower().strip() if email else None


def _addr_dict(a: Address) -> Dict[str, Any]:
    return {
        "id": str(a.id),
        "user_id": str(a.user_id),
        "label": a.label,
        "line1": a.line1,
        "line2": a.line2,
        "city": a.city,
        "state": a.state,
        "postal_code": a.postal_code,
        "is_default": a.is_default,
        "created_at": _iso(a.created_at),
        "updated_at": _iso(a.updated_at),
    }


# ---------- public API ----------
async def create_user(*, full_name: str, phone: str, email: Optional[str] = None) -> Dict[str, Any]:
    if not full_name or not phone:
        raise ValueError("full_name and phone are required")
    email = _normalize_email(email)

    async with Session() as db:
        u = User(full_name=full_name.strip(), phone=phone.strip(), email=email)
        db.add(u)
        try:
            await db.commit()
            await db.refresh(u)
        except IntegrityError as e:
            await db.rollback()
            raise RuntimeError("Phone already exists") from e

        return {"id": str(u.id), "full_name": u.full_name, "phone": u.phone, "email": u.email}


async def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    async with Session() as db:
        u = await db.get(User, uuid.UUID(user_id))
        if not u:
            return None

        # Prefer an ordered lookup to allow fallback if a default isn't set yet.
        default_row = (
            await db.execute(
                select(Address)
                .where(Address.user_id == u.id)
                .order_by(Address.is_default.desc(), Address.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        default_addr = _addr_dict(default_row) if default_row and default_row.is_default else None

        return {
            "id": str(u.id),
            "full_name": u.full_name,
            "phone": u.phone,
            "email": u.email,
            "default_address": default_addr,
        }


async def get_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    if not phone:
        return None
    async with Session() as db:
        row = (await db.execute(select(User).where(User.phone == phone.strip()))).scalar_one_or_none()
        if not row:
            return None
        return {"id": str(row.id), "full_name": row.full_name, "phone": row.phone, "email": row.email}


async def update_user(user_id: str, **fields) -> Dict[str, Any]:
    allowed = {"full_name", "phone", "email"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        raise ValueError("No valid fields to update")

    if "full_name" in data and data["full_name"]:
        data["full_name"] = str(data["full_name"]).strip()
    if "phone" in data and data["phone"]:
        data["phone"] = str(data["phone"]).strip()
    if "email" in data:
        data["email"] = _normalize_email(data.get("email"))

    async with Session() as db:
        try:
            await db.execute(
                update(User)
                .where(User.id == uuid.UUID(user_id))
                .values(**data)
            )
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise RuntimeError("Phone already exists") from e

    # return fresh copy
    out = await get_user(user_id)
    return out or {}


async def add_address(
    user_id: str,
    *,
    line1: str,
    line2: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
    label: Optional[str] = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    if not line1:
        raise ValueError("line1 is required")

    async with Session() as db:
        now = _utcnow()
        if is_default:
            # toggle others off in the same transaction
            await db.execute(
                update(Address)
                .where(Address.user_id == uuid.UUID(user_id), Address.is_default == True)
                .values(is_default=False, updated_at=now)
            )

        addr = Address(
            user_id=uuid.UUID(user_id),
            line1=line1,
            line2=line2,
            city=city,
            state=state,
            postal_code=postal_code,
            label=label,
            is_default=is_default,
            created_at=now,
            updated_at=now,
        )
        db.add(addr)
        await db.commit()
        await db.refresh(addr)
        return _addr_dict(addr)


async def set_default_address(user_id: str, address_id: str) -> Dict[str, Any]:
    async with Session() as db:
        # Ensure the address belongs to the user
        addr = await db.get(Address, uuid.UUID(address_id))
        if not addr or str(addr.user_id) != user_id:
            raise RuntimeError("Address not found for this user")

        now = _utcnow()
        # Turn off previous default
        await db.execute(
            update(Address)
            .where(Address.user_id == uuid.UUID(user_id), Address.is_default == True, Address.id != addr.id)
            .values(is_default=False, updated_at=now)
        )
        # Set new default
        await db.execute(
            update(Address)
            .where(Address.id == addr.id)
            .values(is_default=True, updated_at=now)
        )
        await db.commit()

        # Return the new default
        addr.is_default = True
        addr.updated_at = now
        return _addr_dict(addr)


async def list_addresses(user_id: str) -> List[Dict[str, Any]]:
    async with Session() as db:
        rows = (
            await db.execute(
                select(Address)
                .where(Address.user_id == uuid.UUID(user_id))
                .order_by(Address.is_default.desc(), Address.created_at.desc())
            )
        ).scalars().all()
        return [_addr_dict(a) for a in rows]


async def update_address(address_id: str, **fields) -> Dict[str, Any]:
    allowed = {"line1", "line2", "city", "state", "postal_code", "label", "is_default"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        raise ValueError("No valid fields to update")

    async with Session() as db:
        addr = await db.get(Address, uuid.UUID(address_id))
        if not addr:
            raise RuntimeError("Address not found")

        now = _utcnow()

        # If setting this address as default, unset others for the same user first
        if data.get("is_default") is True:
            await db.execute(
                update(Address)
                .where(Address.user_id == addr.user_id, Address.is_default == True, Address.id != addr.id)
                .values(is_default=False, updated_at=now)
            )

        data["updated_at"] = now
        await db.execute(update(Address).where(Address.id == addr.id).values(**data))
        await db.commit()

        # Refresh & return
        await db.refresh(addr)
        return _addr_dict(addr)


async def delete_address(address_id: str) -> Dict[str, Any]:
    async with Session() as db:
        addr = await db.get(Address, uuid.UUID(address_id))
        if not addr:
            raise RuntimeError("Address not found")
        await db.execute(delete(Address).where(Address.id == addr.id))
        await db.commit()
        return {"ok": True}


async def get_default_address(user_id: str) -> Optional[Dict[str, Any]]:
    async with Session() as db:
        row = (
            await db.execute(
                select(Address)
                .where(Address.user_id == uuid.UUID(user_id))
                .order_by(Address.is_default.desc(), Address.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _addr_dict(row) if (row and row.is_default) else None
