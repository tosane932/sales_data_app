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

from models import Dataset, MaterialOrderItem, db
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
item_names = ["同時追加材料A", "同時追加材料B"]

real_user_loader = app_module.login_manager._user_callback
authenticated_requests_ready = threading.Barrier(2)


def synchronized_user_loader(user_id):
    user = real_user_loader(user_id)
    authenticated_requests_ready.wait(timeout=15)
    return user


app_module.login_manager._user_callback = synchronized_user_loader

first_lock_guard = threading.Lock()
first_lock_is_held = False


def hold_first_material_order_admission_lock(
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
            raise TimeoutError("material order lock release timed out")
        time.sleep(0.02)


with app_module.app.app_context():
    event.listen(
        app_module.db.engine,
        "after_cursor_execute",
        hold_first_material_order_admission_lock,
    )


def submit_item(item_name):
    client = app_module.app.test_client()
    with client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_dataset_id}"
        session_data["_fresh"] = True

    response = client.post(
        "/material-orders",
        data={"name": item_name},
        follow_redirects=False,
    )

    with client.session_transaction() as session_data:
        restored_identity = session_data.get("_user_id")

    return {
        "status_code": response.status_code,
        "restored_identity": restored_identity,
    }


try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit_item, item_names))
    result_path.write_text(json.dumps({"results": results}))
except Exception as error:
    result_path.write_text(json.dumps({"error_type": type(error).__name__}))
    raise
"""


COMPLETION_WORKER_CODE = r"""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import threading
import time

from sqlalchemy import event

import app as app_module
import material_orders as material_orders_module


app_module.app.config["WTF_CSRF_ENABLED"] = False

guest_dataset_id = os.environ["TEST_GUEST_DATASET_ID"]
item_id = os.environ["TEST_MATERIAL_ORDER_ITEM_ID"]
operations = json.loads(os.environ["TEST_MATERIAL_ORDER_OPERATIONS"])
lock_acquired_marker = Path(os.environ["TEST_LOCK_ACQUIRED_MARKER"])
lock_release_marker = Path(os.environ["TEST_LOCK_RELEASE_MARKER"])
result_path = Path(os.environ["TEST_RESULT_PATH"])

real_user_loader = app_module.login_manager._user_callback
authenticated_requests_ready = threading.Barrier(2)


def synchronized_user_loader(user_id):
    user = real_user_loader(user_id)
    authenticated_requests_ready.wait(timeout=15)
    return user


app_module.login_manager._user_callback = synchronized_user_loader

request_state = threading.local()
lock_order = []
lock_order_guard = threading.Lock()
first_lock_is_held = False
utc_now_call_count = 0
utc_now_guard = threading.Lock()


def deterministic_utc_now():
    global utc_now_call_count
    with utc_now_guard:
        utc_now_call_count += 1
        call_number = utc_now_call_count
    return datetime.datetime(
        2026,
        9,
        13,
        12,
        0,
        call_number,
        tzinfo=datetime.timezone.utc,
    )


material_orders_module.utc_now = deterministic_utc_now


def hold_first_material_order_item_lock(
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
        "from material_order_items" not in normalized_statement
        or "for update" not in normalized_statement
    ):
        return

    operation = request_state.operation
    global first_lock_is_held
    with lock_order_guard:
        lock_order.append(operation)
        if first_lock_is_held:
            return
        first_lock_is_held = True

    lock_acquired_marker.touch()
    deadline = time.monotonic() + 20
    while not lock_release_marker.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("material order item lock release timed out")
        time.sleep(0.02)


with app_module.app.app_context():
    event.listen(
        app_module.db.engine,
        "after_cursor_execute",
        hold_first_material_order_item_lock,
    )


def submit_operation(operation):
    request_state.operation = operation
    client = app_module.app.test_client()
    with client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_dataset_id}"
        session_data["_fresh"] = True

    if operation == "delete":
        path = f"/material-orders/{item_id}/delete"
        data = {}
    else:
        path = f"/material-orders/{item_id}/completion"
        data = {"completed": "1" if operation == "complete" else "0"}

    response = client.post(path, data=data, follow_redirects=False)
    return {
        "operation": operation,
        "status_code": response.status_code,
    }


try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit_operation, operations))
    result_path.write_text(
        json.dumps(
            {
                "results": results,
                "lock_order": lock_order,
                "utc_now_call_count": utc_now_call_count,
            }
        )
    )
except Exception as error:
    result_path.write_text(json.dumps({"error_type": type(error).__name__}))
    raise
"""


def _worker_environment(
    *,
    guest_dataset_id,
    lock_acquired_marker,
    lock_release_marker,
    result_path,
    application_name,
):
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=POSTGRESQL_TEST_DATABASE_URL,
        SECRET_KEY="isolated-integration-test-only",
        TEST_GUEST_DATASET_ID=str(guest_dataset_id),
        TEST_LOCK_ACQUIRED_MARKER=str(lock_acquired_marker),
        TEST_LOCK_RELEASE_MARKER=str(lock_release_marker),
        TEST_RESULT_PATH=str(result_path),
        PGAPPNAME=application_name,
    )
    return environment


def _wait_for_marker(
    marker_path,
    process,
    timeout_seconds=20,
    lock_description="Dataset admission",
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if process.poll() is not None:
            pytest.fail(
                "Material order concurrency worker exited before acquiring "
                f"the {lock_description} lock."
            )
        time.sleep(0.05)
    pytest.fail(
        "Material order concurrency worker did not acquire the "
        f"{lock_description} lock."
    )


def _wait_for_dataset_lock_waiter(
    engine,
    application_name,
    timeout_seconds=20,
):
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
    pytest.fail(
        "The second material order request was not observed waiting on "
        "the Dataset FOR UPDATE lock."
    )


def _wait_for_material_order_item_lock_waiter(
    engine,
    application_name,
    timeout_seconds=20,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM pg_stat_activity "
                    "WHERE application_name = :application_name "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%material_order_items%'"
                ),
                {"application_name": application_name},
            ).scalar_one()
        if waiting_count >= 1:
            return
        time.sleep(0.05)
    pytest.fail(
        "The second material order operation was not observed waiting on "
        "the MaterialOrderItem row lock."
    )


def _dataset_values(dataset_id, *, kind, system_key, now):
    return {
        "id": dataset_id,
        "kind": kind,
        "system_key": system_key,
        "created_at": now,
        "last_activity_at": now,
        "absolute_expires_at": (
            None
            if kind == "admin"
            else now + datetime.timedelta(hours=2)
        ),
        "guest_ai_usage_count": 0,
    }


def _completion_worker_environment(
    *,
    guest_dataset_id,
    item_id,
    operations,
    lock_acquired_marker,
    lock_release_marker,
    result_path,
    application_name,
):
    environment = _worker_environment(
        guest_dataset_id=guest_dataset_id,
        lock_acquired_marker=lock_acquired_marker,
        lock_release_marker=lock_release_marker,
        result_path=result_path,
        application_name=application_name,
    )
    environment.update(
        TEST_MATERIAL_ORDER_ITEM_ID=str(item_id),
        TEST_MATERIAL_ORDER_OPERATIONS=json.dumps(operations),
    )
    return environment


def _run_concurrent_item_operations(tmp_path, operations):
    engine = create_engine(
        POSTGRESQL_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )
    repository_root = Path(__file__).resolve().parent.parent
    guest_id = uuid.uuid4()
    application_name = f"mo-completion-{uuid.uuid4()}"
    lock_acquired_marker = tmp_path / "material-item-lock-acquired"
    lock_release_marker = tmp_path / "material-item-lock-release"
    result_path = tmp_path / "completion-worker-result.json"
    worker_process = None
    schema_is_ready = False

    try:
        with engine.begin() as connection:
            db.metadata.drop_all(connection)
            db.metadata.create_all(connection)
            now = datetime.datetime.now(datetime.timezone.utc)
            connection.execute(
                Dataset.__table__.insert(),
                _dataset_values(
                    guest_id,
                    kind="guest",
                    system_key=None,
                    now=now,
                ),
            )
            item_id = connection.execute(
                MaterialOrderItem.__table__.insert()
                .values(dataset_id=guest_id, name="並行状態更新材料")
                .returning(MaterialOrderItem.id)
            ).scalar_one()

        schema_is_ready = True
        worker_process = subprocess.Popen(
            [sys.executable, "-c", COMPLETION_WORKER_CODE],
            cwd=repository_root,
            env=_completion_worker_environment(
                guest_dataset_id=guest_id,
                item_id=item_id,
                operations=operations,
                lock_acquired_marker=lock_acquired_marker,
                lock_release_marker=lock_release_marker,
                result_path=result_path,
                application_name=application_name,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _wait_for_marker(
            lock_acquired_marker,
            worker_process,
            lock_description="MaterialOrderItem row",
        )
        _wait_for_material_order_item_lock_waiter(engine, application_name)
        lock_release_marker.touch()

        assert worker_process.wait(timeout=30) == 0
        worker_result = json.loads(result_path.read_text())
        assert "error_type" not in worker_result

        with engine.connect() as connection:
            item_state = connection.execute(
                text(
                    "SELECT is_completed, completed_at "
                    "FROM material_order_items WHERE id = :item_id"
                ),
                {"item_id": item_id},
            ).one_or_none()
            inconsistent_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM material_order_items "
                    "WHERE (is_completed = false AND completed_at IS NOT NULL) "
                    "OR (is_completed = true AND completed_at IS NULL)"
                )
            ).scalar_one()

        assert inconsistent_count == 0
        return worker_result, item_state
    finally:
        lock_release_marker.touch(exist_ok=True)
        if worker_process is not None and worker_process.poll() is None:
            worker_process.terminate()
            worker_process.wait(timeout=10)

        if schema_is_ready:
            with engine.begin() as connection:
                db.metadata.drop_all(connection)

        engine.dispose()


def test_concurrent_completion_posts_preserve_the_first_completed_at(tmp_path):
    worker_result, item_state = _run_concurrent_item_operations(
        tmp_path,
        ["complete", "complete"],
    )

    assert sorted(
        result["status_code"] for result in worker_result["results"]
    ) == [303, 303]
    assert worker_result["lock_order"] == ["complete", "complete"]
    assert worker_result["utc_now_call_count"] == 1
    assert item_state.is_completed is True
    assert item_state.completed_at is not None


def test_concurrent_completion_and_reopen_follow_item_lock_order(tmp_path):
    worker_result, item_state = _run_concurrent_item_operations(
        tmp_path,
        ["complete", "reopen"],
    )

    assert sorted(
        result["status_code"] for result in worker_result["results"]
    ) == [303, 303]
    assert sorted(worker_result["lock_order"]) == ["complete", "reopen"]
    final_operation = worker_result["lock_order"][-1]
    assert item_state.is_completed is (final_operation == "complete")
    assert (item_state.completed_at is not None) is (
        final_operation == "complete"
    )


def test_concurrent_completion_and_delete_finish_without_server_error(tmp_path):
    worker_result, item_state = _run_concurrent_item_operations(
        tmp_path,
        ["complete", "delete"],
    )

    status_by_operation = {
        result["operation"]: result["status_code"]
        for result in worker_result["results"]
    }
    assert status_by_operation["delete"] == 303
    assert status_by_operation["complete"] in {303, 404}
    assert sorted(worker_result["lock_order"]) == ["complete", "delete"]
    assert item_state is None


def test_concurrent_material_order_posts_never_exceed_dataset_limit(tmp_path):
    engine = create_engine(
        POSTGRESQL_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )
    repository_root = Path(__file__).resolve().parent.parent
    guest_a_id = uuid.uuid4()
    guest_b_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    application_name = f"material-order-limit-test-{uuid.uuid4()}"
    lock_acquired_marker = tmp_path / "material-order-lock-acquired"
    lock_release_marker = tmp_path / "material-order-lock-release"
    result_path = tmp_path / "worker-result.json"
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
            existing_items = [
                {
                    "dataset_id": guest_a_id,
                    "name": f"Guest A既存材料{index}",
                }
                for index in range(99)
            ]
            existing_items.extend(
                [
                    {
                        "dataset_id": guest_b_id,
                        "name": "Guest B保護材料",
                    },
                    {
                        "dataset_id": admin_id,
                        "name": "Admin保護材料",
                    },
                ]
            )
            connection.execute(
                MaterialOrderItem.__table__.insert(),
                existing_items,
            )

        schema_is_ready = True
        worker_process = subprocess.Popen(
            [sys.executable, "-c", WORKER_CODE],
            cwd=repository_root,
            env=_worker_environment(
                guest_dataset_id=guest_a_id,
                lock_acquired_marker=lock_acquired_marker,
                lock_release_marker=lock_release_marker,
                result_path=result_path,
                application_name=application_name,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _wait_for_marker(lock_acquired_marker, worker_process)
        _wait_for_dataset_lock_waiter(engine, application_name)
        lock_release_marker.touch()

        assert worker_process.wait(timeout=30) == 0
        worker_result = json.loads(result_path.read_text())
        assert "error_type" not in worker_result
        assert sorted(
            result["status_code"] for result in worker_result["results"]
        ) == [303, 400]
        assert all(
            result["restored_identity"] == f"guest:{guest_a_id}"
            for result in worker_result["results"]
        )

        with engine.connect() as connection:
            counts = {
                dataset_id: connection.execute(
                    text(
                        "SELECT COUNT(*) FROM material_order_items "
                        "WHERE dataset_id = :dataset_id"
                    ),
                    {"dataset_id": dataset_id},
                ).scalar_one()
                for dataset_id in (guest_a_id, guest_b_id, admin_id)
            }
            new_guest_a_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM material_order_items "
                    "WHERE dataset_id = :dataset_id "
                    "AND name IN ('同時追加材料A', '同時追加材料B')"
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
