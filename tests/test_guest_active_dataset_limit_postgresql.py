import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine, text

from models import db
from postgresql_test_utils import get_isolated_postgresql_test_url


POSTGRESQL_TEST_DATABASE_URL = get_isolated_postgresql_test_url()

pytestmark = pytest.mark.skipif(
    not POSTGRESQL_TEST_DATABASE_URL,
    reason="isolated PostgreSQL URL is not configured",
)


WORKER_CODE = r"""
import os
from pathlib import Path
import sys
import time

from werkzeug.exceptions import ServiceUnavailable

import app as app_module


marker_path = os.environ.get("GUEST_ADMISSION_PAUSE_MARKER")
before_lock_marker_path = os.environ.get(
    "GUEST_ADMISSION_BEFORE_LOCK_MARKER"
)
if before_lock_marker_path:
    real_admission_lock = app_module._acquire_guest_admission_lock

    def marking_admission_lock():
        Path(before_lock_marker_path).touch()
        return real_admission_lock()

    app_module._acquire_guest_admission_lock = marking_admission_lock

if marker_path:
    real_cleanup = app_module._cleanup_expired_guest_datasets

    def pausing_cleanup(*, now=None):
        Path(marker_path).touch()
        time.sleep(5)
        return real_cleanup(now=now)

    app_module._cleanup_expired_guest_datasets = pausing_cleanup

with app_module.app.test_request_context(
    "/",
    headers={"CF-Connecting-IP": os.environ["TEST_CLIENT_IP"]},
):
    try:
        app_module.start_guest_session()
    except ServiceUnavailable:
        sys.exit(3)

sys.exit(0)
"""


def _worker_environment(
    *,
    client_ip,
    marker_path=None,
    before_lock_marker_path=None,
):
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=POSTGRESQL_TEST_DATABASE_URL,
        SECRET_KEY="isolated-integration-test-only",
        GUEST_CREATION_RATE_LIMIT_MAX_ATTEMPTS="100",
        GUEST_CREATION_RATE_LIMIT_WINDOW_SECONDS="60",
        GUEST_ACTIVE_DATASET_LIMIT="1",
        TEST_CLIENT_IP=client_ip,
    )
    if marker_path is not None:
        environment["GUEST_ADMISSION_PAUSE_MARKER"] = str(marker_path)
    else:
        environment.pop("GUEST_ADMISSION_PAUSE_MARKER", None)
    if before_lock_marker_path is not None:
        environment["GUEST_ADMISSION_BEFORE_LOCK_MARKER"] = str(
            before_lock_marker_path
        )
    else:
        environment.pop("GUEST_ADMISSION_BEFORE_LOCK_MARKER", None)
    return environment


def _wait_for_marker(marker_path, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if process.poll() is not None:
            pytest.fail(
                "first admission exited before acquiring the admission lock"
            )
        time.sleep(0.05)
    pytest.fail("first admission did not acquire the admission lock in time")


def test_concurrent_guest_admissions_never_exceed_active_limit(tmp_path):
    engine = create_engine(POSTGRESQL_TEST_DATABASE_URL, pool_pre_ping=True)
    marker_path = tmp_path / "first-admission-holds-lock"
    before_lock_marker_path = tmp_path / "second-admission-reaches-lock"
    repository_root = Path(__file__).resolve().parent.parent
    first_process = None
    second_process = None
    schema_is_ready = False

    try:
        with engine.begin() as connection:
            db.metadata.drop_all(connection)
            db.metadata.create_all(connection)
        schema_is_ready = True

        first_process = subprocess.Popen(
            [sys.executable, "-c", WORKER_CODE],
            cwd=repository_root,
            env=_worker_environment(
                client_ip="192.0.2.1",
                marker_path=marker_path,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for_marker(marker_path, first_process)

        second_process = subprocess.Popen(
            [sys.executable, "-c", WORKER_CODE],
            cwd=repository_root,
            env=_worker_environment(
                client_ip="192.0.2.2",
                before_lock_marker_path=before_lock_marker_path,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _wait_for_marker(before_lock_marker_path, second_process)
        assert second_process.poll() is None

        first_return_code = first_process.wait(timeout=20)
        second_return_code = second_process.wait(timeout=20)

        assert first_return_code == 0
        assert second_return_code == 3

        with engine.connect() as connection:
            guest_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM datasets "
                    "WHERE kind = 'guest' AND system_key IS NULL"
                )
            ).scalar_one()

        assert guest_count == 1
    finally:
        for process in (first_process, second_process):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        if schema_is_ready:
            with engine.begin() as connection:
                db.metadata.drop_all(connection)
        engine.dispose()
