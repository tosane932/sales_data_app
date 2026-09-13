import datetime
import uuid
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup
from flask import g

import app as app_module
from models import DailySales, Dataset, Product, db


PROTECTED_REQUESTS = [
    ("get", "/"),
    ("get", "/input"),
    ("get", "/dashboard"),
    ("get", "/api/dashboard-data"),
    ("post", "/api/ai-advice"),
    ("post", "/api/greeting"),
]


def _tamper_csrf_token(token):
    replacement = "A" if token[0] != "A" else "B"
    return replacement + token[1:]


def _assert_redirects_to_login(response):
    assert response.status_code == 302
    location = response.headers.get("Location")
    assert location is not None
    parsed_location = urlparse(location)
    assert parsed_location.path == "/login"
    assert parsed_location.netloc == ""


def _create_guest_dataset(*, ai_usage_count=0):
    now = datetime.datetime.now(datetime.timezone.utc)
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
        guest_ai_usage_count=ai_usage_count,
    )
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _create_product_and_sale(dataset, *, name, quantity):
    sale_date = app_module.business_today()
    product = Product(
        dataset=dataset,
        year=sale_date.year,
        month=sale_date.month,
        name=name,
        price=200,
    )
    db.session.add(product)
    db.session.flush()
    sale = DailySales(
        product_id=product.id,
        date=sale_date,
        quantity=quantity,
    )
    db.session.add(sale)
    db.session.commit()
    return product, sale


def _dataset_snapshot(dataset_id, *, include_activity=True):
    dataset = db.session.get(Dataset, dataset_id)
    assert dataset is not None
    snapshot = (
        dataset.id,
        dataset.kind,
        dataset.system_key,
        dataset.created_at,
        dataset.absolute_expires_at,
        dataset.guest_ai_usage_count,
    )
    if include_activity:
        snapshot += (dataset.last_activity_at,)
    return snapshot


def _product_snapshot():
    return [
        (
            product.id,
            product.dataset_id,
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


def _assert_auth_session_is_removed(test_client):
    with test_client.session_transaction() as session_data:
        assert "_user_id" not in session_data
        assert "_fresh" not in session_data
        assert "_id" not in session_data
        assert (
            app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY
            not in session_data
        )


def _assert_logout_form(response, expected_principal_label):
    assert response.status_code == 200
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    form = document.select_one('form[action="/logout"]')
    assert form is not None
    assert form.get("method", "get").lower() == "post"

    token_input = form.select_one('input[name="csrf_token"]')
    assert token_input is not None
    assert token_input.get("value")

    submit_button = form.select_one('button[type="submit"]')
    assert submit_button is not None
    assert "ログアウト" in submit_button.get_text(strip=True)
    assert expected_principal_label in document.get_text(" ", strip=True)


def test_admin_logout_clears_auth_session_and_preserves_business_data(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    _create_product_and_sale(
        admin_dataset,
        name="管理者の商品",
        quantity=4,
    )
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()
    token = csrf_token(authenticated_client, "/")

    with authenticated_client.session_transaction() as session_data:
        assert session_data.get("_user_id") == app_module.AdminUser.id
        assert app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY in session_data

    response = authenticated_client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    _assert_redirects_to_login(response)
    _assert_auth_session_is_removed(authenticated_client)
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_logout_blocks_all_protected_routes(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    token = csrf_token(authenticated_client, "/")
    logout_response = authenticated_client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    _assert_redirects_to_login(logout_response)

    for method, path in PROTECTED_REQUESTS:
        response = getattr(authenticated_client, method)(
            path,
            data={"csrf_token": token} if method == "post" else None,
            follow_redirects=False,
        )
        _assert_redirects_to_login(response)


def test_guest_logout_ends_identity_but_preserves_all_datasets_and_sales(
    flask_app,
    client,
    admin_dataset,
    csrf_token,
):
    admin_product, admin_sale = _create_product_and_sale(
        admin_dataset,
        name="管理者の商品",
        quantity=3,
    )
    guest_b_dataset = _create_guest_dataset(ai_usage_count=1)
    guest_b_product, guest_b_sale = _create_product_and_sale(
        guest_b_dataset,
        name="Guest Bの商品",
        quantity=5,
    )

    start_response = client.post(
        "/guest/start",
        data={"csrf_token": csrf_token(client, "/login")},
        follow_redirects=False,
    )
    assert start_response.status_code == 303

    with client.session_transaction() as session_data:
        guest_user_id = session_data.get("_user_id")
    assert isinstance(guest_user_id, str)
    assert guest_user_id.startswith("guest:")
    guest_a_dataset_id = uuid.UUID(guest_user_id.removeprefix("guest:"))
    guest_a_dataset = db.session.get(Dataset, guest_a_dataset_id)
    assert guest_a_dataset is not None
    guest_a_dataset.guest_ai_usage_count = 2
    guest_a_product, guest_a_sale = _create_product_and_sale(
        guest_a_dataset,
        name="Guest Aの商品",
        quantity=7,
    )

    g.pop("_login_user", None)
    token = csrf_token(client, "/")
    dataset_ids_before = {
        row[0] for row in db.session.query(Dataset.id).all()
    }
    guest_a_before = _dataset_snapshot(
        guest_a_dataset_id,
        include_activity=False,
    )
    guest_b_before = _dataset_snapshot(guest_b_dataset.id)
    admin_before = _dataset_snapshot(admin_dataset.id)
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    _assert_redirects_to_login(response)
    _assert_auth_session_is_removed(client)
    assert {row[0] for row in db.session.query(Dataset.id).all()} == (
        dataset_ids_before
    )
    assert _dataset_snapshot(
        guest_a_dataset_id,
        include_activity=False,
    ) == guest_a_before
    assert _dataset_snapshot(guest_b_dataset.id) == guest_b_before
    assert _dataset_snapshot(admin_dataset.id) == admin_before
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before
    assert db.session.get(Product, admin_product.id) is not None
    assert db.session.get(Product, guest_a_product.id) is not None
    assert db.session.get(Product, guest_b_product.id) is not None
    assert db.session.get(DailySales, admin_sale.id) is not None
    assert db.session.get(DailySales, guest_a_sale.id) is not None
    assert db.session.get(DailySales, guest_b_sale.id) is not None

    with client.session_transaction() as session_data:
        session_data["role"] = "guest"
        session_data["dataset_id"] = str(guest_a_dataset_id)
        session_data["is_admin"] = False
    g.pop("_login_user", None)

    _assert_redirects_to_login(client.get("/", follow_redirects=False))


@pytest.mark.parametrize("csrf_case", ["missing", "tampered"])
def test_csrf_rejection_does_not_log_admin_out(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
    csrf_case,
):
    valid_token = csrf_token(authenticated_client, "/")
    payload = {}
    if csrf_case == "tampered":
        payload["csrf_token"] = _tamper_csrf_token(valid_token)

    logout_user = Mock()
    monkeypatch.setattr(
        app_module,
        "logout_user",
        logout_user,
        raising=False,
    )

    response = authenticated_client.post(
        "/logout",
        data=payload,
        follow_redirects=False,
    )

    assert response.status_code == 400
    logout_user.assert_not_called()
    with authenticated_client.session_transaction() as session_data:
        assert session_data.get("_user_id") == app_module.AdminUser.id
        assert app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY in session_data

    g.pop("_login_user", None)
    assert authenticated_client.get("/").status_code == 200


def test_get_logout_is_rejected_without_logging_user_out(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/logout", follow_redirects=False)

    assert response.status_code == 405
    g.pop("_login_user", None)
    assert authenticated_client.get("/").status_code == 200


def test_second_logout_post_is_handled_as_unauthenticated(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    token = csrf_token(authenticated_client, "/")

    first_response = authenticated_client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    second_response = authenticated_client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    _assert_redirects_to_login(first_response)
    _assert_redirects_to_login(second_response)
    _assert_auth_session_is_removed(authenticated_client)


@pytest.mark.parametrize("path", ["/", "/input", "/dashboard"])
def test_admin_major_pages_offer_csrf_protected_logout(
    authenticated_client,
    admin_dataset,
    path,
):
    response = authenticated_client.get(path)

    _assert_logout_form(response, "管理者")


def test_guest_major_page_identifies_guest_logout(flask_app):
    guest_dataset = _create_guest_dataset()
    guest_client = flask_app.test_client()
    with guest_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_dataset.id}"
        session_data["_fresh"] = True
    g.pop("_login_user", None)

    response = guest_client.get("/")

    _assert_logout_form(response, "ゲストデモ")
