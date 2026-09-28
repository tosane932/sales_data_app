"""add material order items

Revision ID: d4f7a9c2e6b1
Revises: e6b4c2d8f0a1
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4f7a9c2e6b1"
down_revision = "e6b4c2d8f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "material_order_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("quantity_text", sa.String(length=30), nullable=True),
        sa.Column("memo", sa.String(length=300), nullable=True),
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
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(is_completed = false AND completed_at IS NULL) OR "
            "(is_completed = true AND completed_at IS NOT NULL)",
            name="ck_material_order_items_completion_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_material_order_items_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_material_order_items_dataset_status_created_id",
        "material_order_items",
        ["dataset_id", "is_completed", "created_at", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_material_order_items_dataset_status_created_id",
        table_name="material_order_items",
    )
    op.drop_table("material_order_items")
