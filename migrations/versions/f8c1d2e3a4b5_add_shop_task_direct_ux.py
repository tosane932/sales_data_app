"""add shop task direct ux state

Revision ID: f8c1d2e3a4b5
Revises: b3f6d8a1c2e4
Create Date: 2026-09-23

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f8c1d2e3a4b5"
down_revision = "b3f6d8a1c2e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "shop_tasks",
        sa.Column(
            "is_starred",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "shop_tasks",
        sa.Column(
            "position",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "WITH ranked AS ("
            "SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY dataset_id ORDER BY created_at DESC, id DESC"
            ") - 1 AS new_position FROM shop_tasks"
            ") UPDATE shop_tasks SET position = ("
            "SELECT new_position FROM ranked WHERE ranked.id = shop_tasks.id"
            ")"
        )
    )
    op.drop_index(
        "ix_shop_tasks_dataset_status_created_id",
        table_name="shop_tasks",
    )
    with op.batch_alter_table("shop_tasks") as batch_op:
        batch_op.create_check_constraint(
            "ck_shop_tasks_position_nonnegative",
            "position >= 0",
        )
    op.create_index(
        "ix_shop_tasks_dataset_status_position_id",
        "shop_tasks",
        ["dataset_id", "is_completed", "position", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_shop_tasks_dataset_status_position_id",
        table_name="shop_tasks",
    )
    with op.batch_alter_table("shop_tasks") as batch_op:
        batch_op.drop_constraint(
            "ck_shop_tasks_position_nonnegative",
            type_="check",
        )
        batch_op.drop_column("position")
        batch_op.drop_column("is_starred")
    op.create_index(
        "ix_shop_tasks_dataset_status_created_id",
        "shop_tasks",
        ["dataset_id", "is_completed", "created_at", "id"],
        unique=False,
    )
