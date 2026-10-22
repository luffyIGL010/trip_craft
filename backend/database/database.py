from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os

def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


DATABASE_URL = _normalize_database_url(
    os.getenv("DATABASE_URL", "sqlite:///./travelagent.db")
)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_travel_preference_budget_basis(bind=engine):
    """Add the preference-basis column without altering existing preference values."""
    inspector = inspect(bind)
    if not inspector.has_table("travel_preferences"):
        return

    columns = {column["name"] for column in inspector.get_columns("travel_preferences")}
    if "budget_basis" not in columns:
        with bind.begin() as connection:
            connection.execute(text(
                "ALTER TABLE travel_preferences "
                "ADD COLUMN budget_basis VARCHAR NOT NULL DEFAULT 'unspecified'"
            ))
