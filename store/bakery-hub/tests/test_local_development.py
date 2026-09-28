import os
import subprocess
import sys

import pytest
from flask import g
from werkzeug.exceptions import ServiceUnavailable

import app as app_module
import config
from models import Dataset


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, False),
        ("", False),
        ("false", False),
        ("1", False),
        ("yes", False),
        ("truee", False),
        ("true", True),
        (" TRUE ", True),
    ],
)
def test_local_development_requires_explicit_true(raw_value, expected):
    assert config._environment_flag_is_true(raw_value) is expected


@pytest.mark.parametrize(
    ("local_development", "expected_local", "expected_secure"),
    [
        ("true", True, False),
        ("false", False, True),
        ("truee", False, True),
    ],
)
def test_local_development_alone_controls_session_cookie_secure(
    local_development,
    expected_local,
    expected_secure,
):
    environment = os.environ.copy()
    environment["LOCAL_DEVELOPMENT"] = local_development
    environment["SESSION_COOKIE_SECURE"] = "false"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import config; "
                f"assert config.LOCAL_DEVELOPMENT is {expected_local}; "
                f"assert config.SESSION_COOKIE_SECURE is {expected_secure}"
            ),
        ],
        cwd=os.path.dirname(app_module.__file__),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_direct_python_run_loads_dotenv_without_overriding_process_env(
    tmp_path,
    monkeypatch,
):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "LOCAL_DEVELOPMENT=true\n"
        "SECRET_KEY=dotenv-file-dummy-value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LOCAL_DEVELOPMENT", raising=False)
    monkeypatch.setenv("SECRET_KEY", "process-environment-dummy-value")

    loaded = app_module._load_direct_run_environment(dotenv_path)

    assert loaded is True
    assert os.environ["LOCAL_DEVELOPMENT"] == "true"
    assert os.environ["SECRET_KEY"] == "process-environment-dummy-value"


@pytest.mark.parametrize(
    "client_key_getter",
    [
        app_module._get_admin_login_client_key,
        app_module._get_guest_creation_client_key,
    ],
)
def test_production_mode_uses_valid_cf_connecting_ip(
    flask_app,
    monkeypatch,
    client_key_getter,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", False)

    with flask_app.test_request_context(
        "/",
        headers={"CF-Connecting-IP": "203.0.113.40"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        first_key = client_key_getter()

    with flask_app.test_request_context(
        "/",
        headers={"CF-Connecting-IP": "203.0.113.40"},
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
    ):
        second_key = client_key_getter()

    assert first_key == second_key
    assert len(first_key) == 64


@pytest.mark.parametrize(
    "client_key_getter",
    [
        app_module._get_admin_login_client_key,
        app_module._get_guest_creation_client_key,
    ],
)
@pytest.mark.parametrize("cf_connecting_ip", [None, "not-an-ip"])
def test_production_mode_rejects_missing_or_invalid_cf_connecting_ip(
    flask_app,
    monkeypatch,
    client_key_getter,
    cf_connecting_ip,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", False)
    headers = {"X-Forwarded-For": "198.51.100.99"}
    if cf_connecting_ip is not None:
        headers["CF-Connecting-IP"] = cf_connecting_ip

    with flask_app.test_request_context(
        "/",
        headers=headers,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        with pytest.raises(ServiceUnavailable):
            client_key_getter()


@pytest.mark.parametrize(
    "client_key_getter",
    [
        app_module._get_admin_login_client_key,
        app_module._get_guest_creation_client_key,
    ],
)
@pytest.mark.parametrize(
    "remote_addr",
    ["127.0.0.1", "::1", "192.168.1.50"],
)
def test_local_development_uses_valid_remote_addr_only(
    flask_app,
    monkeypatch,
    client_key_getter,
    remote_addr,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", True)

    with flask_app.test_request_context(
        "/",
        headers={
            "CF-Connecting-IP": "not-an-ip",
            "X-Forwarded-For": "also-not-an-ip",
        },
        environ_base={"REMOTE_ADDR": remote_addr},
    ):
        client_key = client_key_getter()

    assert len(client_key) == 64


@pytest.mark.parametrize(
    "client_key_getter",
    [
        app_module._get_admin_login_client_key,
        app_module._get_guest_creation_client_key,
    ],
)
@pytest.mark.parametrize("remote_addr", ["", "not-an-ip"])
def test_local_development_rejects_missing_or_invalid_remote_addr(
    flask_app,
    monkeypatch,
    client_key_getter,
    remote_addr,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", True)

    with flask_app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": remote_addr},
    ):
        with pytest.raises(ServiceUnavailable):
            client_key_getter()


def test_testing_mode_keeps_configured_client_ip_fallback(
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "TESTING", True)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", False)
    monkeypatch.setitem(
        flask_app.config,
        "ADMIN_LOGIN_RATE_LIMIT_TEST_CLIENT_IP",
        "192.0.2.80",
    )
    monkeypatch.setitem(
        flask_app.config,
        "GUEST_CREATION_RATE_LIMIT_TEST_CLIENT_IP",
        "192.0.2.81",
    )

    with flask_app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": ""},
    ):
        admin_key = app_module._get_admin_login_client_key()
        guest_key = app_module._get_guest_creation_client_key()

    assert len(admin_key) == 64
    assert len(guest_key) == 64
    assert admin_key != guest_key


def test_local_development_admin_login_works_without_cloudflare_header(
    flask_app,
    admin_dataset,
    admin_auth_config,
    csrf_token,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", True)
    monkeypatch.setitem(flask_app.config, "SESSION_COOKIE_SECURE", False)
    local_client = flask_app.test_client()
    g.pop("csrf_token", None)
    root_response = local_client.get("/")
    assert root_response.status_code == 302
    assert root_response.headers["Location"].startswith("/login?")
    assert local_client.get("/login").status_code == 200
    token = csrf_token(local_client, "/login")

    response = local_client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": admin_auth_config.password,
            "csrf_token": token,
        },
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with local_client.session_transaction() as session_data:
        assert session_data.get("_user_id") == "admin"


def test_local_development_guest_demo_and_material_orders_work_from_lan(
    flask_app,
    csrf_token,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "TESTING", False)
    monkeypatch.setitem(flask_app.config, "LOCAL_DEVELOPMENT", True)
    monkeypatch.setitem(flask_app.config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setitem(
        flask_app.config,
        "GUEST_CREATION_RATE_LIMIT_MAX_ATTEMPTS",
        5,
    )
    monkeypatch.setitem(
        flask_app.config,
        "GUEST_CREATION_RATE_LIMIT_WINDOW_SECONDS",
        60,
    )
    local_client = flask_app.test_client()
    g.pop("csrf_token", None)
    token = csrf_token(local_client, "/login")

    response = local_client.post(
        "/guest/start",
        data={"csrf_token": token},
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert Dataset.query.filter_by(kind="guest").count() == 1
    with local_client.session_transaction() as session_data:
        assert session_data.get("_user_id", "").startswith("guest:")

    g.pop("_login_user", None)
    material_orders_response = local_client.get("/material-orders")
    assert material_orders_response.status_code == 200
