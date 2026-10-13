"""tambah tipe notifikasi prediktif

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13 15:53:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sent_notifications", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_sent_notifications_ck_sent_notifications_type_valid"),
            type_="check",
        )
        batch_op.create_check_constraint(
            op.f("ck_sent_notifications_ck_sent_notifications_type_valid"),
            "notification_type IN ('daily', 'weekly', 'monthly', 'recurring', "
            "'predictive_budget', 'predictive_runway')",
        )


def downgrade() -> None:
    with op.batch_alter_table("sent_notifications", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_sent_notifications_ck_sent_notifications_type_valid"),
            type_="check",
        )
        batch_op.create_check_constraint(
            op.f("ck_sent_notifications_ck_sent_notifications_type_valid"),
            "notification_type IN ('daily', 'weekly', 'monthly', 'recurring')",
        )
