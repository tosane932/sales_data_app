import datetime
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from flask import Flask
from flask_migrate import Migrate, downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from models import db


PROJECT_ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PREVIOUS_REVISION = "f2b6c8d4e1a9"
AI_USAGE_REVISION = "a8f3c1d5e7b9"


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


def _insert_guest_dataset():
    now = datetime.datetime(2026, 9, 3, 12, 0, tzinfo=datetime.timezone.utc)
    guest_id = uuid.uuid4()
    datasets = sa.table(
        "datasets",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("kind", sa.String(length=16)),
        sa.column("system_key", sa.String(length=100)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("last_activity_at", sa.DateTime(timezone=True)),
        sa.column("absolute_expires_at", sa.DateTime(timezone=True)),
    )
    db.session.execute(
        datasets.insert().values(
            id=guest_id,
            kind="guest",
            system_key=None,
            created_at=now,
            last_activity_at=now,
            absolute_expires_at=now + datetime.timedelta(hours=2),
        )
    )
    db.session.commit()
    return guest_id


def _upgrade_to_ai_usage_revision():
    db.session.remove()
    upgrade(
        directory=str(MIGRATIONS_DIR),
        revision=AI_USAGE_REVISION,
    )


def test_ai_usage_migration_backfills_existing_datasets_to_zero(tmp_path):
    migration_app = _migration_app(tmp_path, "ai_usage_backfill")

    with migration_app.app_context():
        upgrade(
            directory=str(MIGRATIONS_DIR),
            revision=PREVIOUS_REVISION,
        )
        guest_id = _insert_guest_dataset()
        dataset_count_before = db.session.execute(
            text("SELECT COUNT(*) FROM datasets")
        ).scalar_one()

        _upgrade_to_ai_usage_revision()

        rows = db.session.execute(
            text(
                """
                SELECT id, kind, guest_ai_usage_count
                FROM datasets
                ORDER BY kind, id
                """
            )
        ).all()
        assert len(rows) == dataset_count_before
        assert all(row.guest_ai_usage_count == 0 for row in rows)
        assert any(
            row.kind == "guest"
            and str(row.id).replace("-", "") == guest_id.hex
            for row in rows
        )

        columns = {
            column["name"]: column
            for column in inspect(db.engine).get_columns("datasets")
        }
        assert columns["guest_ai_usage_count"]["nullable"] is False
        check_names = {
            constraint["name"]
            for constraint in inspect(db.engine).get_check_constraints(
                "datasets"
            )
        }
        assert "ck_datasets_guest_ai_usage_count" in check_names


@pytest.mark.parametrize("invalid_count", [-1, 4])
def test_ai_usage_migration_rejects_out_of_range_guest_count(
    tmp_path,
    invalid_count,
):
    migration_app = _migration_app(
        tmp_path,
        f"ai_usage_invalid_{invalid_count}",
    )

    with migration_app.app_context():
        _upgrade_to_ai_usage_revision()
        _insert_guest_dataset()

        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    """
                    UPDATE datasets
                    SET guest_ai_usage_count = :invalid_count
                    WHERE kind = 'guest'
                    """
                ),
                {"invalid_count": invalid_count},
            )
            db.session.commit()

        db.session.rollback()


def test_ai_usage_migration_requires_admin_count_to_remain_zero(tmp_path):
    migration_app = _migration_app(tmp_path, "ai_usage_admin_zero")

    with migration_app.app_context():
        _upgrade_to_ai_usage_revision()

        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    """
                    UPDATE datasets
                    SET guest_ai_usage_count = 1
                    WHERE kind = 'admin' AND system_key = 'admin'
                    """
                )
            )
            db.session.commit()

        db.session.rollback()


def test_ai_usage_migration_preserves_products_and_daily_sales(tmp_path):
    migration_app = _migration_app(tmp_path, "ai_usage_preserves_data")

    with migration_app.app_context():
        upgrade(
            directory=str(MIGRATIONS_DIR),
            revision=PREVIOUS_REVISION,
        )
        admin_id = db.session.execute(
            text(
                """
                SELECT id
                FROM datasets
                WHERE kind = 'admin' AND system_key = 'admin'
                """
            )
        ).scalar_one()
        db.session.execute(
            text(
                """
                INSERT INTO products
                    (id, dataset_id, year, month, name, price, is_active)
                VALUES
                    (701, :dataset_id, 2026, 9, '移行保持商品', 420, 1)
                """
            ),
            {"dataset_id": admin_id},
        )
        db.session.execute(
            text(
                """
                INSERT INTO daily_sales
                    (id, product_id, date, quantity)
                VALUES
                    (801, 701, '2026-09-03', 17)
                """
            )
        )
        db.session.commit()
        product_before = db.session.execute(
            text("SELECT * FROM products WHERE id = 701")
        ).one()
        sale_before = db.session.execute(
            text("SELECT * FROM daily_sales WHERE id = 801")
        ).one()

        _upgrade_to_ai_usage_revision()

        product_after = db.session.execute(
            text("SELECT * FROM products WHERE id = 701")
        ).one()
        sale_after = db.session.execute(
            text("SELECT * FROM daily_sales WHERE id = 801")
        ).one()
        assert tuple(product_after) == tuple(product_before)
        assert tuple(sale_after) == tuple(sale_before)


def test_ai_usage_migration_downgrade_removes_only_usage_column(tmp_path):
    migration_app = _migration_app(tmp_path, "ai_usage_downgrade")

    with migration_app.app_context():
        _upgrade_to_ai_usage_revision()
        table_names_before = set(inspect(db.engine).get_table_names())
        assert "guest_ai_usage_count" in {
            column["name"]
            for column in inspect(db.engine).get_columns("datasets")
        }

        db.session.remove()
        downgrade(
            directory=str(MIGRATIONS_DIR),
            revision=PREVIOUS_REVISION,
        )

        inspector = inspect(db.engine)
        assert set(inspector.get_table_names()) == table_names_before
        assert "guest_ai_usage_count" not in {
            column["name"]
            for column in inspector.get_columns("datasets")
        }
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == PREVIOUS_REVISION
