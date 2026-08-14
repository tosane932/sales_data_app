import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup
from flask import g

import app as app_module
from models import DailySales, Product, db


def _product_snapshot():
    return [
        (
            product.id,
            product.year,
            product.month,
            product.name,
            product.price,
            product.is_active,
        )
        for product in Product.query.order_by(Product.id).all()
    ]


def _sales_snapshot():
    return [
        (sale.id, sale.product_id, sale.date, sale.quantity)
        for sale in DailySales.query.order_by(DailySales.id).all()
    ]


def _assert_redirects_to_login(response):
    assert response.status_code == 302
    location = response.headers.get("Location")
    assert location is not None
    assert urlparse(location).path == "/login"


@pytest.fixture()
def unauthenticated_write_records(flask_app):
    sale_date = datetime.date(2026, 8, 10)
    product_a = Product(
        year=2026,
        month=8,
        name="商品A",
        price=100,
    )
    product_b = Product(
        year=2026,
        month=8,
        name="商品B",
        price=200,
    )
    db.session.add_all([product_a, product_b])
    db.session.flush()
    db.session.add(
        DailySales(
            product_id=product_a.id,
            date=sale_date,
            quantity=5,
        )
    )
    db.session.commit()

    return SimpleNamespace(
        sale_date=sale_date,
        product_a_id=product_a.id,
        product_b_id=product_b.id,
    )


def test_login_page_is_available(client):
    response = client.get("/login")
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    assert document.select_one('input[name="username"]') is not None
    password_input = document.select_one('input[name="password"]')
    assert password_input is not None
    assert password_input.get("type") == "password"


def test_valid_admin_login_establishes_authenticated_session(
    client,
    unauthenticated_write_records,
    admin_auth_config,
    csrf_token,
):
    login_response = client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": admin_auth_config.password,
            "csrf_token": csrf_token(client, "/login"),
        },
        follow_redirects=False,
    )

    assert login_response.status_code == 302

    protected_response = client.post(
        "/input",
        data={
            "date": unauthenticated_write_records.sale_date.isoformat(),
            "product_id": [
                str(unauthenticated_write_records.product_a_id),
            ],
            "quantity": ["5"],
            "csrf_token": csrf_token(client, "/input"),
        },
        follow_redirects=False,
    )

    assert protected_response.status_code == 200


def test_existing_authenticated_session_is_rejected_when_admin_password_hash_is_invalid(
    authenticated_client,
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(
        flask_app.config,
        "ADMIN_PASSWORD_HASH",
        "invalid$test$hash",
    )
    user_loader = Mock(wraps=app_module.load_user)
    monkeypatch.setattr(
        app_module.login_manager,
        "_user_callback",
        user_loader,
    )
    g.pop("_login_user", None)

    response = authenticated_client.get(
        "/dashboard",
        follow_redirects=False,
    )

    user_loader.assert_called_once_with(app_module.AdminUser.id)
    _assert_redirects_to_login(response)


def test_existing_authenticated_session_is_rejected_when_auth_fingerprint_is_missing(
    authenticated_client,
    monkeypatch,
):
    with authenticated_client.session_transaction() as session_data:
        session_data.pop(
            app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY,
            None,
        )

    user_loader = Mock(wraps=app_module.load_user)
    monkeypatch.setattr(
        app_module.login_manager,
        "_user_callback",
        user_loader,
    )
    g.pop("_login_user", None)

    response = authenticated_client.get(
        "/dashboard",
        follow_redirects=False,
    )

    user_loader.assert_called_once_with(app_module.AdminUser.id)
    _assert_redirects_to_login(response)


def test_existing_authenticated_session_is_restored_when_admin_password_hash_is_unchanged(
    authenticated_client,
    monkeypatch,
):
    user_loader = Mock(wraps=app_module.load_user)
    monkeypatch.setattr(
        app_module.login_manager,
        "_user_callback",
        user_loader,
    )
    g.pop("_login_user", None)

    response = authenticated_client.get(
        "/dashboard",
        follow_redirects=False,
    )

    user_loader.assert_called_once_with(app_module.AdminUser.id)
    assert response.status_code == 200


def test_invalid_admin_login_does_not_authenticate(
    client,
    unauthenticated_write_records,
    admin_auth_config,
    csrf_token,
):
    sales_before = _sales_snapshot()

    login_response = client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": "wrong-password",
            "csrf_token": csrf_token(client, "/login"),
        },
        follow_redirects=False,
    )

    assert login_response.status_code == 401

    protected_response = client.post(
        "/input",
        data={
            "date": unauthenticated_write_records.sale_date.isoformat(),
            "product_id": [
                str(unauthenticated_write_records.product_a_id),
            ],
            "quantity": ["9"],
            "csrf_token": csrf_token(client, "/login"),
        },
        follow_redirects=False,
    )

    _assert_redirects_to_login(protected_response)
    assert _sales_snapshot() == sales_before


def test_unauthenticated_product_post_redirects_to_login_without_database_changes(
    client,
    unauthenticated_write_records,
    csrf_token,
):
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = client.post(
        "/",
        data={
            "year": "2026",
            "month": "8",
            "product_id": [
                str(unauthenticated_write_records.product_a_id),
                "",
            ],
            "prod_name": ["更新商品A", "新規商品C"],
            "prod_price": ["150", "300"],
            "csrf_token": csrf_token(client, "/login"),
        },
        follow_redirects=False,
    )

    _assert_redirects_to_login(response)
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_unauthenticated_sales_post_redirects_to_login_without_database_changes(
    client,
    unauthenticated_write_records,
    csrf_token,
):
    sales_before = _sales_snapshot()

    response = client.post(
        "/input",
        data={
            "date": unauthenticated_write_records.sale_date.isoformat(),
            "product_id": [
                str(unauthenticated_write_records.product_a_id),
                str(unauthenticated_write_records.product_b_id),
            ],
            "quantity": ["9", "7"],
            "csrf_token": csrf_token(client, "/login"),
        },
        follow_redirects=False,
    )

    _assert_redirects_to_login(response)
    assert _sales_snapshot() == sales_before
