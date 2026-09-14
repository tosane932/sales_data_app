import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import create_engine, text

from models import Dataset, ShopMemo, db
from postgresql_test_utils import get_isolated_postgresql_test_url


POSTGRESQL_TEST_DATABASE_URL = get_isolated_postgresql_test_url()

pytestmark = pytest.mark.skipif(
    not POSTGRESQL_TEST_DATABASE_URL,
    reason="isolated PostgreSQL URL is not configured",
)


WORKER_CODE = r"""
import concurrent.futures
import json
import os
from pathlib import Path
import threading
import time

from sqlalchemy import event

import app as app_module


app_module.app.config["WTF_CSRF_ENABLED"] = False

guest_dataset_id = os.environ["TEST_GUEST_DATASET_ID"]
lock_acquired_marker = Path(os.environ["TEST_LOCK_ACQUIRED_MARKER"])
lock_release_marker = Path(os.environ["TEST_LOCK_RELEASE_MARKER"])
result_path = Path(os.environ["TEST_RESULT_PATH"])
memo_bodies = ["同時追加メモA", "同時追加メモB"]

real_user_loader = app_module.login_manager._user_callback
authenticated_requests_ready = threading.Barrier(2)


def synchronized_user_loader(user_id):
    user = real_user_loader(user_id)
    authenticated_requests_ready.wait(timeout=15)
    return user


app_module.login_manager._user_callback = synchronized_user_loader

first_lock_guard = threading.Lock()
first_lock_is_held = False


def hold_first_memo_admission_lock(
    connection,
    cursor,
    statement,
    parameters,
    context,
    executemany,
):
    del connection, cursor, parameters, context, executemany
    normalized_statement = " ".join(statement.lower().split())
    if (
        "from datasets" not in normalized_statement
        or "for update" not in normalized_statement
    ):
        return

    global first_lock_is_held
    with first_lock_guard:
        if first_lock_is_held:
            return
        first_lock_is_held = True

    lock_acquired_marker.touch()
    deadline = time.monotonic() + 20
    while not lock_release_marker.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("shop memo lock release timed out")
        time.sleep(0.02)


with app_module.app.app_context():
    event.listen(
        app_module.db.engine,
        "after_cursor_execute",
        hold_first_memo_admission_lock,
    )


def submit_memo(body):
    client = app_module.app.test_client()
    with client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_dataset_id}"
        session_data["_fresh"] = True

    response = client.post(
        "/shop-tools/memo",
        data={"body": body},
        follow_redirects=False,
    )
    return response.status_code


try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(submit_memo, memo_bodies))
    result_path.write_text(json.dumps({"statuses": statuses}))
except Exception as error:
    result_path.write_text(json.dumps({"error_type": type(error).__name__}))
    raise
"""


def _dataset_values(dataset_id, *, kind, system_key, now):
    return {
        "id": dataset_id,
        "kind": kind,
        "system_key": system_key,
        "created_at": now,
        "last_activity_at": now,
        "absolute_expires_at": (
            None if kind == "admin" else now + datetime.timedelta(hours=2)
        ),
        "guest_ai_usage_count": 0,
    }


def _wait_for_marker(marker_path, process, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if process.poll() is not None:
            pytest.fail("Memo worker exited before acquiring the Dataset lock.")
        time.sleep(0.05)
    pytest.fail("Memo worker did not acquire the Dataset lock.")


def _wait_for_dataset_lock_waiter(engine, application_name, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM pg_stat_activity "
                    "WHERE application_name = :application_name "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%FROM datasets%' "
                    "AND query ILIKE '%FOR UPDATE%'"
                ),
                {"application_name": application_name},
            ).scalar_one()
        if waiting_count >= 1:
            return
        time.sleep(0.05)
    pytest.fail("The second memo request did not wait on the Dataset lock.")


def test_concurrent_memo_posts_never_exceed_dataset_limit(tmp_path):
    engine = create_engine(POSTGRESQL_TEST_DATABASE_URL, pool_pre_ping=True)
    repository_root = Path(__file__).resolve().parent.parent
    guest_a_id = uuid.uuid4()
    guest_b_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    application_name = f"shop-memo-limit-test-{uuid.uuid4()}"
    lock_acquired_marker = tmp_path / "memo-lock-acquired"
    lock_release_marker = tmp_path / "memo-lock-release"
    result_path = tmp_path / "memo-worker-result.json"
    worker_process = None
    schema_is_ready = False

    try:
        with engine.begin() as connection:
            db.metadata.drop_all(connection)
            db.metadata.create_all(connection)
            now = datetime.datetime.now(datetime.timezone.utc)
            connection.execute(
                Dataset.__table__.insert(),
                [
                    _dataset_values(
                        guest_a_id,
                        kind="guest",
                        system_key=None,
                        now=now,
                    ),
                    _dataset_values(
                        guest_b_id,
                        kind="guest",
                        system_key=None,
                        now=now,
                    ),
                    _dataset_values(
                        admin_id,
                        kind="admin",
                        system_key="admin",
                        now=now,
                    ),
                ],
            )
            existing_memos = [
                {
                    "dataset_id": guest_a_id,
                    "body": f"Guest A既存メモ{index}",
                    "deleted_at": now if index == 0 else None,
                }
                for index in range(99)
            ]
            existing_memos.extend(
                [
                    {"dataset_id": guest_b_id, "body": "Guest B保護メモ"},
                    {"dataset_id": admin_id, "body": "Admin保護メモ"},
                ]
            )
            connection.execute(ShopMemo.__table__.insert(), existing_memos)

        schema_is_ready = True
        environment = os.environ.copy()
        environment.update(
            DATABASE_URL=POSTGRESQL_TEST_DATABASE_URL,
            SECRET_KEY="isolated-integration-test-only",
            TEST_GUEST_DATASET_ID=str(guest_a_id),
            TEST_LOCK_ACQUIRED_MARKER=str(lock_acquired_marker),
            TEST_LOCK_RELEASE_MARKER=str(lock_release_marker),
            TEST_RESULT_PATH=str(result_path),
            PGAPPNAME=application_name,
        )
        worker_process = subprocess.Popen(
            [sys.executable, "-c", WORKER_CODE],
            cwd=repository_root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _wait_for_marker(lock_acquired_marker, worker_process)
        _wait_for_dataset_lock_waiter(engine, application_name)
        lock_release_marker.touch()

        assert worker_process.wait(timeout=30) == 0
        worker_result = json.loads(result_path.read_text())
        assert "error_type" not in worker_result
        assert sorted(worker_result["statuses"]) == [303, 400]

        with engine.connect() as connection:
            counts = {
                dataset_id: connection.execute(
                    text(
                        "SELECT COUNT(*) FROM shop_memos "
                        "WHERE dataset_id = :dataset_id"
                    ),
                    {"dataset_id": dataset_id},
                ).scalar_one()
                for dataset_id in (guest_a_id, guest_b_id, admin_id)
            }
            new_guest_a_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM shop_memos "
                    "WHERE dataset_id = :dataset_id "
                    "AND body IN ('同時追加メモA', '同時追加メモB')"
                ),
                {"dataset_id": guest_a_id},
            ).scalar_one()

        assert counts == {
            guest_a_id: 100,
            guest_b_id: 1,
            admin_id: 1,
        }
        assert new_guest_a_count == 1
    finally:
        lock_release_marker.touch(exist_ok=True)
        if worker_process is not None and worker_process.poll() is None:
            worker_process.terminate()
            worker_process.wait(timeout=10)
        if schema_is_ready:
            with engine.begin() as connection:
                db.metadata.drop_all(connection)
        engine.dispose()
