# plumber-ai-agent/tests/conftest.py
import os
import sys
from pathlib import Path
from sqlalchemy.pool import NullPool

# --- Part 1: Path Setup ---
# This must be at the very top to ensure the rest of the script
# can find your application's modules (like the 'db' package).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --- Part 2: Environment Loading ---
# This MUST happen before any of your application modules are imported,
# as they may depend on environment variables being present when the file is loaded.
from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")


# --- Part 3: Application Imports ---
# Now that the path and environment are set, it is safe to import from your application.
# We import here to make `Base` and `DATABASE_URL` available to the fixtures below.
#
# IMPORTANT: This file assumes you have followed the advice to REMOVE the global
# `engine` and `Session` creation from your `db/models.py` file, leaving only
# the model definitions and the `DATABASE_URL` variable.
from db.models import Base


# --- Part 4: Test Library Imports & Pre-run Checks ---
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = (
    os.getenv("DATABASE_URL_TEST")  # prefer a dedicated test URL if you have one
    or os.getenv("DATABASE_URL")
)
# Optional: Fail fast if a critical API key is missing.
if not os.getenv("OPENAI_API_KEY"):
    pytest.skip(
        "OPENAI_API_KEY is not set (loaded from .env/.env.local)",
        allow_module_level=True,
    )


# --- Part 5: Core Test Fixtures ---

@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncSession:
    """
    Provides a clean, isolated database session for each test function.

    This fixture handles the entire test database lifecycle:
    1.  Creates a new async engine within the test's asyncio event loop.
    2.  Fails the test if DATABASE_URL is not configured.
    3.  Drops all tables to ensure a clean state before the test runs.
    4.  Creates all tables from your SQLAlchemy models' metadata.
    5.  Yields a single `AsyncSession` for the test to use.
    6.  Disposes of the engine and its connections after the test is complete.
    """
    if not DATABASE_URL:
        pytest.fail("DATABASE_URL environment variable is not set or was not loaded correctly.")

    # Create an engine specifically for this test function.
    # This is the key to solving the "attached to a different loop" runtime error.
    # engine = create_async_engine(
    #     DATABASE_URL,
    #     connect_args={"ssl": "require"},  # For SSL-required databases like NeonDB
    #     # connect_args={"ssl": False},
    #     echo=False,  # Set to True to see all SQL queries in the test output
    # )
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={
            "ssl": "require",
            # critical: avoid stale prepared statements / type OIDs
            "statement_cache_size": 0,        # asyncpg setting (disables stmt cache)
        },
        poolclass=NullPool,                   # optional but helpful: no pooled reuse
        pool_pre_ping=True,                   # optional safety
    )

    # Set up the schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Create a sessionmaker and yield a session to the test
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as session:
        yield session

    # Teardown: close all connections and dispose of the engine
    await engine.dispose()