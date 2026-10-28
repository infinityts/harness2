"""license_admin_mfa

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "harness_users",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "harness_users",
        sa.Column("mfa_totp_secret_vault", sa.Text(), nullable=True),
    )
    op.add_column(
        "harness_users",
        sa.Column("mfa_last_used_step", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "harness_users",
        sa.Column("password_change_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "harness_users",
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "harness_users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("harness_users", "locked_until")
    op.drop_column("harness_users", "failed_login_attempts")
    op.drop_column("harness_users", "password_change_allowed")
    op.drop_column("harness_users", "mfa_last_used_step")
    op.drop_column("harness_users", "mfa_totp_secret_vault")
    op.drop_column("harness_users", "mfa_enabled")
