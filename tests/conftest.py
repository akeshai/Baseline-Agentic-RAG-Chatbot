import os
import pytest
import asyncio
from fastapi.testclient import TestClient

# Configure database URL to point to a temporary SQLite test database before importing modules
os.environ["DATABASE_URL"] = "sqlite:///./test_db.db"

from app.database import engine
from main import app

@pytest.fixture(name="client")
def fixture_client():
    """
    Provides a FastAPI TestClient configured for the test environment.
    Automatically deletes the test database before and after each test for full isolation.
    """
    # 1. Cleanup old test database if any exists
    if os.path.exists("test_db.db"):
        try:
            os.remove("test_db.db")
        except Exception:
            pass

    # 2. Enter context manager, which triggers FastAPI's lifespan (and thus async table creation)
    with TestClient(app) as test_client:
        yield test_client

    # 3. Teardown: close all connection pools asynchronously
    try:
        asyncio.get_running_loop()
        # If there's a running loop, schedule the disposal task
        asyncio.ensure_future(engine.dispose())
    except RuntimeError:
        # If no loop is running, run disposal synchronously to completion using asyncio.run
        asyncio.run(engine.dispose())

    # 4. Remove database file
    if os.path.exists("test_db.db"):
        try:
            os.remove("test_db.db")
        except Exception:
            pass

@pytest.fixture
def anyio_backend():
    """
    Defines backend runner for anyio async tests.
    """
    return "asyncio"
