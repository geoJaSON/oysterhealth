from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/health/db")
async def health_db(session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text("SELECT postgis_full_version()"))).first()
    return {"status": "ok", "postgis": row[0] if row else None}
