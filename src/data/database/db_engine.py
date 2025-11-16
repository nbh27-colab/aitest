from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.future import select

from config.settings import PostgreSQLSettings

settings = PostgreSQLSettings()

posgress_user = quote_plus(settings.POSTGRES_USER)
posgress_password = quote_plus(settings.POSTGRES_PASSWORD)

DATABASE_URL = (
    f"postgresql+asyncpg://{posgress_user}:{posgress_password}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)

# Glovbal engine 
engine = create_engine(
    DATABASE_URL.replace("asyncpg", "psycopg2"),
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Async engine
DATABASE_URL_ASYNC = DATABASE_URL.replace("psycopg2", "asyncpg")

async_engine = create_async_engine(
    DATABASE_URL_ASYNC,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    pool_pre_ping=True
)

AsyncSessionLocal = sessionmaker(
    bind=async_engine, 
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)