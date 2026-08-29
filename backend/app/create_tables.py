from app.database_connect import engine
from app.database_tables import BaseTable

# test creating tables
def create_tables() -> None:
    BaseTable.metadata.create_all(bind=engine)
    print("Tables created successfully")


if __name__ == "__main__":
    create_tables()