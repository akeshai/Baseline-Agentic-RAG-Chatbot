from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.configs.dbs import settings as db_settings

# SQLite requires different connection arguments for multi-threading in FastAPI
connect_args = {}
if db_settings.async_database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# Initialize the SQLAlchemy async engine with explicit connection pool parameters
# We omit poolclass so that it automatically uses the asyncio-compatible AsyncAdaptedQueuePool
engine = create_async_engine(
    db_settings.async_database_url,
    connect_args=connect_args,
    pool_size=10,          # Keep up to 10 connections open in the pool
    max_overflow=20,       # Allow up to 20 extra temporary overflow connections
    pool_recycle=1800,     # Recycle connections every 30 minutes to prevent stale timeouts
    pool_timeout=30        # Wait up to 30 seconds to obtain a connection from the pool
)

# Create an async sessionmaker factory
SessionLocal = async_sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine, 
    class_=AsyncSession
)

# Declarative base class for models
class Base(DeclarativeBase):
    pass

# Async Dependency generator to obtain a DB session per request and return it to the pool
async def get_db():
    async with SessionLocal() as db:
        yield db
