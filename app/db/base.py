from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase,sessionmaker
from app.core.config import get_settings

settings=get_settings()

engine=create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    echo=False,
    future=True
)

SessionLocal=sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False
    )

class Base(DeclarativeBase):
    pass