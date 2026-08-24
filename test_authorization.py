import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
from flask_login import UserMixin

import app as app_module
from models import DailySales, Product, db


ADMIN_ROUTE_PATHS = [
    "/",
    "/input",
    "/dashboard",
    "/api/dashboard-data",
    "/api/ai-advice",
    "/api/greeting",
]


class _AuthenticatedNonAdmin(UserMixin):
    id = "test-authenticated-non-admin"


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


@pytest.fixture()
def authenticated_non_admin_client(flask_app, monkeypatch):
    original_user_loader = app_module.load_user

    def load_test_user(user_id):
        if user_id == _AuthenticatedNonAdmin.id:
            return _AuthenticatedNonAdmin()
        return original_user_loader(user_id)

    monkeypatch.setattr(
        app_module.login_manager,
        "_user_callback",
        load_test_user,
    )

    test_client = flask_app.test_client()
    with test_client.session_transaction() as session_data:
        session_data["_user_id"] = _AuthenticatedNonAdmin.id
        session_data["_fresh"] = True

    return test_client


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


@pytest.mark.parametrize("path", ADMIN_ROUTE_PATHS)
def test_authenticated_non_admin_is_forbidden_from_admin_route(
    authenticated_non_admin_client,
    path,
    monkeypatch,
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = authenticated_non_admin_client.get(path)

    assert response.status_code == 403


def test_authenticated_non_admin_ai_advice_is_rejected_before_generation(
    authenticated_non_admin_client,
    monkeypatch,
):
    generate_ai_advice = Mock(return_value="呼び出してはいけないAI返答")
    monkeypatch.setattr(
        app_module,
        "_generate_ai_advice",
        generate_ai_advice,
    )

    response = authenticated_non_admin_client.get("/api/ai-advice")

    assert response.status_code == 403
    generate_ai_advice.assert_not_called()


def test_authenticated_non_admin_greeting_is_rejected_before_gemini_call(
    authenticated_non_admin_client,
    monkeypatch,
):
    gemini_client = Mock()
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.setattr(app_module.genai, "Client", gemini_client)

    response = authenticated_non_admin_client.get("/api/greeting")

    assert response.status_code == 403
    gemini_client.assert_not_called()


@pytest.mark.parametrize("path", ADMIN_ROUTE_PATHS)
def test_admin_remains_allowed_to_access_admin_route(
    authenticated_client,
    admin_dataset,
    path,
    monkeypatch,
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = authenticated_client.get(path)

    assert response.status_code == 200
