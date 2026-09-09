import datetime
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from flask import g
from sqlalchemy.exc import SQLAlchemyError

import app as app_module
from models import GuestCreationRateLimit, db


TEST_LIMIT = 5
TEST_WINDOW_SECONDS = 15 * 60
TEST_IP = "192.0.2.40"


def _configure_rate_limit(flask_app, *, limit=TEST_LIMIT):
    flask_app.config.update(
        ADMIN_LOGIN_RATE_LIMIT_MAX_FAILURES=limit,
        ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS=TEST_WINDOW_SECONDS,
    )


def _post_login(
    client,
    csrf_token,
    *,
    username,
    password,
    ip_address=TEST_IP,
):
    g.pop("csrf_token", None)
    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": csrf_token(client, "/login"),
        },
        environ_base={"REMOTE_ADDR": ip_address},
        follow_redirects=False,
    )


def _alert_text(response):
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    alert = document.select_one('[role="alert"]')
    assert alert is not None
    return alert.get_text(strip=True)


def _admin_client_key(flask_app, ip_address=TEST_IP):
    with flask_app.test_request_context(
        "/login",
        environ_base={"REMOTE_ADDR": ip_address},
    ):
        return app_module._get_admin_login_client_key()


def test_five_failed_logins_are_allowed_and_sixth_is_rate_limited(
    flask_app,
    client,
    admin_auth_config,
    csrf_token,
):
    _configure_rate_limit(flask_app)

    responses = [
        _post_login(
            client,
            csrf_token,
            username=admin_auth_config.username,
            password="wrong-password",
        )
        for _ in range(TEST_LIMIT + 1)
    ]

    assert [response.status_code for response in responses] == [
        401,
        401,
        401,
        401,
        401,
        429,
    ]
    row = GuestCreationRateLimit.query.one()
    assert row.request_count == TEST_LIMIT
    assert TEST_IP not in row.client_key_hash


def test_wrong_username_and_wrong_password_have_same_external_failure(
    flask_app,
    client,
    admin_auth_config,
    csrf_token,
    monkeypatch,
):
    _configure_rate_limit(flask_app)
    password_check = Mock(return_value=False)
    monkeypatch.setattr(app_module, "check_password_hash", password_check)

    wrong_username = _post_login(
        client,
        csrf_token,
        username="unknown-admin",
        password="submitted-password",
    )
    wrong_password = _post_login(
        client,
        csrf_token,
        username=admin_auth_config.username,
        password="submitted-password",
    )

    assert wrong_username.status_code == wrong_password.status_code == 401
    assert _alert_text(wrong_username) == _alert_text(wrong_password)
    assert "unknown-admin" not in wrong_username.get_data(as_text=True)
    assert password_check.call_count == 2


def test_limit_reached_blocks_even_valid_credentials_without_login(
    flask_app,
    client,
    admin_auth_config,
    csrf_token,
):
    _configure_rate_limit(flask_app)
    for _ in range(TEST_LIMIT):
        response = _post_login(
            client,
            csrf_token,
            username=admin_auth_config.username,
            password="wrong-password",
        )
        assert response.status_code == 401

    response = _post_login(
        client,
        csrf_token,
        username=admin_auth_config.username,
        password=admin_auth_config.password,
    )

    assert response.status_code == 429
    with client.session_transaction() as session_data:
        assert "_user_id" not in session_data
        assert app_module.ADMIN_AUTH_FINGERPRINT_SESSION_KEY not in session_data


def test_success_before_limit_does_not_increment_failure_count(
    flask_app,
    client,
    admin_auth_config,
    csrf_token,
):
    _configure_rate_limit(flask_app)
    failed = _post_login(
        client,
        csrf_token,
        username=admin_auth_config.username,
        password="wrong-password",
    )
    succeeded = _post_login(
        client,
        csrf_token,
        username=admin_auth_config.username,
        password=admin_auth_config.password,
    )

    assert failed.status_code == 401
    assert succeeded.status_code == 302
    assert GuestCreationRateLimit.query.one().request_count == 1


def test_different_client_has_independent_login_failure_limit(
    flask_app,
    admin_auth_config,
    csrf_token,
):
    _configure_rate_limit(flask_app, limit=1)
    first_client = flask_app.test_client()
    second_client = flask_app.test_client()

    first_failure = _post_login(
        first_client,
        csrf_token,
        username=admin_auth_config.username,
        password="wrong-password",
        ip_address="192.0.2.41",
    )
    first_blocked = _post_login(
        first_client,
        csrf_token,
        username=admin_auth_config.username,
        password="wrong-password",
        ip_address="192.0.2.41",
    )
    second_failure = _post_login(
        second_client,
        csrf_token,
        username=admin_auth_config.username,
        password="wrong-password",
        ip_address="192.0.2.42",
    )

    assert first_failure.status_code == 401
    assert first_blocked.status_code == 429
    assert second_failure.status_code == 401
    assert GuestCreationRateLimit.query.count() == 2


def test_admin_failure_window_resets_at_exact_boundary(flask_app):
    _configure_rate_limit(flask_app, limit=1)
    client_key = _admin_client_key(flask_app)
    window_start = datetime.datetime(
        2026,
        9,
        9,
        12,
        0,
        tzinfo=datetime.timezone.utc,
    )

    assert app_module._reserve_admin_login_failure(
        client_key, now=window_start,
    ) is True
    assert app_module._reserve_admin_login_failure(
        client_key,
        now=window_start + datetime.timedelta(seconds=899),
    ) is False
    assert app_module._reserve_admin_login_failure(
        client_key,
        now=window_start + datetime.timedelta(seconds=900),
    ) is True


def test_admin_and_guest_rate_limits_use_independent_keys_and_counters(
    flask_app,
):
    _configure_rate_limit(flask_app)
    admin_key = _admin_client_key(flask_app)
    with flask_app.test_request_context(
        "/guest/start",
        environ_base={"REMOTE_ADDR": TEST_IP},
    ):
        guest_key = app_module._get_guest_creation_client_key()

    assert admin_key != guest_key
    assert app_module._reserve_admin_login_failure(admin_key) is True
    assert app_module._reserve_guest_creation_attempt(guest_key) is True
    assert sorted(
        row.request_count for row in GuestCreationRateLimit.query.all()
    ) == [1, 1]


def test_admin_failure_reservation_uses_atomic_upsert(
    flask_app,
    monkeypatch,
):
    _configure_rate_limit(flask_app)
    client_key = _admin_client_key(flask_app)
    statements = []
    real_execute = db.session.execute

    def record_statement(statement, *args, **kwargs):
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) == "guest_creation_rate_limits":
            statements.append(statement)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "execute", record_statement)

    assert app_module._reserve_admin_login_failure(client_key) is True

    assert len(statements) == 1
    sql_text = str(statements[0].compile(dialect=db.engine.dialect)).upper()
    assert sql_text.startswith("INSERT INTO GUEST_CREATION_RATE_LIMITS")
    assert "ON CONFLICT" in sql_text
    assert "REQUEST_COUNT <" in sql_text


@pytest.mark.parametrize("token_mode", ["missing", "tampered"])
def test_csrf_rejection_does_not_record_admin_login_failure(
    flask_app,
    client,
    admin_auth_config,
    csrf_token,
    token_mode,
):
    _configure_rate_limit(flask_app)
    data = {
        "username": admin_auth_config.username,
        "password": "wrong-password",
    }
    if token_mode == "tampered":
        valid_token = csrf_token(client, "/login")
        data["csrf_token"] = (
            ("x" if valid_token[0] != "x" else "y") + valid_token[1:]
        )

    response = client.post("/login", data=data)

    assert response.status_code == 400
    assert GuestCreationRateLimit.query.count() == 0


def test_rate_limit_database_failure_is_fail_closed_without_login(
    flask_app,
    client,
    admin_auth_config,
    csrf_token,
    monkeypatch,
):
    _configure_rate_limit(flask_app)
    login = Mock()
    monkeypatch.setattr(app_module, "login_user", login)
    real_execute = db.session.execute

    def fail_rate_limit_select(statement, *args, **kwargs):
        if getattr(statement, "is_select", False):
            raise SQLAlchemyError("synthetic rate limit database failure")
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "execute", fail_rate_limit_select)

    response = _post_login(
        client,
        csrf_token,
        username=admin_auth_config.username,
        password=admin_auth_config.password,
    )

    assert response.status_code == 503
    login.assert_not_called()
    assert GuestCreationRateLimit.query.count() == 0
