"""tambah user_settings dan sent_notifications

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13 13:43:02.014848
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sent_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=30), nullable=False),
        sa.Column("period_key", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "notification_type IN ('daily', 'weekly', 'monthly', 'recurring')",
            name=op.f("ck_sent_notifications_ck_sent_notifications_type_valid"),
        ),
        sa.CheckConstraint(
            "length(trim(period_key)) > 0",
            name=op.f("ck_sent_notifications_ck_sent_notifications_period_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sent_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sent_notifications")),
        sa.UniqueConstraint(
            "user_id",
            "notification_type",
            "period_key",
            name="uq_sent_notifications_user_type_period",
        ),
    )
    with op.batch_alter_table("sent_notifications", schema=None) as batch_op:
        batch_op.create_index(
            "ix_sent_notifications_user_type",
            ["user_id", "notification_type"],
            unique=False,
        )

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=50), server_default="Asia/Jakarta", nullable=False),
        sa.Column("is_daily_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("daily_time", sa.String(length=5), server_default="20:00", nullable=False),
        sa.Column("is_recurring_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_weekly_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_monthly_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(daily_time)) = 5",
            name=op.f("ck_user_settings_ck_user_settings_time_format"),
        ),
        sa.CheckConstraint(
            "length(trim(timezone)) > 0",
            name=op.f("ck_user_settings_ck_user_settings_tz_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_settings_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_settings")),
        sa.UniqueConstraint("user_id", name="uq_user_settings_user_id"),
    )
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.create_index("ix_user_settings_user", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.drop_index("ix_user_settings_user")

    op.drop_table("user_settings")
    with op.batch_alter_table("sent_notifications", schema=None) as batch_op:
        batch_op.drop_index("ix_sent_notifications_user_type")

    op.drop_table("sent_notifications")
