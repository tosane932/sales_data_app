"""add guest AI usage count

Revision ID: a8f3c1d5e7b9
Revises: f2b6c8d4e1a9
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a8f3c1d5e7b9"
down_revision = "f2b6c8d4e1a9"
branch_labels = None
depends_on = None


AI_USAGE_CHECK_NAME = "ck_datasets_guest_ai_usage_count"


def _dataset_table():
    return sa.table(
        "datasets",
        sa.column("guest_ai_usage_count", sa.Integer()),
    )


def upgrade():
    bind = op.get_bind()

    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "guest_ai_usage_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=True,
            )
        )

    datasets = _dataset_table()
    bind.execute(
        datasets.update()
        .where(datasets.c.guest_ai_usage_count.is_(None))
        .values(guest_ai_usage_count=0)
    )

    null_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(datasets)
        .where(datasets.c.guest_ai_usage_count.is_(None))
    ).scalar_one()
    if null_count != 0:
        raise RuntimeError(
            "Guest AI usage backfill left NULL values: "
            f"count={null_count}"
        )

    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.alter_column(
            "guest_ai_usage_count",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        )
        batch_op.create_check_constraint(
            AI_USAGE_CHECK_NAME,
            "(kind = 'admin' AND guest_ai_usage_count = 0) OR "
            "(kind = 'guest' AND guest_ai_usage_count BETWEEN 0 AND 3)",
        )


def downgrade():
    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.drop_constraint(
            AI_USAGE_CHECK_NAME,
            type_="check",
        )
        batch_op.drop_column("guest_ai_usage_count")
