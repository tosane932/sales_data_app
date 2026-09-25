"""add shop tasks

Revision ID: b3f6d8a1c2e4
Revises: a14c5e7d9b20
Create Date: 2026-09-23

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b3f6d8a1c2e4"
down_revision = "a14c5e7d9b20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shop_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column(
            "is_completed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(trim(title)) >= 1",
            name="ck_shop_tasks_title_nonblank",
        ),
        sa.CheckConstraint(
            "length(title) <= 100",
            name="ck_shop_tasks_title_max_length",
        ),
        sa.CheckConstraint(
            "(is_completed = false AND completed_at IS NULL) OR "
            "(is_completed = true AND completed_at IS NOT NULL)",
            name="ck_shop_tasks_completion_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_shop_tasks_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shop_tasks_dataset_status_created_id",
        "shop_tasks",
        ["dataset_id", "is_completed", "created_at", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_shop_tasks_dataset_status_created_id",
        table_name="shop_tasks",
    )
    op.drop_table("shop_tasks")
