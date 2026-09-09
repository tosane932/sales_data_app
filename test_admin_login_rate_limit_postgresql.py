import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine, text

from models import db


POSTGRESQL_TEST_DATABASE_URL = os.environ.get(
    "TEST_POSTGRESQL_DATABASE_URL"
)

pytestmark = pytest.mark.skipif(
    not POSTGRESQL_TEST_DATABASE_URL,
    reason="isolated PostgreSQL URL is not configured",
)


WORKER_CODE = r"""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import threading

from werkzeug.security import generate_password_hash

import app as app_module


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


app_module.app.config.update(
    TESTING=True,
    ADMIN_USERNAME="integration-admin",
    ADMIN_PASSWORD_HASH=generate_password_hash(
        "integration-correct-password",
        method="pbkdf2:sha256:1",
    ),
    ADMIN_LOGIN_RATE_LIMIT_MAX_FAILURES=5,
    ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS=15 * 60,
    GUEST_CREATION_RATE_LIMIT_MAX_ATTEMPTS=100,
    GUEST_CREATION_RATE_LIMIT_WINDOW_SECONDS=60,
    GUEST_ACTIVE_DATASET_LIMIT=10,
)

remote_address = os.environ["TEST_CLIENT_ADDRESS"]


def client_with_csrf_token():
    client = app_module.app.test_client()
    login_page = client.get(
        "/login",
        environ_base={"REMOTE_ADDR": remote_address},
    )
    parser = CsrfTokenParser()
    parser.feed(login_page.get_data(as_text=True))
    if login_page.status_code != 200 or not parser.token:
        raise RuntimeError("login page did not provide a CSRF token")
    return client, parser.token


clients_and_tokens = [client_with_csrf_token() for _ in range(8)]
start_barrier = threading.Barrier(len(clients_and_tokens))
credential_check_lock = threading.Lock()
credential_check_count = 0
real_check_password_hash = app_module.check_password_hash


def tracked_check_password_hash(*args, **kwargs):
    global credential_check_count
    with credential_check_lock:
        credential_check_count += 1
    return real_check_password_hash(*args, **kwargs)


app_module.check_password_hash = tracked_check_password_hash


def submit_failed_login(client_and_token):
    client, csrf_token = client_and_token
    start_barrier.wait(timeout=15)
    response = client.post(
        "/login",
        data={
            "username": "integration-admin",
            "password": "integration-wrong-password",
            "csrf_token": csrf_token,
        },
        environ_base={"REMOTE_ADDR": remote_address},
        follow_redirects=False,
    )
    return response.status_code


with ThreadPoolExecutor(max_workers=8) as executor:
    statuses = list(executor.map(
        submit_failed_login,
        clients_and_tokens,
    ))

after_limit_client, after_limit_token = client_with_csrf_token()
after_limit_response = after_limit_client.post(
    "/login",
    data={
        "username": "integration-admin",
        "password": "integration-wrong-password",
        "csrf_token": after_limit_token,
    },
    environ_base={"REMOTE_ADDR": remote_address},
    follow_redirects=False,
)

with app_module.app.test_request_context(
    "/guest/start",
    environ_base={"REMOTE_ADDR": remote_address},
):
    guest_client_key = app_module._get_guest_creation_client_key()
    guest_reserved = app_module._reserve_guest_creation_attempt(
        guest_client_key
    )

Path(os.environ["TEST_RESULT_PATH"]).write_text(
    json.dumps({
        "statuses": statuses,
        "after_limit_status": after_limit_response.status_code,
        "guest_reserved": guest_reserved,
        "credential_check_count": credential_check_count,
    }),
    encoding="utf-8",
)
"""


STALE_PRECHECK_WORKER_CODE = r"""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import time

from werkzeug.security import generate_password_hash

import app as app_module


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


app_module.app.config.update(
    TESTING=True,
    ADMIN_USERNAME="integration-admin",
    ADMIN_PASSWORD_HASH=generate_password_hash(
        "integration-correct-password",
        method="pbkdf2:sha256:1",
    ),
    ADMIN_LOGIN_RATE_LIMIT_MAX_FAILURES=5,
    ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS=15 * 60,
    GUEST_CREATION_RATE_LIMIT_MAX_ATTEMPTS=100,
    GUEST_CREATION_RATE_LIMIT_WINDOW_SECONDS=60,
    GUEST_ACTIVE_DATASET_LIMIT=10,
)

remote_address = os.environ["TEST_CLIENT_ADDRESS"]
credential_check_count = 0
real_check_password_hash = app_module.check_password_hash


def tracked_check_password_hash(*args, **kwargs):
    global credential_check_count
    credential_check_count += 1
    Path(os.environ["TEST_CREDENTIAL_MARKER"]).touch()
    return real_check_password_hash(*args, **kwargs)


app_module.check_password_hash = tracked_check_password_hash

client = app_module.app.test_client()
login_page = client.get(
    "/login",
    environ_base={"REMOTE_ADDR": remote_address},
)
parser = CsrfTokenParser()
parser.feed(login_page.get_data(as_text=True))
if login_page.status_code != 200 or not parser.token:
    raise RuntimeError("login page did not provide a CSRF token")

with app_module.app.test_request_context(
    "/login",
    environ_base={"REMOTE_ADDR": remote_address},
):
    client_key_hash = app_module._get_admin_login_client_key()
    advisory_lock_key = app_module._get_admin_login_advisory_lock_key(
        client_key_hash
    )

Path(os.environ["TEST_LOCK_IDENTITY_PATH"]).write_text(
    json.dumps({
        "client_key_hash": client_key_hash,
        "advisory_lock_key": advisory_lock_key,
    }),
    encoding="utf-8",
)

start_marker = Path(os.environ["TEST_START_MARKER"])
deadline = time.monotonic() + 15
while not start_marker.exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("login request was not released in time")
    time.sleep(0.01)

response = client.post(
    "/login",
    data={
        "username": "integration-admin",
        "password": "integration-correct-password",
        "csrf_token": parser.token,
    },
    environ_base={"REMOTE_ADDR": remote_address},
    follow_redirects=False,
)

with client.session_transaction() as session_data:
    authenticated = "_user_id" in session_data

Path(os.environ["TEST_RESULT_PATH"]).write_text(
    json.dumps({
        "status": response.status_code,
        "authenticated": authenticated,
        "credential_check_count": credential_check_count,
    }),
    encoding="utf-8",
)
"""


def _worker_environment(
    *,
    client_address,
    result_path,
    application_name=None,
    lock_identity_path=None,
    start_marker=None,
    credential_marker=None,
):
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=POSTGRESQL_TEST_DATABASE_URL,
        SECRET_KEY="isolated-integration-test-only",
        TEST_CLIENT_ADDRESS=client_address,
        TEST_RESULT_PATH=str(result_path),
    )
    if application_name is not None:
        environment["PGAPPNAME"] = application_name
    if lock_identity_path is not None:
        environment["TEST_LOCK_IDENTITY_PATH"] = str(
            lock_identity_path
        )
    if start_marker is not None:
        environment["TEST_START_MARKER"] = str(start_marker)
    if credential_marker is not None:
        environment["TEST_CREDENTIAL_MARKER"] = str(
            credential_marker
        )
    return environment


def _wait_for_file(path, process, failure_message):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            pytest.fail(failure_message)
        time.sleep(0.05)
    pytest.fail(failure_message)


def _wait_for_postgresql_advisory_lock(
    engine,
    application_name,
    process,
    credential_marker,
):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if credential_marker.exists():
            pytest.fail(
                "credential verification started before the advisory lock"
            )
        if process.poll() is not None:
            pytest.fail(
                "login request exited before waiting for the advisory lock"
            )

        with engine.connect() as connection:
            waiting_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM pg_stat_activity "
                    "WHERE application_name = :application_name "
                    "AND wait_event_type = 'Lock' "
                    "AND wait_event = 'advisory'"
                ),
                {"application_name": application_name},
            ).scalar_one()

        if waiting_count == 1:
            return
        time.sleep(0.05)

    pytest.fail("login request did not wait for the advisory lock")


def test_concurrent_failed_admin_logins_never_exceed_postgresql_limit(
    tmp_path,
):
    engine = create_engine(
        POSTGRESQL_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )
    repository_root = Path(__file__).resolve().parent
    result_path = tmp_path / "concurrent-login-result.json"
    schema_is_ready = False

    try:
        with engine.begin() as connection:
            db.metadata.drop_all(connection)
            db.metadata.create_all(connection)
        schema_is_ready = True

        worker = subprocess.run(
            [sys.executable, "-c", WORKER_CODE],
            cwd=repository_root,
            env=_worker_environment(
                client_address="192.0.2.80",
                result_path=result_path,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )

        assert worker.returncode == 0
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert sorted(result["statuses"]) == [
            401,
            401,
            401,
            401,
            401,
            429,
            429,
            429,
        ]
        assert result["after_limit_status"] == 429
        assert result["guest_reserved"] is True
        assert result["credential_check_count"] == 5

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT client_key_hash, request_count "
                    "FROM guest_creation_rate_limits "
                    "ORDER BY request_count"
                )
            ).all()

        assert [row.request_count for row in rows] == [1, 5]
        assert len({row.client_key_hash for row in rows}) == 2
        assert all(
            len(row.client_key_hash) == 64
            and all(
                character in "0123456789abcdef"
                for character in row.client_key_hash
            )
            for row in rows
        )
    finally:
        if schema_is_ready:
            with engine.begin() as connection:
                db.metadata.drop_all(connection)
        engine.dispose()


def test_valid_login_does_not_bypass_limit_after_stale_precheck(tmp_path):
    engine = create_engine(
        POSTGRESQL_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )
    repository_root = Path(__file__).resolve().parent
    result_path = tmp_path / "stale-precheck-result.json"
    lock_identity_path = tmp_path / "lock-identity.json"
    start_marker = tmp_path / "start-valid-login"
    credential_marker = tmp_path / "credential-check-started"
    application_name = "admin-login-rate-limit-race-test"
    worker = None
    lock_connection = None
    lock_transaction = None
    schema_is_ready = False

    try:
        with engine.begin() as connection:
            db.metadata.drop_all(connection)
            db.metadata.create_all(connection)
        schema_is_ready = True

        worker = subprocess.Popen(
            [sys.executable, "-c", STALE_PRECHECK_WORKER_CODE],
            cwd=repository_root,
            env=_worker_environment(
                client_address="192.0.2.81",
                result_path=result_path,
                application_name=application_name,
                lock_identity_path=lock_identity_path,
                start_marker=start_marker,
                credential_marker=credential_marker,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _wait_for_file(
            lock_identity_path,
            worker,
            "login worker did not prepare its advisory lock identity",
        )
        lock_identity = json.loads(
            lock_identity_path.read_text(encoding="utf-8")
        )

        lock_connection = engine.connect()
        lock_transaction = lock_connection.begin()
        lock_connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_identity["advisory_lock_key"]},
        )

        start_marker.touch()
        _wait_for_postgresql_advisory_lock(
            engine,
            application_name,
            worker,
            credential_marker,
        )
        assert credential_marker.exists() is False

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO guest_creation_rate_limits ("
                    "client_key_hash, window_started_at, "
                    "request_count, updated_at"
                    ") VALUES ("
                    ":client_key_hash, CURRENT_TIMESTAMP, 5, "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "client_key_hash": lock_identity[
                        "client_key_hash"
                    ],
                },
            )

        assert credential_marker.exists() is False
        lock_transaction.commit()

        worker_return_code = worker.wait(timeout=30)
        assert worker_return_code == 0
        result = json.loads(result_path.read_text(encoding="utf-8"))

        with engine.connect() as connection:
            request_count = connection.execute(
                text(
                    "SELECT request_count "
                    "FROM guest_creation_rate_limits"
                )
            ).scalar_one()

        assert request_count == 5
        assert result["status"] == 429
        assert result["authenticated"] is False
        assert result["credential_check_count"] == 0
        assert credential_marker.exists() is False
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=10)
        if lock_transaction is not None and lock_transaction.is_active:
            lock_transaction.rollback()
        if lock_connection is not None:
            lock_connection.close()
        if schema_is_ready:
            with engine.begin() as connection:
                db.metadata.drop_all(connection)
        engine.dispose()
