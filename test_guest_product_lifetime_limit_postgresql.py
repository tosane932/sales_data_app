import datetime
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import create_engine, text

from models import Dataset, Product, db


POSTGRESQL_TEST_DATABASE_URL = os.environ.get(
    "TEST_POSTGRESQL_DATABASE_URL"
)

pytestmark = pytest.mark.skipif(
    not POSTGRESQL_TEST_DATABASE_URL,
    reason="isolated PostgreSQL URL is not configured",
)


WORKER_CODE = r"""
import os
import sys

import app as app_module


app_module.app.config["WTF_CSRF_ENABLED"] = False

guest_dataset_id = os.environ["TEST_GUEST_DATASET_ID"]
product_name = os.environ["TEST_PRODUCT_NAME"]

client = app_module.app.test_client()

with client.session_transaction() as session_data:
    session_data["_user_id"] = f"guest:{guest_dataset_id}"
    session_data["_fresh"] = True

response = client.post(
    "/",
    data={
        "year": "2040",
        "month": "1",
        "product_id": [""],
        "prod_name": [product_name],
        "prod_price": ["100"],
    },
)

if response.status_code == 200:
    sys.exit(0)

if response.status_code == 400:
    sys.exit(3)

sys.exit(9)
"""


def _worker_environment(*, guest_dataset_id, product_name):
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=POSTGRESQL_TEST_DATABASE_URL,
        SECRET_KEY="isolated-integration-test-only",
        TEST_GUEST_DATASET_ID=str(guest_dataset_id),
        TEST_PRODUCT_NAME=product_name,
    )
    return environment


def test_concurrent_product_posts_never_exceed_guest_lifetime_limit():
    engine = create_engine(
        POSTGRESQL_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )
    repository_root = Path(__file__).resolve().parent
    guest_dataset_id = uuid.uuid4()
    first_process = None
    second_process = None
    schema_is_ready = False

    try:
        with engine.begin() as connection:
            db.metadata.drop_all(connection)
            db.metadata.create_all(connection)

            now = datetime.datetime.now(datetime.timezone.utc)

            connection.execute(
                Dataset.__table__.insert(),
                {
                    "id": guest_dataset_id,
                    "kind": "guest",
                    "system_key": None,
                    "created_at": now,
                    "last_activity_at": now,
                    "absolute_expires_at": (
                        now + datetime.timedelta(hours=2)
                    ),
                    "ai_usage_count": 0,
                },
            )

            connection.execute(
                Product.__table__.insert(),
                [
                    {
                        "dataset_id": guest_dataset_id,
                        "year": 2039,
                        "month": 12,
                        "name": f"既存商品{index}",
                        "price": 100,
                        "is_active": True,
                    }
                    for index in range(29)
                ],
            )

        schema_is_ready = True

        # 親transactionでGuest Dataset rowを一時的にlockする。
        # その間に2つのworkerを開始し、両方を同じrow lockへ待たせる。
        with engine.connect() as lock_connection:
            transaction = lock_connection.begin()

            lock_connection.execute(
                text(
                    "SELECT id FROM datasets "
                    "WHERE id = :dataset_id "
                    "FOR UPDATE"
                ),
                {"dataset_id": guest_dataset_id},
            )

            first_process = subprocess.Popen(
                [sys.executable, "-c", WORKER_CODE],
                cwd=repository_root,
                env=_worker_environment(
                    guest_dataset_id=guest_dataset_id,
                    product_name="同時追加A",
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            second_process = subprocess.Popen(
                [sys.executable, "-c", WORKER_CODE],
                cwd=repository_root,
                env=_worker_environment(
                    guest_dataset_id=guest_dataset_id,
                    product_name="同時追加B",
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # 両workerが起動する時間を与えてから親lockを解放する。
            time.sleep(1)
            transaction.commit()

        first_return_code = first_process.wait(timeout=20)
        second_return_code = second_process.wait(timeout=20)

        assert sorted([
            first_return_code,
            second_return_code,
        ]) == [0, 3]

        with engine.connect() as connection:
            product_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM products "
                    "WHERE dataset_id = :dataset_id"
                ),
                {"dataset_id": guest_dataset_id},
            ).scalar_one()

            new_product_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM products "
                    "WHERE dataset_id = :dataset_id "
                    "AND year = 2040 "
                    "AND month = 1"
                ),
                {"dataset_id": guest_dataset_id},
            ).scalar_one()

        assert product_count == 30
        assert new_product_count == 1

    finally:
        for process in (first_process, second_process):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

        if schema_is_ready:
            with engine.begin() as connection:
                db.metadata.drop_all(connection)

        engine.dispose()