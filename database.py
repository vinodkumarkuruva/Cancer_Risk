from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import logging
import os
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



DATABASE_URL = os.getenv("DATABASE_URL")
# DATABASE_URL = "postgresql://postgres:Postgres@localhost:5432/cancer_db"

try:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        logger.info("Database connection successful!")
except Exception as e:
    logger.error(f"Database connection failed: {e}")
    raise

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()