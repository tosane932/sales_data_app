"""create datasets and backfill products

Revision ID: c7a1d9e4f2b6
Revises: 9d3c1b7e5a42
Create Date: 2026-08-19

"""
import datetime
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7a1d9e4f2b6"
down_revision = "9d3c1b7e5a42"
branch_labels = None
depends_on = None


ADMIN_DATASET_ID = uuid.UUID("6f3f7558-04fc-4f5a-9e57-5d5f9f8b1a01")


def _dataset_table():
    return sa.table(
        "datasets",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("kind", sa.String(length=16)),
        sa.column("system_key", sa.String(length=100)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("last_activity_at", sa.DateTime(timezone=True)),
        sa.column("absolute_expires_at", sa.DateTime(timezone=True)),
    )


def _product_table():
    return sa.table(
        "products",
        sa.column("id", sa.Integer()),
        sa.column("dataset_id", sa.Uuid(as_uuid=True)),
    )


def _daily_sales_table():
    return sa.table(
        "daily_sales",
        sa.column("id", sa.Integer()),
    )


def _count_rows(bind, table):
    return bind.execute(
        sa.select(sa.func.count()).select_from(table)
    ).scalar_one()


def _validate_backfill(bind, expected_product_count, expected_sales_count):
    datasets = _dataset_table()
    products = _product_table()
    daily_sales = _daily_sales_table()

    product_count = _count_rows(bind, products)
    if product_count != expected_product_count:
        raise RuntimeError(
            "Product count changed during Dataset migration: "
            f"expected={expected_product_count}, actual={product_count}"
        )

    null_product_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(products)
        .where(products.c.dataset_id.is_(None))
    ).scalar_one()
    if null_product_count != 0:
        raise RuntimeError(
            "Product backfill left NULL dataset_id values: "
            f"count={null_product_count}"
        )

    admin_product_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(products)
        .where(products.c.dataset_id == ADMIN_DATASET_ID)
    ).scalar_one()
    if admin_product_count != expected_product_count:
        raise RuntimeError(
            "Not all Products belong to the admin Dataset: "
            f"expected={expected_product_count}, actual={admin_product_count}"
        )

    admin_dataset_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(datasets)
        .where(
            datasets.c.kind == "admin",
            datasets.c.system_key == "admin",
        )
    ).scalar_one()
    if admin_dataset_count != 1:
        raise RuntimeError(
            "Admin Dataset count is invalid: "
            f"expected=1, actual={admin_dataset_count}"
        )

    sales_count = _count_rows(bind, daily_sales)
    if sales_count != expected_sales_count:
        raise RuntimeError(
            "DailySales count changed during Dataset migration: "
            f"expected={expected_sales_count}, actual={sales_count}"
        )

    orphan_product_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(
            products.outerjoin(
                datasets,
                products.c.dataset_id == datasets.c.id,
            )
        )
        .where(datasets.c.id.is_(None))
    ).scalar_one()
    if orphan_product_count != 0:
        raise RuntimeError(
            "Dataset migration created orphan Products: "
            f"count={orphan_product_count}"
        )


def upgrade():
    bind = op.get_bind()
    products_without_dataset = sa.table(
        "products",
        sa.column("id", sa.Integer()),
    )
    daily_sales = _daily_sales_table()
    product_count_before = _count_rows(bind, products_without_dataset)
    sales_count_before = _count_rows(bind, daily_sales)

    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("system_key", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "absolute_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "kind IN ('admin', 'guest')",
            name="ck_datasets_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'admin' AND system_key = 'admin') OR "
            "(kind = 'guest' AND system_key IS NULL)",
            name="ck_datasets_system_key_by_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'admin' AND absolute_expires_at IS NULL) OR "
            "(kind = 'guest' AND absolute_expires_at IS NOT NULL)",
            name="ck_datasets_absolute_expiry_by_kind",
        ),
        sa.CheckConstraint(
            "last_activity_at >= created_at",
            name="ck_datasets_activity_not_before_creation",
        ),
        sa.CheckConstraint(
            "absolute_expires_at IS NULL OR absolute_expires_at > created_at",
            name="ck_datasets_expiry_after_creation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_key", name="uq_datasets_system_key"),
    )

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "dataset_id",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )

    datasets = _dataset_table()
    products = _product_table()
    admin_created_at = datetime.datetime.now(datetime.timezone.utc)

    bind.execute(
        datasets.insert().values(
            id=ADMIN_DATASET_ID,
            kind="admin",
            system_key="admin",
            created_at=admin_created_at,
            last_activity_at=admin_created_at,
            absolute_expires_at=None,
        )
    )

    bind.execute(
        products.update()
        .where(products.c.dataset_id.is_(None))
        .values(dataset_id=ADMIN_DATASET_ID)
    )

    _validate_backfill(bind, product_count_before, sales_count_before)

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_products_dataset_id_datasets",
            "datasets",
            ["dataset_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_products_dataset_id",
            ["dataset_id"],
            unique=False,
        )

    _validate_backfill(bind, product_count_before, sales_count_before)


def downgrade():
    bind = op.get_bind()
    datasets = _dataset_table()

    guest_dataset_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(datasets)
        .where(datasets.c.kind == "guest")
    ).scalar_one()
    if guest_dataset_count != 0:
        raise RuntimeError(
            "Refusing to downgrade while guest Datasets exist: "
            f"count={guest_dataset_count}"
        )

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index("ix_products_dataset_id")
        batch_op.drop_constraint(
            "fk_products_dataset_id_datasets",
            type_="foreignkey",
        )
        batch_op.drop_column("dataset_id")

    op.drop_table("datasets")
