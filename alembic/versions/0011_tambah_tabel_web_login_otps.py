"""tambah tabel web_login_otps

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-14 22:16:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_login_otps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(code_hash)) > 0",
            name=op.f("ck_web_login_otps_code_hash_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_web_login_otps_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_login_otps")),
    )
    with op.batch_alter_table("web_login_otps", schema=None) as batch_op:
        batch_op.create_index("ix_web_login_otps_user", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_web_login_otps_user_created", ["user_id", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("web_login_otps", schema=None) as batch_op:
        batch_op.drop_index("ix_web_login_otps_user_created")
        batch_op.drop_index("ix_web_login_otps_user")

    op.drop_table("web_login_otps")
