import datetime
import uuid
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import Migrate, downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from models import db


PROJECT_ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PREVIOUS_REVISION = "a8f3c1d5e7b9"
RATE_LIMIT_REVISION = "e6b4c2d8f0a1"


def _migration_app(tmp_path, name):
    database_path = tmp_path / f"{name}.sqlite"
    migration_app = Flask(name)
    migration_app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR))
    return migration_app


def _upgrade_to_rate_limit_revision():
    db.session.remove()
    upgrade(
        directory=str(MIGRATIONS_DIR),
        revision=RATE_LIMIT_REVISION,
    )


def test_rate_limit_migration_upgrade_creates_expected_table(tmp_path):
    migration_app = _migration_app(tmp_path, "rate_limit_upgrade")

    with migration_app.app_context():
        _upgrade_to_rate_limit_revision()
        inspector = inspect(db.engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "guest_creation_rate_limits"
            )
        }

        assert set(columns) == {
            "client_key_hash",
            "window_started_at",
            "request_count",
            "updated_at",
        }
        assert columns["client_key_hash"]["nullable"] is False
        assert columns["request_count"]["nullable"] is False
        assert inspect(db.engine).get_pk_constraint(
            "guest_creation_rate_limits"
        )["constrained_columns"] == ["client_key_hash"]
        assert inspect(db.engine).get_foreign_keys(
            "guest_creation_rate_limits"
        ) == []
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == RATE_LIMIT_REVISION


def test_rate_limit_migration_downgrade_removes_only_rate_limit_table(
    tmp_path,
):
    migration_app = _migration_app(tmp_path, "rate_limit_downgrade")

    with migration_app.app_context():
        _upgrade_to_rate_limit_revision()
        db.session.remove()
        downgrade(
            directory=str(MIGRATIONS_DIR),
            revision=PREVIOUS_REVISION,
        )

        table_names = set(inspect(db.engine).get_table_names())
        assert "guest_creation_rate_limits" not in table_names
        assert {"datasets", "products", "daily_sales"} <= table_names
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == PREVIOUS_REVISION


def test_rate_limit_migration_rejects_negative_request_count(tmp_path):
    migration_app = _migration_app(tmp_path, "rate_limit_constraint")

    with migration_app.app_context():
        _upgrade_to_rate_limit_revision()
        now = datetime.datetime(
            2026,
            9,
            3,
            12,
            0,
            tzinfo=datetime.timezone.utc,
        )

        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    """
                    INSERT INTO guest_creation_rate_limits
                        (client_key_hash, window_started_at,
                         request_count, updated_at)
                    VALUES
                        (:client_key_hash, :window_started_at,
                         -1, :updated_at)
                    """
                ),
                {
                    "client_key_hash": "a" * 64,
                    "window_started_at": now.replace(
                        tzinfo=None
                    ).isoformat(sep=" "),
                    "updated_at": now.replace(
                        tzinfo=None
                    ).isoformat(sep=" "),
                },
            )
            db.session.commit()

        db.session.rollback()


def test_rate_limit_migration_preserves_business_data(tmp_path):
    migration_app = _migration_app(tmp_path, "rate_limit_data")

    with migration_app.app_context():
        upgrade(
            directory=str(MIGRATIONS_DIR),
            revision=PREVIOUS_REVISION,
        )
        admin_id = db.session.execute(
            text(
                """
                SELECT id FROM datasets
                WHERE kind = 'admin' AND system_key = 'admin'
                """
            )
        ).scalar_one()
        guest_id = uuid.uuid4()
        now = datetime.datetime(
            2026,
            9,
            3,
            12,
            0,
            tzinfo=datetime.timezone.utc,
        )
        db.session.execute(
            text(
                """
                INSERT INTO datasets
                    (id, kind, system_key, created_at, last_activity_at,
                     absolute_expires_at, guest_ai_usage_count)
                VALUES
                    (:id, 'guest', NULL, :now, :now, :expires_at, 2)
                """
            ),
            {
                "id": guest_id.hex,
                "now": now.replace(tzinfo=None).isoformat(sep=" "),
                "expires_at": (
                    now + datetime.timedelta(hours=2)
                ).replace(tzinfo=None).isoformat(sep=" "),
            },
        )
        db.session.execute(
            text(
                """
                INSERT INTO products
                    (id, dataset_id, year, month, name, price, is_active)
                VALUES
                    (901, :dataset_id, 2026, 9, '保持確認商品', 530, 1)
                """
            ),
            {"dataset_id": guest_id.hex},
        )
        db.session.execute(
            text(
                """
                INSERT INTO daily_sales
                    (id, product_id, date, quantity)
                VALUES
                    (902, 901, '2026-09-03', 19)
                """
            )
        )
        db.session.commit()
        dataset_before = db.session.execute(
            text("SELECT * FROM datasets WHERE id = :id"),
            {"id": guest_id.hex},
        ).one()
        product_before = db.session.execute(
            text("SELECT * FROM products WHERE id = 901")
        ).one()
        sale_before = db.session.execute(
            text("SELECT * FROM daily_sales WHERE id = 902")
        ).one()

        _upgrade_to_rate_limit_revision()

        dataset_after = db.session.execute(
            text("SELECT * FROM datasets WHERE id = :id"),
            {"id": guest_id.hex},
        ).one()
        product_after = db.session.execute(
            text("SELECT * FROM products WHERE id = 901")
        ).one()
        sale_after = db.session.execute(
            text("SELECT * FROM daily_sales WHERE id = 902")
        ).one()
        assert tuple(dataset_after) == tuple(dataset_before)
        assert tuple(product_after) == tuple(product_before)
        assert tuple(sale_after) == tuple(sale_before)
        assert str(admin_id)
