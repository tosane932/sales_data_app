import datetime
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup

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
):
    login_response = client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": admin_auth_config.password,
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
        },
        follow_redirects=False,
    )

    assert protected_response.status_code == 200


def test_invalid_admin_login_does_not_authenticate(
    client,
    unauthenticated_write_records,
    admin_auth_config,
):
    sales_before = _sales_snapshot()

    login_response = client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": "wrong-password",
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
        },
        follow_redirects=False,
    )

    _assert_redirects_to_login(protected_response)
    assert _sales_snapshot() == sales_before


def test_unauthenticated_product_post_redirects_to_login_without_database_changes(
    client,
    unauthenticated_write_records,
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
        },
        follow_redirects=False,
    )

    _assert_redirects_to_login(response)
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_unauthenticated_sales_post_redirects_to_login_without_database_changes(
    client,
    unauthenticated_write_records,
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
        },
        follow_redirects=False,
    )

    _assert_redirects_to_login(response)
    assert _sales_snapshot() == sales_before
