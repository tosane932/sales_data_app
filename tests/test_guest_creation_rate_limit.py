import datetime
from unittest.mock import Mock

import pytest
from flask import g
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import ServiceUnavailable, TooManyRequests

import app as app_module
import models as models_module
from models import DailySales, Dataset, Product, db


TEST_LIMIT = 2
TEST_WINDOW_SECONDS = 60
TEST_IP = "192.0.2.10"


def _configure_rate_limit(flask_app, *, limit=TEST_LIMIT):
    flask_app.config.update(
        GUEST_CREATION_RATE_LIMIT_MAX_ATTEMPTS=limit,
        GUEST_CREATION_RATE_LIMIT_WINDOW_SECONDS=TEST_WINDOW_SECONDS,
    )


def _client_key(flask_app, ip_address, *, headers=None):
    with flask_app.test_request_context(
        "/",
        headers=headers,
        environ_base={"REMOTE_ADDR": ip_address},
    ):
        return app_module._get_guest_creation_client_key()


def _start_guest(flask_app, ip_address=TEST_IP):
    g.pop("_login_user", None)
    with flask_app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": ip_address},
    ):
        return app_module.start_guest_session()


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


def _create_product_and_sale(dataset):
    product = Product(
        dataset=dataset,
        year=2026,
        month=9,
        name="rate limit保護確認商品",
        price=410,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()
    sale = DailySales(
        product_id=product.id,
        date=datetime.date(2026, 9, 3),
        quantity=14,
    )
    db.session.add(sale)
    db.session.commit()
    return product, sale


def test_same_client_ip_generates_same_hmac_key(flask_app):
    first = _client_key(flask_app, "192.0.2.10")
    second = _client_key(flask_app, "192.0.2.10")

    assert first == second
    assert len(first) == 64


def test_different_client_ips_generate_different_hmac_keys(flask_app):
    first = _client_key(flask_app, "192.0.2.10")
    second = _client_key(flask_app, "192.0.2.11")

    assert first != second


def test_database_stores_hmac_key_without_raw_ip(flask_app):
    _configure_rate_limit(flask_app)

    _start_guest(flask_app)

    rate_limit_model = models_module.GuestCreationRateLimit
    row = rate_limit_model.query.one()
    assert row.client_key_hash != TEST_IP
    assert TEST_IP not in row.client_key_hash
    assert len(row.client_key_hash) == 64


@pytest.mark.parametrize("header_value", [None, "not-an-ip"])
def test_production_client_ip_rejects_missing_or_invalid_cf_header(
    flask_app,
    monkeypatch,
    header_value,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    headers = {"X-Forwarded-For": "198.51.100.99"}
    if header_value is not None:
        headers["CF-Connecting-IP"] = header_value

    with flask_app.test_request_context(
        "/",
        headers=headers,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        with pytest.raises(ServiceUnavailable):
            app_module._get_guest_creation_client_key()


def test_x_forwarded_for_cannot_change_production_client_key(
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    first = _client_key(
        flask_app,
        "127.0.0.1",
        headers={
            "CF-Connecting-IP": "203.0.113.20",
            "X-Forwarded-For": "198.51.100.10",
        },
    )
    second = _client_key(
        flask_app,
        "127.0.0.1",
        headers={
            "CF-Connecting-IP": "203.0.113.20",
            "X-Forwarded-For": "198.51.100.200",
        },
    )

    assert first == second


def test_guest_creation_proceeds_within_rate_limit(flask_app):
    _configure_rate_limit(flask_app)

    first = _start_guest(flask_app)
    second = _start_guest(flask_app)

    assert first.id != second.id
    assert Dataset.query.filter_by(kind="guest").count() == TEST_LIMIT


def test_rate_limit_rejection_creates_no_new_guest_dataset(flask_app):
    _configure_rate_limit(flask_app, limit=1)
    _start_guest(flask_app)
    dataset_count_before = Dataset.query.count()

    with pytest.raises(TooManyRequests):
        _start_guest(flask_app)

    assert Dataset.query.count() == dataset_count_before


def test_discarding_cookie_does_not_reset_same_client_rate_limit(flask_app):
    _configure_rate_limit(flask_app, limit=1)

    _start_guest(flask_app)
    g.pop("_login_user", None)
    with flask_app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": TEST_IP},
    ):
        assert "_user_id" not in app_module.session
        with pytest.raises(TooManyRequests):
            app_module.start_guest_session()


def test_new_time_window_allows_guest_creation_again(flask_app):
    _configure_rate_limit(flask_app, limit=1)
    client_key = _client_key(flask_app, TEST_IP)
    first_window = datetime.datetime(
        2026,
        9,
        3,
        12,
        0,
        tzinfo=datetime.timezone.utc,
    )

    assert app_module._reserve_guest_creation_attempt(
        client_key,
        now=first_window,
    ) is True
    assert app_module._reserve_guest_creation_attempt(
        client_key,
        now=first_window + datetime.timedelta(seconds=30),
    ) is False
    assert app_module._reserve_guest_creation_attempt(
        client_key,
        now=first_window + datetime.timedelta(seconds=60),
    ) is True


def test_rate_limit_reservation_uses_single_atomic_upsert(
    flask_app,
    monkeypatch,
):
    _configure_rate_limit(flask_app)
    client_key = _client_key(flask_app, TEST_IP)
    executed_rate_statements = []
    real_execute = db.session.execute

    def record_rate_limit_statement(statement, *args, **kwargs):
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) == "guest_creation_rate_limits":
            executed_rate_statements.append(statement)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(
        db.session,
        "execute",
        record_rate_limit_statement,
    )

    assert app_module._reserve_guest_creation_attempt(client_key) is True

    assert len(executed_rate_statements) == 1
    sql_text = str(
        executed_rate_statements[0].compile(dialect=db.engine.dialect)
    ).upper()
    assert sql_text.startswith("INSERT INTO GUEST_CREATION_RATE_LIMITS")
    assert "ON CONFLICT" in sql_text
    assert "DO UPDATE SET" in sql_text
    assert "GUEST_CREATION_RATE_LIMITS.REQUEST_COUNT <" in sql_text


def test_rate_limit_rejection_does_not_call_login_user(
    flask_app,
    monkeypatch,
):
    _configure_rate_limit(flask_app, limit=1)
    _start_guest(flask_app)
    login = Mock()
    cleanup = Mock()
    monkeypatch.setattr(app_module, "login_user", login)
    monkeypatch.setattr(
        app_module,
        "_cleanup_expired_guest_datasets",
        cleanup,
    )

    with pytest.raises(TooManyRequests):
        _start_guest(flask_app)

    login.assert_not_called()
    cleanup.assert_not_called()


def test_rate_limit_rejection_keeps_admin_and_existing_guest_unchanged(
    flask_app,
    admin_dataset,
):
    _configure_rate_limit(flask_app, limit=1)
    existing_guest = _create_guest_dataset(ai_usage_count=2)
    _start_guest(flask_app)
    admin_snapshot = (
        admin_dataset.id,
        admin_dataset.kind,
        admin_dataset.system_key,
        admin_dataset.guest_ai_usage_count,
    )
    guest_snapshot = (
        existing_guest.id,
        existing_guest.last_activity_at,
        existing_guest.absolute_expires_at,
        existing_guest.guest_ai_usage_count,
    )

    with pytest.raises(TooManyRequests):
        _start_guest(flask_app)

    db.session.refresh(admin_dataset)
    db.session.refresh(existing_guest)
    assert (
        admin_dataset.id,
        admin_dataset.kind,
        admin_dataset.system_key,
        admin_dataset.guest_ai_usage_count,
    ) == admin_snapshot
    assert (
        existing_guest.id,
        existing_guest.last_activity_at,
        existing_guest.absolute_expires_at,
        existing_guest.guest_ai_usage_count,
    ) == guest_snapshot


def test_rate_limit_rejection_keeps_products_and_sales_unchanged(
    flask_app,
):
    _configure_rate_limit(flask_app, limit=1)
    existing_guest = _create_guest_dataset()
    product, sale = _create_product_and_sale(existing_guest)
    _start_guest(flask_app)
    product_snapshot = (
        product.dataset_id,
        product.name,
        product.price,
        product.is_active,
    )
    sale_snapshot = (sale.product_id, sale.date, sale.quantity)
    product_count_before = Product.query.count()
    sales_count_before = DailySales.query.count()

    with pytest.raises(TooManyRequests):
        _start_guest(flask_app)

    db.session.refresh(product)
    db.session.refresh(sale)
    assert Product.query.count() == product_count_before
    assert DailySales.query.count() == sales_count_before
    assert (
        product.dataset_id,
        product.name,
        product.price,
        product.is_active,
    ) == product_snapshot
    assert (sale.product_id, sale.date, sale.quantity) == sale_snapshot


def test_rate_limit_rejection_keeps_existing_guest_ai_count(flask_app):
    _configure_rate_limit(flask_app, limit=1)
    existing_guest = _create_guest_dataset(ai_usage_count=3)
    _start_guest(flask_app)

    with pytest.raises(TooManyRequests):
        _start_guest(flask_app)

    db.session.refresh(existing_guest)
    assert existing_guest.guest_ai_usage_count == 3


def test_rate_limit_database_failure_is_fail_closed_and_preserves_data(
    flask_app,
    admin_dataset,
    monkeypatch,
):
    _configure_rate_limit(flask_app)
    guest_a = _create_guest_dataset(ai_usage_count=1)
    guest_b = _create_guest_dataset(ai_usage_count=2)
    product, sale = _create_product_and_sale(guest_a)
    dataset_count_before = Dataset.query.count()
    product_snapshot = (product.id, product.dataset_id, product.name)
    sale_snapshot = (sale.id, sale.product_id, sale.quantity)
    login = Mock()
    real_execute = db.session.execute

    def fail_rate_limit_statement(statement, *args, **kwargs):
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) == "guest_creation_rate_limits":
            raise SQLAlchemyError("test rate limit DB failure")
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "execute", fail_rate_limit_statement)
    monkeypatch.setattr(app_module, "login_user", login)

    with pytest.raises(ServiceUnavailable):
        _start_guest(flask_app)

    assert Dataset.query.count() == dataset_count_before
    assert db.session.get(Dataset, admin_dataset.id) is not None
    assert db.session.get(Dataset, guest_a.id).guest_ai_usage_count == 1
    assert db.session.get(Dataset, guest_b.id).guest_ai_usage_count == 2
    assert (
        product.id,
        product.dataset_id,
        product.name,
    ) == product_snapshot
    assert (sale.id, sale.product_id, sale.quantity) == sale_snapshot
    login.assert_not_called()


def test_guest_creation_failure_does_not_refund_rate_limit_attempt(
    flask_app,
    monkeypatch,
):
    _configure_rate_limit(flask_app, limit=1)
    real_commit = db.session.commit
    commit_count = 0
    login = Mock()

    def fail_guest_dataset_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise SQLAlchemyError("test Guest Dataset commit failure")
        return real_commit()

    monkeypatch.setattr(db.session, "commit", fail_guest_dataset_commit)
    monkeypatch.setattr(app_module, "login_user", login)

    with pytest.raises(ServiceUnavailable):
        _start_guest(flask_app)

    rate_limit_model = models_module.GuestCreationRateLimit
    assert rate_limit_model.query.one().request_count == 1
    assert Dataset.query.filter_by(kind="guest").count() == 0
    login.assert_not_called()


def test_guest_cleanup_does_not_delete_rate_limit_history(flask_app):
    _configure_rate_limit(flask_app)
    guest_dataset = _start_guest(flask_app)
    rate_limit_model = models_module.GuestCreationRateLimit
    client_key_hash = rate_limit_model.query.one().client_key_hash
    now = datetime.datetime.now(datetime.timezone.utc)
    guest_dataset.created_at = now - datetime.timedelta(hours=4)
    guest_dataset.last_activity_at = now - datetime.timedelta(hours=3)
    guest_dataset.absolute_expires_at = now - datetime.timedelta(hours=1)
    guest_dataset_id = guest_dataset.id
    db.session.commit()

    deleted_count = app_module._cleanup_expired_guest_datasets(now=now)
    db.session.commit()

    assert deleted_count == 1
    assert db.session.get(Dataset, guest_dataset_id) is None
    assert db.session.get(rate_limit_model, client_key_hash) is not None
