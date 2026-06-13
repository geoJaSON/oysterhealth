"""Async SQLAlchemy engine + session factory.

KVM8 has 8 vCPUs, so pool_size of 16 (= 2 × vCPU) with 20 overflow is the
starting point from Section 8.4 of the plan. Tune from real load.
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from settings import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=16,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
