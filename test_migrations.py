from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from models import db


PROJECT_ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


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
        assert {"products", "daily_sales", "alembic_version"}.issubset(
            table_names
        )

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

        current_revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert expected_head is not None
        assert current_revision == expected_head
