from pathlib import Path
import datetime
import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import create_engine, inspect, text

from models import db
from postgresql_test_utils import get_isolated_postgresql_test_url


POSTGRESQL_TEST_DATABASE_URL = get_isolated_postgresql_test_url()

pytestmark = pytest.mark.skipif(
    not POSTGRESQL_TEST_DATABASE_URL,
    reason="isolated PostgreSQL URL is not configured",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PRE_DATASET_REVISION = "9d3c1b7e5a42"
PRE_MATERIAL_ORDER_REVISION = "e6b4c2d8f0a1"


def _reset_public_schema(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def test_postgresql_migrations_reach_head_and_preserve_existing_data():
    reset_engine = create_engine(
        POSTGRESQL_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )
    _reset_public_schema(reset_engine)

    migration_app = Flask("postgresql_migration_test")
    migration_app.config.update(
        SQLALCHEMY_DATABASE_URI=POSTGRESQL_TEST_DATABASE_URL,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR))

    alembic_config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    alembic_config.set_main_option(
        "script_location",
        str(MIGRATIONS_DIR),
    )
    expected_head = ScriptDirectory.from_config(
        alembic_config
    ).get_current_head()

    try:
        with migration_app.app_context():
            assert db.engine.url.get_backend_name() == "postgresql"
            assert inspect(db.engine).get_table_names() == []

            upgrade(
                directory=str(MIGRATIONS_DIR),
                revision=PRE_DATASET_REVISION,
            )
            db.session.execute(
                text(
                    "INSERT INTO products "
                    "(id, year, month, name, price, is_active) "
                    "VALUES (101, 2026, 8, '既存商品', 200, true)"
                )
            )
            db.session.execute(
                text(
                    "INSERT INTO daily_sales "
                    "(id, product_id, date, quantity) "
                    "VALUES (201, 101, '2026-08-19', 5)"
                )
            )
            db.session.commit()

            upgrade(
                directory=str(MIGRATIONS_DIR),
                revision=PRE_MATERIAL_ORDER_REVISION,
            )
            guest_dataset_id = uuid.uuid4()
            now = datetime.datetime(
                2026,
                9,
                13,
                3,
                0,
                tzinfo=datetime.timezone.utc,
            )
            db.session.execute(
                text(
                    "INSERT INTO datasets "
                    "(id, kind, system_key, created_at, last_activity_at, "
                    "absolute_expires_at, guest_ai_usage_count) "
                    "VALUES (:id, 'guest', NULL, :created_at, :activity_at, "
                    ":expires_at, 2)"
                ),
                {
                    "id": guest_dataset_id,
                    "created_at": now,
                    "activity_at": now,
                    "expires_at": now + datetime.timedelta(hours=2),
                },
            )
            db.session.execute(
                text(
                    "INSERT INTO guest_creation_rate_limits "
                    "(client_key_hash, window_started_at, request_count, "
                    "updated_at) "
                    "VALUES (:client_key_hash, :started_at, 2, :updated_at)"
                ),
                {
                    "client_key_hash": "a" * 64,
                    "started_at": now,
                    "updated_at": now,
                },
            )
            db.session.commit()

            upgrade(directory=str(MIGRATIONS_DIR), revision="head")

            inspector = inspect(db.engine)
            assert {
                "datasets",
                "products",
                "daily_sales",
                "guest_creation_rate_limits",
                "material_order_items",
                "alembic_version",
            }.issubset(inspector.get_table_names())
            assert db.session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head

            product = db.session.execute(
                text(
                    "SELECT id, dataset_id, name, price "
                    "FROM products WHERE id = 101"
                )
            ).one()
            sale = db.session.execute(
                text(
                    "SELECT id, product_id, date, quantity "
                    "FROM daily_sales WHERE id = 201"
                )
            ).one()

            assert product.id == 101
            assert product.dataset_id is not None
            assert product.name == "既存商品"
            assert product.price == 200
            assert sale.id == 201
            assert sale.product_id == 101
            assert sale.quantity == 5
            assert db.session.execute(
                text(
                    "SELECT COUNT(*) FROM datasets "
                    "WHERE id = :dataset_id "
                    "AND kind = 'admin' "
                    "AND system_key = 'admin'"
                ),
                {"dataset_id": product.dataset_id},
            ).scalar_one() == 1
            assert db.session.execute(
                text("SELECT COUNT(*) FROM products")
            ).scalar_one() == 1
            assert db.session.execute(
                text("SELECT COUNT(*) FROM daily_sales")
            ).scalar_one() == 1
            guest_dataset = db.session.execute(
                text(
                    "SELECT kind, system_key, guest_ai_usage_count "
                    "FROM datasets WHERE id = :dataset_id"
                ),
                {"dataset_id": guest_dataset_id},
            ).one()
            assert guest_dataset.kind == "guest"
            assert guest_dataset.system_key is None
            assert guest_dataset.guest_ai_usage_count == 2
            assert db.session.execute(
                text(
                    "SELECT request_count "
                    "FROM guest_creation_rate_limits "
                    "WHERE client_key_hash = :client_key_hash"
                ),
                {"client_key_hash": "a" * 64},
            ).scalar_one() == 2

            material_order_columns = {
                column["name"]: column
                for column in inspector.get_columns("material_order_items")
            }
            assert set(material_order_columns) == {
                "id",
                "dataset_id",
                "name",
                "quantity_text",
                "memo",
                "is_completed",
                "created_at",
                "completed_at",
            }
            material_order_foreign_keys = inspector.get_foreign_keys(
                "material_order_items"
            )
            assert len(material_order_foreign_keys) == 1
            assert material_order_foreign_keys[0]["constrained_columns"] == [
                "dataset_id"
            ]
            assert material_order_foreign_keys[0]["referred_table"] == (
                "datasets"
            )
            assert (
                material_order_foreign_keys[0]["options"]["ondelete"].upper()
                == "CASCADE"
            )
            assert {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "material_order_items"
                )
            } == {"ck_material_order_items_completion_timestamp"}
            assert any(
                index["name"]
                == "ix_material_order_items_dataset_status_created_id"
                and index["column_names"]
                == ["dataset_id", "is_completed", "created_at", "id"]
                and not index["unique"]
                for index in inspector.get_indexes("material_order_items")
            )

            material_order_item_id = db.session.execute(
                text(
                    "INSERT INTO material_order_items "
                    "(dataset_id, name, is_completed, created_at) "
                    "VALUES (:dataset_id, 'cascade確認材料', false, :now) "
                    "RETURNING id"
                ),
                {"dataset_id": guest_dataset_id, "now": now},
            ).scalar_one()
            db.session.commit()

            db.session.execute(
                text("DELETE FROM datasets WHERE id = :dataset_id"),
                {"dataset_id": guest_dataset_id},
            )
            db.session.commit()

            assert db.session.execute(
                text(
                    "SELECT COUNT(*) FROM material_order_items "
                    "WHERE id = :item_id"
                ),
                {"item_id": material_order_item_id},
            ).scalar_one() == 0
            assert db.session.execute(
                text("SELECT COUNT(*) FROM products")
            ).scalar_one() == 1
            assert db.session.execute(
                text("SELECT COUNT(*) FROM daily_sales")
            ).scalar_one() == 1

            db.session.remove()
            db.engine.dispose()
    finally:
        _reset_public_schema(reset_engine)
        reset_engine.dispose()
