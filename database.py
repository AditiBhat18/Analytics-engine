
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Uses DATABASE_URL from the environment if set (e.g. on Render); otherwise
# falls back to the local Docker Compose Postgres instance. Local setup is
# completely unaffected by this — same behavior as before.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:supersecretpassword@localhost:5432/analytics_engine",
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()