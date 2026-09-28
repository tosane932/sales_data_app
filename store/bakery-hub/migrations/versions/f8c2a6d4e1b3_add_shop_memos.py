"""add shop memos

Revision ID: f8c2a6d4e1b3
Revises: d4f7a9c2e6b1
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f8c2a6d4e1b3"
down_revision = "d4f7a9c2e6b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shop_memos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "length(trim(body)) >= 1",
            name="ck_shop_memos_body_nonblank",
        ),
        sa.CheckConstraint(
            "length(body) <= 2000",
            name="ck_shop_memos_body_max_length",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_shop_memos_updated_not_before_creation",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name="ck_shop_memos_deleted_not_before_creation",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR updated_at >= deleted_at",
            name="ck_shop_memos_updated_not_before_deletion",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_shop_memos_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shop_memos_dataset_deleted_updated_id",
        "shop_memos",
        ["dataset_id", "deleted_at", "updated_at", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_shop_memos_dataset_deleted_updated_id",
        table_name="shop_memos",
    )
    op.drop_table("shop_memos")
