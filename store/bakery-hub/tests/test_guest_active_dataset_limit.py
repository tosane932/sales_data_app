import datetime
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import ServiceUnavailable, TooManyRequests

import app as app_module
from models import DailySales, Dataset, GuestCreationRateLimit, Product, db


NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _create_guest_dataset(
    *,
    last_activity_at=None,
    absolute_expires_at=None,
    guest_ai_usage_count=0,
):
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=NOW - datetime.timedelta(hours=1),
        last_activity_at=(
            last_activity_at
            if last_activity_at is not None
            else NOW - datetime.timedelta(minutes=5)
        ),
        absolute_expires_at=(
            absolute_expires_at
            if absolute_expires_at is not None
            else NOW + datetime.timedelta(hours=1)
        ),
        guest_ai_usage_count=guest_ai_usage_count,
    )
    db.session.add(dataset)
    db.session.flush()
    return dataset


def _create_active_guest_datasets(count):
    return [_create_guest_dataset() for _ in range(count)]


def _create_product_and_sale(dataset, *, name, quantity):
    product = Product(
        dataset=dataset,
        year=2026,
        month=9,
        name=name,
        price=300,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()
    sale = DailySales(
        product_id=product.id,
        date=datetime.date(2026, 9, 7),
        quantity=quantity,
    )
    db.session.add(sale)
    db.session.flush()
    return product, sale


def test_active_guest_count_matches_existing_expiration_boundaries(
    flask_app,
    admin_dataset,
):
    active_guest = _create_guest_dataset()
    absolute_expired = _create_guest_dataset(
        absolute_expires_at=NOW - datetime.timedelta(seconds=1),
    )
    idle_expired = _create_guest_dataset(
        last_activity_at=NOW - datetime.timedelta(minutes=31),
    )
    absolute_boundary = _create_guest_dataset(
        absolute_expires_at=NOW,
    )
    idle_boundary = _create_guest_dataset(
        last_activity_at=NOW - datetime.timedelta(minutes=30),
    )
    db.session.commit()

    assert app_module._guest_dataset_is_expired(
        active_guest,
        now=NOW,
    ) is False
    for expired_guest in (
        absolute_expired,
        idle_expired,
        absolute_boundary,
        idle_boundary,
    ):
        assert app_module._guest_dataset_is_expired(
            expired_guest,
            now=NOW,
        ) is True

    assert app_module._get_active_guest_dataset_count(now=NOW) == 1
    assert admin_dataset.kind == "admin"


def test_guest_creation_below_limit_succeeds(flask_app):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 2
    _create_active_guest_datasets(1)
    db.session.commit()

    with flask_app.test_request_context("/"):
        created_guest = app_module.start_guest_session()

        assert current_user.get_id() == f"guest:{created_guest.id}"

    assert Dataset.query.filter_by(kind="guest").count() == 2


def test_ninth_of_ten_allows_tenth_guest(flask_app):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 10
    _create_active_guest_datasets(9)
    db.session.commit()

    with flask_app.test_request_context("/"):
        app_module.start_guest_session()

    assert Dataset.query.filter_by(kind="guest").count() == 10


def test_tenth_of_ten_rejects_eleventh_without_login(flask_app, monkeypatch):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 10
    existing_guests = _create_active_guest_datasets(10)
    existing_ids = {dataset.id for dataset in existing_guests}
    db.session.commit()
    login = Mock()
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

        assert current_user.is_authenticated is False

    assert {
        dataset.id
        for dataset in Dataset.query.filter_by(kind="guest").all()
    } == existing_ids
    login.assert_not_called()


@pytest.mark.parametrize(
    ("last_activity_at", "absolute_expires_at"),
    [
        (
            NOW - datetime.timedelta(minutes=5),
            NOW - datetime.timedelta(seconds=1),
        ),
        (
            NOW - datetime.timedelta(minutes=31),
            NOW + datetime.timedelta(hours=1),
        ),
        (
            NOW - datetime.timedelta(minutes=5),
            NOW,
        ),
        (
            NOW - datetime.timedelta(minutes=30),
            NOW + datetime.timedelta(hours=1),
        ),
    ],
    ids=[
        "absolute-expired",
        "idle-expired",
        "absolute-boundary",
        "idle-boundary",
    ],
)
def test_expired_guest_does_not_consume_capacity(
    flask_app,
    last_activity_at,
    absolute_expires_at,
):
    _create_guest_dataset(
        last_activity_at=last_activity_at,
        absolute_expires_at=absolute_expires_at,
    )
    db.session.commit()

    assert app_module._get_active_guest_dataset_count(now=NOW) == 0


def test_admin_dataset_does_not_consume_capacity(flask_app, admin_dataset):
    assert app_module._get_active_guest_dataset_count(now=NOW) == 0
    assert admin_dataset.kind == "admin"


def test_cleanup_frees_capacity_for_new_guest(flask_app):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 1
    expired_guest = _create_guest_dataset(
        absolute_expires_at=(
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=1)
        ),
    )
    expired_guest_id = expired_guest.id
    db.session.commit()

    with flask_app.test_request_context("/"):
        created_guest = app_module.start_guest_session()
        created_guest_id = created_guest.id

    assert db.session.get(Dataset, expired_guest_id) is None
    assert db.session.get(Dataset, created_guest_id) is not None
    assert Dataset.query.filter_by(kind="guest").count() == 1


def test_full_capacity_commits_cleanup_but_rejects_new_guest(
    flask_app,
    monkeypatch,
):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 1
    active_guest = _create_guest_dataset()
    expired_guest = _create_guest_dataset(
        absolute_expires_at=(
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=1)
        ),
    )
    active_guest_id = active_guest.id
    expired_guest_id = expired_guest.id
    db.session.commit()
    login = Mock()
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

    assert db.session.get(Dataset, expired_guest_id) is None
    assert db.session.get(Dataset, active_guest_id) is not None
    assert Dataset.query.filter_by(kind="guest").count() == 1
    login.assert_not_called()


def test_full_capacity_still_consumes_rate_limit_attempt(flask_app):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 1
    _create_active_guest_datasets(1)
    db.session.commit()

    with flask_app.test_request_context("/"):
        client_key = app_module._get_guest_creation_client_key()

        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

    saved_attempt = db.session.get(GuestCreationRateLimit, client_key)
    assert saved_attempt is not None
    assert saved_attempt.request_count == 1


def test_full_capacity_preserves_admin_guest_products_sales_and_ai_usage(
    flask_app,
    admin_dataset,
    monkeypatch,
):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = 1
    active_guest = _create_guest_dataset(guest_ai_usage_count=2)
    admin_product, admin_sale = _create_product_and_sale(
        admin_dataset,
        name="保護対象Admin商品",
        quantity=11,
    )
    guest_product, guest_sale = _create_product_and_sale(
        active_guest,
        name="保護対象Guest商品",
        quantity=22,
    )
    existing_rate_limit = GuestCreationRateLimit(
        client_key_hash="a" * 64,
        window_started_at=NOW,
        request_count=2,
        updated_at=NOW,
    )
    db.session.add(existing_rate_limit)
    db.session.commit()
    snapshots = {
        "admin": (
            admin_dataset.id,
            admin_dataset.last_activity_at,
            admin_dataset.guest_ai_usage_count,
        ),
        "guest": (
            active_guest.id,
            active_guest.last_activity_at,
            active_guest.guest_ai_usage_count,
        ),
        "admin_product": (admin_product.id, admin_product.name),
        "admin_sale": (admin_sale.id, admin_sale.quantity),
        "guest_product": (guest_product.id, guest_product.name),
        "guest_sale": (guest_sale.id, guest_sale.quantity),
        "rate": (
            existing_rate_limit.window_started_at,
            existing_rate_limit.request_count,
            existing_rate_limit.updated_at,
        ),
    }
    login = Mock()
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

    db.session.expire_all()
    saved_admin = db.session.get(Dataset, admin_dataset.id)
    saved_guest = db.session.get(Dataset, active_guest.id)
    saved_rate = db.session.get(GuestCreationRateLimit, "a" * 64)
    assert (
        saved_admin.id,
        saved_admin.last_activity_at,
        saved_admin.guest_ai_usage_count,
    ) == snapshots["admin"]
    assert (
        saved_guest.id,
        saved_guest.last_activity_at,
        saved_guest.guest_ai_usage_count,
    ) == snapshots["guest"]
    assert (
        db.session.get(Product, admin_product.id).id,
        db.session.get(Product, admin_product.id).name,
    ) == snapshots["admin_product"]
    assert (
        db.session.get(DailySales, admin_sale.id).id,
        db.session.get(DailySales, admin_sale.id).quantity,
    ) == snapshots["admin_sale"]
    assert (
        db.session.get(Product, guest_product.id).id,
        db.session.get(Product, guest_product.id).name,
    ) == snapshots["guest_product"]
    assert (
        db.session.get(DailySales, guest_sale.id).id,
        db.session.get(DailySales, guest_sale.id).quantity,
    ) == snapshots["guest_sale"]
    assert (
        saved_rate.window_started_at,
        saved_rate.request_count,
        saved_rate.updated_at,
    ) == snapshots["rate"]
    login.assert_not_called()


@pytest.mark.parametrize("invalid_limit", [None, "not-a-number", 0, -1])
def test_invalid_active_guest_limit_fails_closed(
    flask_app,
    monkeypatch,
    invalid_limit,
):
    flask_app.config["GUEST_ACTIVE_DATASET_LIMIT"] = invalid_limit
    login = Mock()
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

    assert Dataset.query.filter_by(kind="guest").count() == 0
    login.assert_not_called()


def test_admission_lock_failure_rolls_back_without_creating_guest(
    flask_app,
    monkeypatch,
):
    login = Mock()
    rollback = Mock(wraps=db.session.rollback)

    def fail_lock():
        raise SQLAlchemyError("test admission lock failure")

    monkeypatch.setattr(
        app_module,
        "_acquire_guest_admission_lock",
        fail_lock,
        raising=False,
    )
    monkeypatch.setattr(db.session, "rollback", rollback)
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

    assert Dataset.query.filter_by(kind="guest").count() == 0
    rollback.assert_called_once_with()
    login.assert_not_called()


def test_count_failure_rolls_back_cleanup_without_creating_guest(
    flask_app,
    monkeypatch,
):
    expired_guest = _create_guest_dataset(
        absolute_expires_at=(
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=1)
        ),
    )
    product, sale = _create_product_and_sale(
        expired_guest,
        name="rollback対象Guest商品",
        quantity=33,
    )
    expired_guest_id = expired_guest.id
    product_id = product.id
    sale_id = sale.id
    db.session.commit()
    login = Mock()

    def fail_count(*, now=None):
        raise SQLAlchemyError("test active count failure")

    monkeypatch.setattr(
        app_module,
        "_get_active_guest_dataset_count",
        fail_count,
        raising=False,
    )
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

    assert db.session.get(Dataset, expired_guest_id) is not None
    assert db.session.get(Product, product_id) is not None
    assert db.session.get(DailySales, sale_id) is not None
    assert Dataset.query.filter_by(kind="guest").count() == 1
    login.assert_not_called()


def test_rate_limit_rejection_does_not_enter_capacity_flow(
    flask_app,
    monkeypatch,
):
    lock = Mock(side_effect=AssertionError("capacity lock must not run"))
    count = Mock(side_effect=AssertionError("capacity count must not run"))
    cleanup = Mock(side_effect=AssertionError("cleanup must not run"))
    login = Mock()
    monkeypatch.setattr(
        app_module,
        "_reserve_guest_creation_attempt",
        Mock(return_value=False),
    )
    monkeypatch.setattr(
        app_module,
        "_acquire_guest_admission_lock",
        lock,
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "_get_active_guest_dataset_count",
        count,
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "_cleanup_expired_guest_datasets",
        cleanup,
    )
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(TooManyRequests):
            app_module.start_guest_session()

    assert Dataset.query.filter_by(kind="guest").count() == 0
    lock.assert_not_called()
    count.assert_not_called()
    cleanup.assert_not_called()
    login.assert_not_called()


def test_rate_limit_precedes_lock_cleanup_count_and_login(
    flask_app,
    monkeypatch,
):
    events = []
    real_reserve = app_module._reserve_guest_creation_attempt
    real_lock = app_module._acquire_guest_admission_lock
    real_cleanup = app_module._cleanup_expired_guest_datasets
    real_count = app_module._get_active_guest_dataset_count

    def recording_reserve(client_key_hash, *, now=None):
        events.append("rate-limit")
        return real_reserve(client_key_hash, now=now)

    def recording_lock():
        events.append("admission-lock")
        return real_lock()

    def recording_cleanup(*, now=None):
        events.append("cleanup")
        return real_cleanup(now=now)

    def recording_count(*, now=None):
        events.append("count")
        return real_count(now=now)

    def recording_login(user):
        events.append("login")
        return True

    monkeypatch.setattr(
        app_module,
        "_reserve_guest_creation_attempt",
        recording_reserve,
    )
    monkeypatch.setattr(
        app_module,
        "_acquire_guest_admission_lock",
        recording_lock,
    )
    monkeypatch.setattr(
        app_module,
        "_cleanup_expired_guest_datasets",
        recording_cleanup,
    )
    monkeypatch.setattr(
        app_module,
        "_get_active_guest_dataset_count",
        recording_count,
    )
    monkeypatch.setattr(app_module, "login_user", recording_login)

    with flask_app.test_request_context("/"):
        app_module.start_guest_session()

    assert events == [
        "rate-limit",
        "admission-lock",
        "cleanup",
        "count",
        "login",
    ]


def test_unknown_database_dialect_fails_closed(flask_app, monkeypatch):
    unsupported_bind = SimpleNamespace(
        dialect=SimpleNamespace(name="unsupported")
    )
    monkeypatch.setattr(
        db.session,
        "get_bind",
        Mock(return_value=unsupported_bind),
    )

    with pytest.raises(ServiceUnavailable):
        app_module._acquire_guest_admission_lock()
