"""add shop memo titles

Revision ID: c92128da7c36
Revises: f8c2a6d4e1b3
Create Date: 2026-09-18 20:32:30.126600

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c92128da7c36"
down_revision = "f8c2a6d4e1b3"
branch_labels = None
depends_on = None


def _title_from_body(body):
    """既存メモの本文から移行用タイトルを作る。"""
    for line in body.splitlines():
        stripped_line = line.strip()
        if stripped_line:
            return stripped_line[:100]

    return body.strip()[:100]


def upgrade():
    # 既存行があるため、最初はNULL許可で追加する。
    op.add_column(
        "shop_memos",
        sa.Column("title", sa.String(length=100), nullable=True),
    )

    # 既存メモは本文の最初の空でない行を仮タイトルにする。
    bind = op.get_bind()
    shop_memos = sa.table(
        "shop_memos",
        sa.column("id", sa.Integer()),
        sa.column("title", sa.String(length=100)),
        sa.column("body", sa.Text()),
    )

    rows = bind.execute(
        sa.select(shop_memos.c.id, shop_memos.c.body)
    ).fetchall()

    for row in rows:
        bind.execute(
            shop_memos.update()
            .where(shop_memos.c.id == row.id)
            .values(title=_title_from_body(row.body))
        )

    # 全既存行へ値を入れた後、NOT NULLと制約を適用する。
    with op.batch_alter_table("shop_memos") as batch_op:
        batch_op.alter_column(
            "title",
            existing_type=sa.String(length=100),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_shop_memos_title_nonblank",
            "length(trim(title)) >= 1",
        )
        batch_op.create_check_constraint(
            "ck_shop_memos_title_max_length",
            "length(title) <= 100",
        )


def downgrade():
    with op.batch_alter_table("shop_memos") as batch_op:
        batch_op.drop_constraint(
            "ck_shop_memos_title_max_length",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_shop_memos_title_nonblank",
            type_="check",
        )
        batch_op.drop_column("title")
