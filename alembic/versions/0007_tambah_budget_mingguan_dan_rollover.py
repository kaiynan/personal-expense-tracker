"""tambah budget mingguan dan rollover

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 07:40:16.578247
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_weekly_budgets_amount_positive")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_weekly_budgets_category_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_weekly_budgets_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_budgets")),
        sa.UniqueConstraint(
            "user_id",
            "category_id",
            "week_start",
            name="uq_weekly_budgets_user_id_category_id_week_start",
        ),
    )
    with op.batch_alter_table("weekly_budgets", schema=None) as batch_op:
        batch_op.create_index(
            "uq_weekly_budgets_total_per_week",
            ["user_id", "week_start"],
            unique=True,
            sqlite_where=sa.text("category_id IS NULL"),
            postgresql_where=sa.text("category_id IS NULL"),
        )

    with op.batch_alter_table("budgets", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "carryover_amount",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_rollover_enabled",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_budgets_carryover_non_negative"),
            "carryover_amount >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("budgets", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_budgets_carryover_non_negative"), type_="check")
        batch_op.drop_column("is_rollover_enabled")
        batch_op.drop_column("carryover_amount")

    with op.batch_alter_table("weekly_budgets", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_weekly_budgets_total_per_week",
            sqlite_where=sa.text("category_id IS NULL"),
            postgresql_where=sa.text("category_id IS NULL"),
        )

    op.drop_table("weekly_budgets")
