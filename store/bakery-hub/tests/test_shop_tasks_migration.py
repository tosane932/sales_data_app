import datetime
from pathlib import Path

from flask import Flask
from flask_migrate import Migrate, downgrade, upgrade
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from models import db


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PRE_SHOP_TASK_REVISION = "a14c5e7d9b20"
SHOP_TASK_REVISION = "b3f6d8a1c2e4"
SHOP_TASK_DIRECT_UX_REVISION = "f8c1d2e3a4b5"


def _migration_app(database_uri):
    migration_app = Flask("shop_task_migration_test")
    migration_app.config.update(
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR))
    return migration_app


def test_shop_task_migration_upgrades_constraints_and_downgrades_sqlite(
    tmp_path,
):
    database_path = tmp_path / "shop_task_migration.sqlite"
    migration_app = _migration_app(f"sqlite:///{database_path}")
    now = datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc)

    with migration_app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision=PRE_SHOP_TASK_REVISION)
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys = ON"))
        assert db.session.execute(
            text("PRAGMA foreign_keys")
        ).scalar_one() == 1
        dataset_id = db.session.execute(
            text("SELECT id FROM datasets WHERE system_key = 'admin'")
        ).scalar_one()

        upgrade(directory=str(MIGRATIONS_DIR), revision=SHOP_TASK_REVISION)
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys = ON"))
        assert db.session.execute(
            text("PRAGMA foreign_keys")
        ).scalar_one() == 1
        inspector = inspect(db.engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("shop_tasks")
        }
        assert set(columns) == {
            "id",
            "dataset_id",
            "title",
            "is_completed",
            "created_at",
            "completed_at",
        }
        assert columns["dataset_id"]["nullable"] is False
        assert columns["title"]["nullable"] is False
        assert columns["title"]["type"].length == 100
        assert columns["is_completed"]["nullable"] is False
        assert columns["created_at"]["nullable"] is False
        assert columns["completed_at"]["nullable"] is True

        foreign_key = inspector.get_foreign_keys("shop_tasks")[0]
        assert foreign_key["name"] == "fk_shop_tasks_dataset_id_datasets"
        assert foreign_key["constrained_columns"] == ["dataset_id"]
        assert foreign_key["referred_table"] == "datasets"
        assert foreign_key["options"].get("ondelete", "").upper() == "CASCADE"

        check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("shop_tasks")
        }
        assert check_names == {
            "ck_shop_tasks_title_nonblank",
            "ck_shop_tasks_title_max_length",
            "ck_shop_tasks_completion_timestamp",
        }
        assert any(
            index["name"] == "ix_shop_tasks_dataset_status_created_id"
            and index["column_names"]
            == ["dataset_id", "is_completed", "created_at", "id"]
            and not index["unique"]
            for index in inspector.get_indexes("shop_tasks")
        )

        db.session.execute(
            text(
                "INSERT INTO shop_tasks "
                "(id, dataset_id, title, is_completed, created_at, completed_at) "
                "VALUES (1, :dataset_id, '既存確認タスク', false, :now, NULL)"
            ),
            {"dataset_id": dataset_id, "now": now},
        )
        db.session.commit()

        invalid_statements = (
            "INSERT INTO shop_tasks "
            "(dataset_id, title, is_completed, created_at, completed_at) "
            "VALUES (:dataset_id, '   ', false, :now, NULL)",
            "INSERT INTO shop_tasks "
            "(dataset_id, title, is_completed, created_at, completed_at) "
            "VALUES (:dataset_id, :long_title, false, :now, NULL)",
            "INSERT INTO shop_tasks "
            "(dataset_id, title, is_completed, created_at, completed_at) "
            "VALUES (:dataset_id, '不整合', true, :now, NULL)",
            "INSERT INTO shop_tasks "
            "(dataset_id, title, is_completed, created_at, completed_at) "
            "VALUES (:dataset_id, '逆向き不整合', false, :now, :now)",
        )
        for statement in invalid_statements:
            with pytest.raises(IntegrityError):
                db.session.execute(
                    text(statement),
                    {
                        "dataset_id": dataset_id,
                        "now": now,
                        "long_title": "タ" * 101,
                    },
                )
                db.session.commit()
            db.session.rollback()

        db.session.execute(
            text("DELETE FROM datasets WHERE id = :dataset_id"),
            {"dataset_id": dataset_id},
        )
        db.session.commit()
        assert db.session.execute(
            text("SELECT COUNT(*) FROM shop_tasks")
        ).scalar_one() == 0

        downgrade(directory=str(MIGRATIONS_DIR), revision=PRE_SHOP_TASK_REVISION)
        assert "shop_tasks" not in inspect(db.engine).get_table_names()
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == PRE_SHOP_TASK_REVISION


def test_direct_task_ux_migration_preserves_rows_and_downgrades_sqlite(
    tmp_path,
):
    database_path = tmp_path / "shop_task_direct_ux_migration.sqlite"
    migration_app = _migration_app(f"sqlite:///{database_path}")
    now = datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc)

    with migration_app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision=SHOP_TASK_REVISION)
        dataset_ids = db.session.execute(
            text("SELECT id FROM datasets ORDER BY system_key")
        ).scalars().all()
        assert dataset_ids
        dataset_id = dataset_ids[0]
        db.session.execute(
            text(
                "INSERT INTO shop_tasks "
                "(id, dataset_id, title, is_completed, created_at, completed_at) "
                "VALUES "
                "(5, :dataset_id, '既存A', false, :now, NULL), "
                "(9, :dataset_id, '既存B', true, :now, :now)"
            ),
            {"dataset_id": dataset_id, "now": now},
        )
        db.session.commit()

        upgrade(
            directory=str(MIGRATIONS_DIR),
            revision=SHOP_TASK_DIRECT_UX_REVISION,
        )
        inspector = inspect(db.engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("shop_tasks")
        }
        assert columns["position"]["nullable"] is False
        assert columns["is_starred"]["nullable"] is False
        assert any(
            index["name"] == "ix_shop_tasks_dataset_status_position_id"
            and index["column_names"]
            == ["dataset_id", "is_completed", "position", "id"]
            for index in inspector.get_indexes("shop_tasks")
        )
        check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("shop_tasks")
        }
        assert "ck_shop_tasks_position_nonnegative" in check_names
        rows = db.session.execute(
            text(
                "SELECT id, title, position, is_starred "
                "FROM shop_tasks ORDER BY id"
            )
        ).mappings().all()
        assert [dict(row) for row in rows] == [
            {
                "id": 5,
                "title": "既存A",
                "position": 1,
                "is_starred": 0,
            },
            {
                "id": 9,
                "title": "既存B",
                "position": 0,
                "is_starred": 0,
            },
        ]

        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    "UPDATE shop_tasks SET position = -1 WHERE id = 5"
                )
            )
            db.session.commit()
        db.session.rollback()

        downgrade(directory=str(MIGRATIONS_DIR), revision=SHOP_TASK_REVISION)
        downgraded_columns = {
            column["name"]
            for column in inspect(db.engine).get_columns("shop_tasks")
        }
        assert "position" not in downgraded_columns
        assert "is_starred" not in downgraded_columns
        preserved = db.session.execute(
            text("SELECT id, title FROM shop_tasks ORDER BY id")
        ).all()
        assert preserved == [(5, "既存A"), (9, "既存B")]
