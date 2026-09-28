"""create initial products and daily_sales tables

Revision ID: b7e2c4a91f30
Revises:
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2c4a91f30'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'products',
        sa.Column(
            'id',
            sa.Integer(),
            nullable=False
        ),
        sa.Column(
            'year',
            sa.Integer(),
            nullable=False
        ),
        sa.Column(
            'month',
            sa.Integer(),
            nullable=False
        ),
        sa.Column(
            'name',
            sa.String(length=100),
            nullable=False
        ),
        sa.Column(
            'price',
            sa.Integer(),
            nullable=False
        ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table(
        'daily_sales',
        sa.Column(
            'id',
            sa.Integer(),
            nullable=False
        ),
        sa.Column(
            'product_id',
            sa.Integer(),
            nullable=False
        ),
        sa.Column(
            'date',
            sa.Date(),
            nullable=False
        ),
        sa.Column(
            'quantity',
            sa.Integer(),
            nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['product_id'],
            ['products.id']
        ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('daily_sales')
    op.drop_table('products')
