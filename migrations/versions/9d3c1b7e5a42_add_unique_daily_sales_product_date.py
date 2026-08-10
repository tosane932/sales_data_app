"""add unique constraint to daily_sales product and date

Revision ID: 9d3c1b7e5a42
Revises: 043c481b4069
Create Date: 2026-08-10

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '9d3c1b7e5a42'
down_revision = '043c481b4069'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('daily_sales', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_daily_sales_product_date',
            ['product_id', 'date']
        )


def downgrade():
    with op.batch_alter_table('daily_sales', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_daily_sales_product_date',
            type_='unique'
        )
