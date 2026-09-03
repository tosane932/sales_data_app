import datetime
from unittest.mock import Mock

import pytest
from flask_login import current_user
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import ServiceUnavailable

import app as app_module
from models import DailySales, Dataset, Product, db


NOW = datetime.datetime(2026, 9, 3, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def enable_sqlite_foreign_keys(flask_app):
    db.session.execute(text("PRAGMA foreign_keys = ON"))
    assert db.session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def _create_guest_dataset(
    *,
    created_at,
    last_activity_at,
    absolute_expires_at,
    guest_ai_usage_count=None,
):
    dataset_values = {
        "kind": "guest",
        "system_key": None,
        "created_at": created_at,
        "last_activity_at": last_activity_at,
        "absolute_expires_at": absolute_expires_at,
    }
    if guest_ai_usage_count is not None:
        dataset_values["guest_ai_usage_count"] = guest_ai_usage_count

    dataset = Dataset(
        **dataset_values,
    )
    db.session.add(dataset)
    db.session.flush()
    return dataset


def _create_active_guest_dataset():
    return _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=1),
        last_activity_at=NOW - datetime.timedelta(minutes=10),
        absolute_expires_at=NOW + datetime.timedelta(hours=1),
    )


def _create_absolute_expired_guest_dataset():
    return _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=3),
        last_activity_at=NOW - datetime.timedelta(minutes=10),
        absolute_expires_at=NOW - datetime.timedelta(hours=1),
    )


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
        date=datetime.date(2026, 9, 1),
        quantity=quantity,
    )
    db.session.add(sale)
    db.session.flush()
    return product, sale


def _run_cleanup():
    deleted_count = app_module._cleanup_expired_guest_datasets(now=NOW)
    db.session.commit()
    return deleted_count


def test_cleanup_deletes_absolute_expired_guest_dataset(flask_app):
    expired_guest = _create_absolute_expired_guest_dataset()
    expired_guest_id = expired_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None


def test_cleanup_deletes_idle_expired_guest_dataset(flask_app):
    expired_guest = _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=1),
        last_activity_at=NOW - datetime.timedelta(minutes=31),
        absolute_expires_at=NOW + datetime.timedelta(hours=1),
    )
    expired_guest_id = expired_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None


def test_cleanup_keeps_active_guest_dataset(flask_app):
    active_guest = _create_active_guest_dataset()
    active_guest_id = active_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 0
    assert db.session.get(Dataset, active_guest_id) is not None


def test_cleanup_never_deletes_admin_dataset(flask_app, admin_dataset):
    admin_dataset_id = admin_dataset.id

    deleted_count = _run_cleanup()

    assert deleted_count == 0
    saved_admin = db.session.get(Dataset, admin_dataset_id)
    assert saved_admin is not None
    assert saved_admin.kind == "admin"
    assert saved_admin.system_key == "admin"


def test_cleanup_keeps_other_active_guest_when_expired_guest_exists(flask_app):
    expired_guest = _create_absolute_expired_guest_dataset()
    active_guest = _create_active_guest_dataset()
    expired_guest_id = expired_guest.id
    active_guest_id = active_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None
    assert db.session.get(Dataset, active_guest_id) is not None


def test_cleanup_keeps_active_guest_products_and_sales_when_expired_guest_is_deleted(
    flask_app,
):
    expired_guest = _create_absolute_expired_guest_dataset()
    expired_product, expired_sale = _create_product_and_sale(
        expired_guest,
        name="期限切れGuest A商品",
        quantity=91,
    )
    active_guest = _create_active_guest_dataset()
    active_product, active_sale = _create_product_and_sale(
        active_guest,
        name="有効Guest B商品",
        quantity=92,
    )
    expired_guest_id = expired_guest.id
    expired_product_id = expired_product.id
    expired_sale_id = expired_sale.id
    active_guest_id = active_guest.id
    active_product_id = active_product.id
    active_sale_id = active_sale.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None
    assert db.session.get(Product, expired_product_id) is None
    assert db.session.get(DailySales, expired_sale_id) is None
    assert db.session.get(Dataset, active_guest_id) is not None
    assert db.session.get(Product, active_product_id) is not None
    assert db.session.get(DailySales, active_sale_id) is not None


def test_cleanup_removes_expired_guest_ai_usage_with_dataset(flask_app):
    expired_guest = _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=3),
        last_activity_at=NOW - datetime.timedelta(minutes=10),
        absolute_expires_at=NOW - datetime.timedelta(hours=1),
        guest_ai_usage_count=3,
    )
    expired_guest_id = expired_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None


def test_cleanup_keeps_other_active_guest_ai_usage_count(flask_app):
    expired_guest = _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=3),
        last_activity_at=NOW - datetime.timedelta(minutes=10),
        absolute_expires_at=NOW - datetime.timedelta(hours=1),
        guest_ai_usage_count=3,
    )
    active_guest = _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=1),
        last_activity_at=NOW - datetime.timedelta(minutes=10),
        absolute_expires_at=NOW + datetime.timedelta(hours=1),
        guest_ai_usage_count=2,
    )
    expired_guest_id = expired_guest.id
    active_guest_id = active_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None
    saved_active_guest = db.session.get(Dataset, active_guest_id)
    assert saved_active_guest is not None
    assert saved_active_guest.guest_ai_usage_count == 2


def test_cleanup_deletes_guest_at_exact_expiration_boundary(flask_app):
    absolute_boundary_guest = _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=2),
        last_activity_at=NOW - datetime.timedelta(minutes=10),
        absolute_expires_at=NOW,
    )
    idle_boundary_guest = _create_guest_dataset(
        created_at=NOW - datetime.timedelta(hours=1),
        last_activity_at=NOW - datetime.timedelta(minutes=30),
        absolute_expires_at=NOW + datetime.timedelta(hours=1),
    )
    absolute_boundary_guest_id = absolute_boundary_guest.id
    idle_boundary_guest_id = idle_boundary_guest.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 2
    assert db.session.get(Dataset, absolute_boundary_guest_id) is None
    assert db.session.get(Dataset, idle_boundary_guest_id) is None


def test_cleanup_deletes_expired_guest_products_and_sales(flask_app):
    expired_guest = _create_absolute_expired_guest_dataset()
    product, sale = _create_product_and_sale(
        expired_guest,
        name="期限切れGuest商品",
        quantity=41,
    )
    expired_guest_id = expired_guest.id
    product_id = product.id
    sale_id = sale.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, expired_guest_id) is None
    assert db.session.get(Product, product_id) is None
    assert db.session.get(DailySales, sale_id) is None


def test_cleanup_keeps_admin_products_and_sales(
    flask_app,
    admin_dataset,
):
    expired_guest = _create_absolute_expired_guest_dataset()
    _create_product_and_sale(
        expired_guest,
        name="削除対象Guest商品",
        quantity=51,
    )
    admin_product, admin_sale = _create_product_and_sale(
        admin_dataset,
        name="保護対象Admin商品",
        quantity=61,
    )
    admin_dataset_id = admin_dataset.id
    admin_product_id = admin_product.id
    admin_sale_id = admin_sale.id
    db.session.commit()

    deleted_count = _run_cleanup()

    assert deleted_count == 1
    assert db.session.get(Dataset, admin_dataset_id) is not None
    assert db.session.get(Product, admin_product_id) is not None
    assert db.session.get(DailySales, admin_sale_id) is not None


def test_cleanup_is_idempotent(flask_app):
    expired_guest = _create_absolute_expired_guest_dataset()
    active_guest = _create_active_guest_dataset()
    _create_product_and_sale(
        expired_guest,
        name="冪等削除対象商品",
        quantity=71,
    )
    active_guest_id = active_guest.id
    db.session.commit()

    first_deleted_count = _run_cleanup()
    second_deleted_count = _run_cleanup()

    assert first_deleted_count == 1
    assert second_deleted_count == 0
    assert db.session.get(Dataset, active_guest_id) is not None
    assert Dataset.query.filter_by(kind="guest").count() == 1


def test_cleanup_database_failure_rolls_back_without_guest_login(
    flask_app,
    monkeypatch,
):
    expired_guest = _create_absolute_expired_guest_dataset()
    product, sale = _create_product_and_sale(
        expired_guest,
        name="rollback確認商品",
        quantity=81,
    )
    expired_guest_id = expired_guest.id
    product_id = product.id
    sale_id = sale.id
    db.session.commit()

    def failing_cleanup(*, now):
        DailySales.query.filter_by(id=sale_id).delete(
            synchronize_session=False
        )
        Product.query.filter_by(id=product_id).delete(
            synchronize_session=False
        )
        db.session.flush()
        raise SQLAlchemyError("test cleanup failure")

    real_rollback = db.session.rollback
    rollback = Mock(wraps=real_rollback)
    login = Mock()
    monkeypatch.setattr(
        app_module,
        "_cleanup_expired_guest_datasets",
        failing_cleanup,
    )
    monkeypatch.setattr(db.session, "rollback", rollback)
    monkeypatch.setattr(app_module, "login_user", login)

    with flask_app.test_request_context("/"):
        with pytest.raises(ServiceUnavailable):
            app_module.start_guest_session()

        assert current_user.is_authenticated is False

    assert rollback.call_count == 1
    login.assert_not_called()
    assert db.session.get(Dataset, expired_guest_id) is not None
    assert db.session.get(Product, product_id) is not None
    assert db.session.get(DailySales, sale_id) is not None
    assert Dataset.query.filter_by(kind="guest").count() == 1


def test_start_guest_session_calls_expired_guest_cleanup(
    flask_app,
    monkeypatch,
):
    cleanup = Mock(wraps=app_module._cleanup_expired_guest_datasets)
    monkeypatch.setattr(
        app_module,
        "_cleanup_expired_guest_datasets",
        cleanup,
    )

    with flask_app.test_request_context("/"):
        app_module.start_guest_session()

    cleanup.assert_called_once()


def test_start_guest_session_creates_authenticated_guest_after_cleanup(
    flask_app,
):
    expired_guest = _create_guest_dataset(
        created_at=datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=3),
        last_activity_at=datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=3),
        absolute_expires_at=datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=1),
    )
    expired_guest_id = expired_guest.id
    db.session.commit()

    with flask_app.test_request_context("/"):
        new_guest = app_module.start_guest_session()
        new_guest_id = new_guest.id

        assert current_user.is_authenticated is True
        assert current_user.get_id() == f"guest:{new_guest_id}"

    assert db.session.get(Dataset, expired_guest_id) is None
    assert db.session.get(Dataset, new_guest_id) is not None
    assert Dataset.query.filter_by(kind="guest").count() == 1
