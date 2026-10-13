"""tambah tabel reminder_settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 03:00:43
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminder_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("is_daily_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("daily_time", sa.String(length=5), server_default="20:00", nullable=False),
        sa.Column("is_weekly_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_monthly_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_daily_sent", sa.Date(), nullable=True),
        sa.Column("last_weekly_sent", sa.Date(), nullable=True),
        sa.Column("last_monthly_sent", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(daily_time)) = 5",
            name=op.f("ck_reminder_settings_ck_reminder_settings_time_format"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_reminder_settings_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_settings")),
        sa.UniqueConstraint("user_id", name="uq_reminder_settings_user_id"),
    )
    with op.batch_alter_table("reminder_settings", schema=None) as batch_op:
        batch_op.create_index("ix_reminder_settings_user", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("reminder_settings", schema=None) as batch_op:
        batch_op.drop_index("ix_reminder_settings_user")

    op.drop_table("reminder_settings")
