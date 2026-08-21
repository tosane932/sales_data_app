import datetime
import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

import app as app_module
from models import Dataset, db


ADMIN_ROUTE_PATHS = [
    "/",
    "/input",
    "/dashboard",
    "/api/dashboard-data",
    "/api/ai-advice",
    "/api/greeting",
]


def _create_guest_dataset():
    now = datetime.datetime.now(datetime.timezone.utc)
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _load_user(flask_app, user_id):
    with flask_app.test_request_context("/"):
        return app_module.load_user(user_id)


def _guest_client(flask_app, dataset):
    test_client = flask_app.test_client()
    with test_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{dataset.id}"
        session_data["_fresh"] = True
    return test_client


def test_guest_a_dataset_restores_guest_a(flask_app):
    guest_dataset = _create_guest_dataset()

    restored_user = _load_user(flask_app, f"guest:{guest_dataset.id}")

    assert restored_user is not None
    assert restored_user.dataset_id == guest_dataset.id


def test_guest_b_dataset_restores_guest_b(flask_app):
    guest_dataset = _create_guest_dataset()

    restored_user = _load_user(flask_app, f"guest:{guest_dataset.id}")

    assert restored_user is not None
    assert restored_user.dataset_id == guest_dataset.id


def test_guest_a_and_guest_b_have_different_flask_login_ids(flask_app):
    guest_a_dataset = _create_guest_dataset()
    guest_b_dataset = _create_guest_dataset()

    guest_a = _load_user(flask_app, f"guest:{guest_a_dataset.id}")
    guest_b = _load_user(flask_app, f"guest:{guest_b_dataset.id}")

    assert guest_a.get_id() != guest_b.get_id()


def test_guest_user_id_format_is_exact():
    dataset_id = uuid.uuid4()

    assert hasattr(app_module, "GuestUser")
    guest_user = app_module.GuestUser(dataset_id)

    assert guest_user.get_id() == f"guest:{dataset_id}"


def test_restored_guest_is_authenticated(flask_app):
    guest_dataset = _create_guest_dataset()

    restored_user = _load_user(flask_app, f"guest:{guest_dataset.id}")

    assert restored_user.is_authenticated is True


def test_restored_guest_is_not_admin(flask_app):
    guest_dataset = _create_guest_dataset()

    restored_user = _load_user(flask_app, f"guest:{guest_dataset.id}")

    assert restored_user.is_guest is True
    assert restored_user.is_admin is False


def test_nonexistent_guest_dataset_is_not_restored(flask_app):
    restored_user = _load_user(flask_app, f"guest:{uuid.uuid4()}")

    assert restored_user is None


@pytest.mark.parametrize(
    "user_id",
    [
        "guest:not-a-uuid",
        "guest:",
        f"unknown:{uuid.uuid4()}",
    ],
)
def test_invalid_guest_identity_is_not_restored(flask_app, user_id):
    restored_user = _load_user(flask_app, user_id)

    assert restored_user is None


def test_admin_dataset_uuid_is_not_restored_as_guest(flask_app, admin_dataset):
    restored_user = _load_user(
        flask_app,
        f"guest:{admin_dataset.id}",
    )

    assert restored_user is None


def test_deleted_guest_dataset_is_not_restored(flask_app):
    guest_dataset = _create_guest_dataset()
    guest_dataset_id = guest_dataset.id
    db.session.delete(guest_dataset)
    db.session.commit()

    restored_user = _load_user(flask_app, f"guest:{guest_dataset_id}")

    assert restored_user is None


def test_non_guest_kind_is_not_restored_as_guest(flask_app, admin_dataset):
    restored_user = _load_user(
        flask_app,
        f"guest:{admin_dataset.id}",
    )

    assert restored_user is None


def test_guest_db_error_fails_closed(flask_app, monkeypatch):
    class FailingQuery:
        def filter_by(self, **kwargs):
            raise SQLAlchemyError("test database error")

    rollback = Mock()
    monkeypatch.setattr(Dataset, "query", FailingQuery())
    monkeypatch.setattr(db.session, "rollback", rollback)

    restored_user = _load_user(flask_app, f"guest:{uuid.uuid4()}")

    assert restored_user is None
    rollback.assert_called_once_with()


def test_guest_lookup_scopes_id_kind_and_system_key(flask_app, monkeypatch):
    dataset_id = uuid.uuid4()
    received_filters = {}

    class RecordingQuery:
        def filter_by(self, **kwargs):
            received_filters.update(kwargs)
            return self

        def one_or_none(self):
            return None

    monkeypatch.setattr(Dataset, "query", RecordingQuery())

    restored_user = _load_user(flask_app, f"guest:{dataset_id}")

    assert restored_user is None
    assert received_filters == {
        "id": dataset_id,
        "kind": "guest",
        "system_key": None,
    }


@pytest.mark.parametrize("path", ADMIN_ROUTE_PATHS)
def test_guest_user_is_forbidden_from_admin_route(
    flask_app,
    path,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    guest_client = _guest_client(flask_app, guest_dataset)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = guest_client.get(path)

    assert response.status_code == 403


def test_guest_cannot_escalate_with_session_role_values(
    flask_app,
    admin_dataset,
):
    guest_dataset = _create_guest_dataset()
    guest_client = _guest_client(flask_app, guest_dataset)

    with guest_client.session_transaction() as session_data:
        session_data["role"] = "admin"
        session_data["is_admin"] = True
        session_data["dataset_id"] = str(admin_dataset.id)
        session_data[app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY] = (
            app_module._get_admin_auth_fingerprint(
                flask_app.config["ADMIN_PASSWORD_HASH"]
            )
        )

    response = guest_client.get("/")

    assert response.status_code == 403


def test_guest_id_is_not_restored_as_admin(flask_app):
    guest_dataset = _create_guest_dataset()

    restored_user = _load_user(flask_app, f"guest:{guest_dataset.id}")

    assert restored_user is not None
    assert restored_user.get_id().startswith("guest:")
    assert restored_user.is_admin is False


def test_admin_id_is_not_restored_as_guest(flask_app):
    with flask_app.test_request_context("/"):
        app_module.session[
            app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY
        ] = app_module._get_admin_auth_fingerprint(
            flask_app.config["ADMIN_PASSWORD_HASH"]
        )

        restored_user = app_module.load_user("admin")

    assert restored_user is not None
    assert restored_user.get_id() == "admin"
    assert restored_user.is_admin is True
    assert not hasattr(restored_user, "dataset_id")
