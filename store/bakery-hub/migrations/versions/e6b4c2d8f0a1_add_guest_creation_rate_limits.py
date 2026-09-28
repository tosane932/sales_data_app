"""add guest creation rate limits

Revision ID: e6b4c2d8f0a1
Revises: a8f3c1d5e7b9
Create Date: 2026-09-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6b4c2d8f0a1"
down_revision = "a8f3c1d5e7b9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "guest_creation_rate_limits",
        sa.Column(
            "client_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "request_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "request_count >= 0",
            name=(
                "ck_guest_creation_rate_limits_"
                "request_count_nonnegative"
            ),
        ),
        sa.PrimaryKeyConstraint("client_key_hash"),
    )


def downgrade():
    op.drop_table("guest_creation_rate_limits")
