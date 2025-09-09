# db/session.py
from __future__ import annotations

import os
import pathlib
from typing import Iterable, Any

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import ssl as _ssl
from sqlalchemy.pool import NullPool


def _load_env_files(candidates: Iterable[str]) -> None:
    """
    Load env files from both CWD (/app) and project root (relative to this file),
    without overriding values already provided by the platform.
    """
    here = pathlib.Path(__file__).resolve()
    roots = {
        pathlib.Path.cwd(),                 # /app at runtime
        here.parent.parent,                 # project root (…/app)
    }
    for fname in candidates:
        for root in roots:
            p = root / fname
            if p.exists():
                load_dotenv(p, override=False)


# Load secrets if present (won't override variables already set by the platform)
_load_env_files(("cloud.secrets.env", ".env.local", "env.local", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
# Avoid printing secrets; just show if present (useful in `lk agent logs`)
print("DATABASE_URL set:", DATABASE_URL)

if not DATABASE_URL:
    # Raise here so callers fail fast with a clear message
    raise RuntimeError("DATABASE_URL is not set")


sslmode = os.getenv("DB_SSLMODE", "disable").lower()
ssl_arg: Any
if sslmode in ("disable", "off", "false", "0"):
    ssl_arg = False
elif sslmode in ("require",):
    ssl_arg = True  # encrypted, no verification
elif sslmode in ("verify-ca", "verify-full"):
    ctx = _ssl.create_default_context(cafile=os.getenv("DB_SSLROOTCERT"))
    if sslmode == "verify-full":
        ctx.check_hostname = True
    ssl_arg = ctx
else:
    ssl_arg = False  # sane fallback

DATABASE_URL = os.getenv("DATABASE_URL")  # must include sslmode=require
print("DATABASE_URL set:", DATABASE_URL)

# (Optional) harden against bad env like PGSSLMODE="true"
for var in ("PGSSLMODE", "PGSSLNEGOTIATION"):
    val = os.environ.get(var)
    if val and val.lower() not in ("disable","allow","prefer","require","verify-ca","verify-full"):
        os.environ.pop(var, None)
ssl_ctx = _ssl.create_default_context()   # encrypted

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    echo=bool(os.getenv("SQL_ECHO")),
    connect_args={
        "ssl": "require",
    }
)


Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def ping() -> bool:
    """Optional: simple connectivity check you can call at startup."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
