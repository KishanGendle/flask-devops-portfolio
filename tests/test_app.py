
import sys
from pathlib import Path

# Add project root to Python import path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as test_client:
        yield test_client


def test_homepage_loads(client):
    response = client.get("/")
    assert response.status_code == 200


def test_app_handles_unknown_page(client):
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404