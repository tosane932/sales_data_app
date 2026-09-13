from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from models import db


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PRE_MATERIAL_ORDER_REVISION = "e6b4c2d8f0a1"


def test_empty_database_upgrades_from_base_to_head(tmp_path):
    database_path = tmp_path / "alembic_migration_test.sqlite"
    database_uri = f"sqlite:///{database_path}"

    migration_app = Flask("migration_test")
    migration_app.config.update(
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR))

    configured_url = make_url(
        migration_app.config["SQLALCHEMY_DATABASE_URI"]
    )
    assert configured_url.drivername == "sqlite"
    assert configured_url.drivername != "postgresql"
    assert Path(configured_url.database).resolve().is_relative_to(
        tmp_path.resolve()
    )
    assert Path(configured_url.database).resolve() == database_path.resolve()
    assert Path(configured_url.database).resolve() != (
        PROJECT_ROOT / "local.db"
    ).resolve()

    alembic_config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR))
    expected_head = ScriptDirectory.from_config(
        alembic_config
    ).get_current_head()

    with migration_app.app_context():
        assert db.engine.url == configured_url
        assert inspect(db.engine).get_table_names() == []

        upgrade(directory=str(MIGRATIONS_DIR), revision="head")

        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        assert {
            "datasets",
            "products",
            "daily_sales",
            "guest_creation_rate_limits",
            "material_order_items",
            "alembic_version",
        }.issubset(table_names)

        product_columns = {
            column["name"]
            for column in inspector.get_columns("products")
        }
        assert {
            "id",
            "year",
            "month",
            "name",
            "price",
            "is_active",
        }.issubset(product_columns)

        daily_sales_columns = {
            column["name"]
            for column in inspector.get_columns("daily_sales")
        }
        assert {
            "id",
            "product_id",
            "date",
            "quantity",
        }.issubset(daily_sales_columns)

        unique_constraints = inspector.get_unique_constraints("daily_sales")
        assert any(
            set(constraint["column_names"]) == {"product_id", "date"}
            for constraint in unique_constraints
        )

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
        assert material_order_columns["dataset_id"]["nullable"] is False
        assert material_order_columns["name"]["nullable"] is False
        assert material_order_columns["quantity_text"]["nullable"] is True
        assert material_order_columns["memo"]["nullable"] is True
        assert material_order_columns["is_completed"]["nullable"] is False
        assert material_order_columns["created_at"]["nullable"] is False
        assert material_order_columns["completed_at"]["nullable"] is True

        material_order_foreign_keys = inspector.get_foreign_keys(
            "material_order_items"
        )
        assert len(material_order_foreign_keys) == 1
        material_order_foreign_key = material_order_foreign_keys[0]
        assert material_order_foreign_key["name"] == (
            "fk_material_order_items_dataset_id_datasets"
        )
        assert material_order_foreign_key["constrained_columns"] == [
            "dataset_id"
        ]
        assert material_order_foreign_key["referred_table"] == "datasets"
        assert material_order_foreign_key["referred_columns"] == ["id"]
        assert (
            material_order_foreign_key["options"].get("ondelete", "").upper()
            == "CASCADE"
        )

        material_order_checks = inspector.get_check_constraints(
            "material_order_items"
        )
        assert {
            constraint["name"] for constraint in material_order_checks
        } == {"ck_material_order_items_completion_timestamp"}

        material_order_indexes = inspector.get_indexes("material_order_items")
        assert any(
            index["name"]
            == "ix_material_order_items_dataset_status_created_id"
            and index["column_names"]
            == ["dataset_id", "is_completed", "created_at", "id"]
            and not index["unique"]
            for index in material_order_indexes
        )

        current_revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert expected_head is not None
        assert current_revision == expected_head


def test_material_order_migration_preserves_existing_sqlite_data(tmp_path):
    database_path = tmp_path / "material_order_upgrade_test.sqlite"
    database_uri = f"sqlite:///{database_path}"
    migration_app = Flask("material_order_upgrade_test")
    migration_app.config.update(
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR))

    with migration_app.app_context():
        upgrade(
            directory=str(MIGRATIONS_DIR),
            revision=PRE_MATERIAL_ORDER_REVISION,
        )
        admin_dataset_id = db.session.execute(
            text("SELECT id FROM datasets WHERE system_key = 'admin'")
        ).scalar_one()
        guest_dataset_id = "11111111111111111111111111111111"
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
                "created_at": "2026-09-13 03:00:00",
                "activity_at": "2026-09-13 03:00:00",
                "expires_at": "2026-09-13 05:00:00",
            },
        )
        db.session.execute(
            text(
                "INSERT INTO products "
                "(id, dataset_id, year, month, name, price, is_active) "
                "VALUES (901, :dataset_id, 2026, 9, '既存商品', 250, 1)"
            ),
            {"dataset_id": admin_dataset_id},
        )
        db.session.execute(
            text(
                "INSERT INTO daily_sales "
                "(id, product_id, date, quantity) "
                "VALUES (902, 901, '2026-09-12', 7)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO guest_creation_rate_limits "
                "(client_key_hash, window_started_at, request_count, "
                "updated_at) "
                "VALUES (:client_key_hash, :started_at, 2, :updated_at)"
            ),
            {
                "client_key_hash": "b" * 64,
                "started_at": "2026-09-13 03:00:00",
                "updated_at": "2026-09-13 03:00:00",
            },
        )
        db.session.commit()

        upgrade(directory=str(MIGRATIONS_DIR), revision="head")

        assert db.session.execute(
            text("SELECT COUNT(*) FROM datasets")
        ).scalar_one() == 2
        assert db.session.execute(
            text(
                "SELECT kind, guest_ai_usage_count FROM datasets "
                "WHERE id = :id"
            ),
            {"id": guest_dataset_id},
        ).one() == ("guest", 2)
        assert db.session.execute(
            text("SELECT name, price FROM products WHERE id = 901")
        ).one() == ("既存商品", 250)
        assert db.session.execute(
            text("SELECT quantity FROM daily_sales WHERE id = 902")
        ).scalar_one() == 7
        assert db.session.execute(
            text(
                "SELECT request_count FROM guest_creation_rate_limits "
                "WHERE client_key_hash = :client_key_hash"
            ),
            {"client_key_hash": "b" * 64},
        ).scalar_one() == 2
        assert db.session.execute(
            text("SELECT COUNT(*) FROM material_order_items")
        ).scalar_one() == 0
