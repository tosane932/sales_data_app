"""enforce product dataset not null

Revision ID: f2b6c8d4e1a9
Revises: c7a1d9e4f2b6
Create Date: 2026-08-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2b6c8d4e1a9"
down_revision = "c7a1d9e4f2b6"
branch_labels = None
depends_on = None


def _dataset_table():
    return sa.table(
        "datasets",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("kind", sa.String(length=16)),
        sa.column("system_key", sa.String(length=100)),
    )


def _product_table():
    return sa.table(
        "products",
        sa.column("id", sa.Integer()),
        sa.column("dataset_id", sa.Uuid(as_uuid=True)),
    )


def _count_null_products(bind, products):
    return bind.execute(
        sa.select(sa.func.count())
        .select_from(products)
        .where(products.c.dataset_id.is_(None))
    ).scalar_one()


def _count_orphan_products(bind, products, datasets):
    return bind.execute(
        sa.select(sa.func.count())
        .select_from(
            products.outerjoin(
                datasets,
                products.c.dataset_id == datasets.c.id,
            )
        )
        .where(
            products.c.dataset_id.is_not(None),
            datasets.c.id.is_(None),
        )
    ).scalar_one()


def upgrade():
    bind = op.get_bind()
    datasets = _dataset_table()
    products = _product_table()

    admin_dataset_ids = bind.execute(
        sa.select(datasets.c.id).where(
            datasets.c.kind == "admin",
            datasets.c.system_key == "admin",
        )
    ).scalars().all()
    if len(admin_dataset_ids) != 1:
        raise RuntimeError(
            "Admin Dataset count is invalid before Product NOT NULL "
            f"migration: expected=1, actual={len(admin_dataset_ids)}"
        )
    admin_dataset_id = admin_dataset_ids[0]

    null_product_count = _count_null_products(bind, products)
    orphan_product_count = _count_orphan_products(
        bind,
        products,
        datasets,
    )
    if orphan_product_count != 0:
        raise RuntimeError(
            "Product NOT NULL migration found Products referencing missing "
            f"Datasets: count={orphan_product_count}"
        )

    if null_product_count != 0:
        bind.execute(
            products.update()
            .where(products.c.dataset_id.is_(None))
            .values(dataset_id=admin_dataset_id)
        )

    remaining_null_product_count = _count_null_products(bind, products)
    if remaining_null_product_count != 0:
        raise RuntimeError(
            "Product NOT NULL migration could not backfill every NULL "
            f"dataset_id: count={remaining_null_product_count}"
        )

    remaining_orphan_product_count = _count_orphan_products(
        bind,
        products,
        datasets,
    )
    if remaining_orphan_product_count != 0:
        raise RuntimeError(
            "Product NOT NULL migration left Products referencing missing "
            f"Datasets: count={remaining_orphan_product_count}"
        )

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.alter_column(
            "dataset_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.alter_column(
            "dataset_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=True,
        )
