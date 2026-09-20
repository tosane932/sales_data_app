"""add shop memo pins

Revision ID: a14c5e7d9b20
Revises: c92128da7c36
Create Date: 2026-09-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a14c5e7d9b20"
down_revision = "c92128da7c36"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "shop_memos",
        sa.Column(
            "pinned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    with op.batch_alter_table("shop_memos") as batch_op:
        batch_op.create_check_constraint(
            "ck_shop_memos_pinned_not_before_creation",
            "pinned_at IS NULL OR pinned_at >= created_at",
        )

    op.drop_index(
        "ix_shop_memos_dataset_deleted_updated_id",
        table_name="shop_memos",
    )
    op.create_index(
        "ix_shop_memos_dataset_deleted_pinned_updated_id",
        "shop_memos",
        ["dataset_id", "deleted_at", "pinned_at", "updated_at", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_shop_memos_dataset_deleted_pinned_updated_id",
        table_name="shop_memos",
    )
    op.create_index(
        "ix_shop_memos_dataset_deleted_updated_id",
        "shop_memos",
        ["dataset_id", "deleted_at", "updated_at", "id"],
        unique=False,
    )

    with op.batch_alter_table("shop_memos") as batch_op:
        batch_op.drop_constraint(
            "ck_shop_memos_pinned_not_before_creation",
            type_="check",
        )
        batch_op.drop_column("pinned_at")
