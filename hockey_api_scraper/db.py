import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return url

ENGINE = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE)

def db_ping() -> bool:
    try:
        with ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
