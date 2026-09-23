from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine
from sqlalchemy.orm import DeclarativeBase,sessionmaker
from app.core.config import get_settings

settings=get_settings()

engine=create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=1800,
    echo=False,
)

SessionLocal=async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False
    )

class Base(DeclarativeBase):
    pass