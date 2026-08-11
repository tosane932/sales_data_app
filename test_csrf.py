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


def _assert_form_contains_csrf_token(response):
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    assert document.select_one('form input[name="csrf_token"]') is not None


@pytest.fixture()
def csrf_write_records(flask_app):
    sale_date = datetime.date.today()
    product_a = Product(
        year=sale_date.year,
        month=sale_date.month,
        name="商品A",
        price=100,
    )
    product_b = Product(
        year=sale_date.year,
        month=sale_date.month,
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


def test_login_form_contains_csrf_token(client):
    response = client.get("/login")

    _assert_form_contains_csrf_token(response)


def test_product_form_contains_csrf_token(authenticated_client):
    response = authenticated_client.get("/")

    _assert_form_contains_csrf_token(response)


def test_sales_form_contains_csrf_token(
    authenticated_client,
    csrf_write_records,
):
    response = authenticated_client.get("/input")

    _assert_form_contains_csrf_token(response)


def test_login_post_without_csrf_is_rejected_without_authentication(
    client,
    csrf_write_records,
    admin_auth_config,
    csrf_token,
):
    sales_before = _sales_snapshot()

    login_response = client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": admin_auth_config.password,
        },
        follow_redirects=False,
    )

    sales_payload = {
        "date": csrf_write_records.sale_date.isoformat(),
        "product_id": [str(csrf_write_records.product_a_id)],
        "quantity": ["9"],
        "csrf_token": csrf_token(client, "/login"),
    }

    protected_response = client.post(
        "/input",
        data=sales_payload,
        follow_redirects=False,
    )

    location = protected_response.headers.get("Location")
    redirects_to_login = (
        protected_response.status_code == 302
        and location is not None
        and urlparse(location).path == "/login"
    )
    sales_after = _sales_snapshot()
    assert (
        login_response.status_code == 400
        and redirects_to_login
        and sales_after == sales_before
    ), (
        f"login_status={login_response.status_code}, "
        f"protected_status={protected_response.status_code}, "
        f"redirects_to_login={redirects_to_login}, "
        f"sales_unchanged={sales_after == sales_before}"
    )


def test_authenticated_product_post_without_csrf_is_rejected_without_database_changes(
    authenticated_client,
    csrf_write_records,
):
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = authenticated_client.post(
        "/",
        data={
            "year": str(csrf_write_records.sale_date.year),
            "month": str(csrf_write_records.sale_date.month),
            "product_id": [str(csrf_write_records.product_a_id), ""],
            "prod_name": ["更新商品A", "新規商品C"],
            "prod_price": ["150", "300"],
        },
    )

    products_after = _product_snapshot()
    sales_after = _sales_snapshot()
    assert (
        response.status_code == 400
        and products_after == products_before
        and sales_after == sales_before
    ), (
        f"status={response.status_code}, "
        f"products_unchanged={products_after == products_before}, "
        f"sales_unchanged={sales_after == sales_before}"
    )


def test_authenticated_sales_post_without_csrf_is_rejected_without_database_changes(
    authenticated_client,
    csrf_write_records,
):
    sales_before = _sales_snapshot()

    response = authenticated_client.post(
        "/input",
        data={
            "date": csrf_write_records.sale_date.isoformat(),
            "product_id": [
                str(csrf_write_records.product_a_id),
                str(csrf_write_records.product_b_id),
            ],
            "quantity": ["9", "7"],
        },
    )

    sales_after = _sales_snapshot()
    assert (
        response.status_code == 400
        and sales_after == sales_before
    ), (
        f"status={response.status_code}, "
        f"sales_unchanged={sales_after == sales_before}"
    )
