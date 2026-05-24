from sqlalchemy.orm import DeclarativeBase
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from app.config import settings
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.exc import OperationalError

def _normalize_database_url(url: str) -> str:
    """Normalize DATABASE_URL for SQLAlchemy 2.0+ with asyncpg.

    Handles three incompatibilities:
    1. SQLAlchemy 2.0+ removed the ``postgres://`` dialect alias
    2. asyncpg requires ``postgresql+asyncpg://`` as the scheme
    3. asyncpg does not accept ``sslmode`` as a query parameter
       (it uses ``ssl`` instead, or omits it for local connections)
    """
    # Step 1: Fix the scheme
    if url.startswith("postgresql+asyncpg://"):
        pass  # already correct
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Step 2: Convert or remove sslmode (asyncpg doesn't accept it)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    if "sslmode" in params:
        sslmode_value = params.pop("sslmode")[0]
        # Map common sslmode values to asyncpg's ssl parameter
        # asyncpg: ssl=True (require), ssl=False (disable), ssl="prefer" (prefer)
        if sslmode_value in ("require", "verify-ca", "verify-full"):
            params["ssl"] = ["require"]
        elif sslmode_value == "prefer":
            params["ssl"] = ["prefer"]
        # "disable" or "allow" → just remove; asyncpg defaults to no SSL

    new_query = urlencode(params, doseq=True)
    url = urlunparse(parsed._replace(query=new_query))
    return url

engine = create_async_engine(
    _normalize_database_url(settings.database_url),
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def init_db(retries=5, delay=2):
    """Initialize database with retry logic for startup race conditions."""
    from app.db.models import Base  # noqa: F811

    for attempt in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("Database initialized successfully")
            return
        except OperationalError as e:
            if "Connection refused" in str(e) and attempt < retries - 1:
                wait = delay * (2 ** attempt)
                print(f"Database not ready (attempt {attempt+1}/{retries}), retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                raise
        except Exception as e:
            # Unexpected error – log and re-raise
            print(f"Unexpected error during DB init: {e}")
            raise

async def get_db() -> AsyncSession:
    """Dependency that provides a database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()