"""Verify that the configured PostgreSQL database is reachable.

Run from ``backend`` with::

    python -m tests.integration.test_database
"""

from app.database.database_connect import test_connection


def run_database_connection_test() -> None:
    """Execute a harmless SELECT statement against the configured database."""
    test_connection()
    print("Database connection integration test passed.")


if __name__ == "__main__":
    run_database_connection_test()
