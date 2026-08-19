import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

import app as app_module
from models import DailySales, Product, db


def _redirects_to_login(response):
    location = response.headers.get("Location")
    return (
        response.status_code == 302
        and location is not None
        and urlparse(location).path == "/login"
    )


def _assert_redirects_to_login(response):
    assert _redirects_to_login(response), (
        f"status={response.status_code}, "
        f"location={response.headers.get('Location')}"
    )


@pytest.fixture()
def authorization_sales_record(flask_app, admin_dataset):
    sale_date = datetime.date.today()
    product = Product(
        dataset=admin_dataset,
        year=sale_date.year,
        month=sale_date.month,
        name="認可テスト商品",
        price=300,
    )
    db.session.add(product)
    db.session.flush()
    db.session.add(
        DailySales(
            product_id=product.id,
            date=sale_date,
            quantity=8,
        )
    )
    db.session.commit()


def test_login_page_remains_public(client):
    response = client.get("/login")

    assert response.status_code == 200


def test_anonymous_product_page_redirects_to_login(client):
    response = client.get("/")

    _assert_redirects_to_login(response)


def test_anonymous_sales_page_redirects_to_login(client):
    response = client.get("/input")

    _assert_redirects_to_login(response)


def test_anonymous_dashboard_redirects_to_login(client):
    response = client.get("/dashboard")

    _assert_redirects_to_login(response)


def test_anonymous_dashboard_api_redirects_to_login(client):
    response = client.get("/api/dashboard-data")

    _assert_redirects_to_login(response)


def test_anonymous_ai_advice_api_redirects_to_login(
    client,
    authorization_sales_record,
    monkeypatch,
):
    gemini_client = Mock()
    gemini_client.return_value.models.generate_content.return_value = (
        SimpleNamespace(text="モックAI返答")
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.setattr(app_module.genai, "Client", gemini_client)

    response = client.get("/api/ai-advice")

    assert _redirects_to_login(response) and not gemini_client.called, (
        f"status={response.status_code}, "
        f"location={response.headers.get('Location')}, "
        f"gemini_calls={gemini_client.call_count}"
    )


def test_anonymous_greeting_api_redirects_to_login(client, monkeypatch):
    gemini_client = Mock()
    gemini_client.return_value.models.generate_content.return_value = (
        SimpleNamespace(text="モック挨拶")
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.setattr(app_module.genai, "Client", gemini_client)

    response = client.get("/api/greeting")

    assert _redirects_to_login(response) and not gemini_client.called, (
        f"status={response.status_code}, "
        f"location={response.headers.get('Location')}, "
        f"gemini_calls={gemini_client.call_count}"
    )
