import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker


# Load environment variables from .env
load_dotenv()

# Build the URL from separate values so passwords containing characters such as
# @, :, or / do not need to be URL-encoded.
DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    host=os.environ["DB_HOST"],
    port=int(os.getenv("DB_PORT", "5432")),
    database=os.environ["DB_NAME"],
    query={"sslmode": "require"},
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Instantiate SessionLocal session factory, creates sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def test_connection() -> None:
    """Open a connection and run a lightweight query."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


if __name__ == "__main__":
    test_connection()
    print("Connection successful!")
