import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# --- Database Connection Setup for the Main Application ---

# 1. Get the database URL from the environment variables.
#    Your application should ensure .env files are loaded before this runs.
DATABASE_URL = os.getenv("DATABASE_URL")

print("DATABASE_URL", DATABASE_URL)
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set. The application cannot start.")

# 2. Create the single, reusable SQLAlchemy engine instance for the application.
#    The engine manages a pool of database connections.
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,                  # Recommended for serverless DBs like Neon
    echo=bool(os.getenv("SQL_ECHO")),    # Log SQL statements if SQL_ECHO is set
    connect_args={"ssl": "require"},     # Enforce SSL connection for security
)

# 3. Create a session factory ("Session" is a convention).
#    This object will create new `AsyncSession` instances for each unit of work
#    (for example, for each API request in a web application).
Session = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# This setup provides a `Session` factory that your application code (like services)
# can import to get a database session, while keeping your `db/models.py` file
# clean and focused only on data shape.