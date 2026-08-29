import os

from sqlalchemy import create_engine
from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import URL


# Load environment variables from .env
load_dotenv()

# Construct the SQLAlchemy connection string to supabase DB
DATABASE_URL = URL.create(
    drivername="postgresql+psycopg", # uses psycopg3
    username=os.getenv("user"),
    password=os.getenv("password"),
    host=os.getenv("host"),
    port=int(os.getenv("port")),
    database=os.getenv("dbname"),
    query={"sslmode": "require"},
)
# Create the SQLAlchemy engine
engine = create_engine(DATABASE_URL)

# Instantiate SessionLocal session factory, creates sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Test the connection
try:
    with engine.connect() as connection:
        print("Connection successful!")
except Exception as e:
    print(f"Failed to connect: {e}")