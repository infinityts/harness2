"""project_git_credentials

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "harness_projects",
        sa.Column("git_auth_type", sa.String(20), nullable=False, server_default="none"),
    )
    op.add_column(
        "harness_projects",
        sa.Column("git_token_vault", sa.Text(), nullable=True),
    )
    op.add_column(
        "harness_projects",
        sa.Column("git_username", sa.String(150), nullable=True),
    )
    op.add_column(
        "harness_projects",
        sa.Column("git_token_hint", sa.String(12), nullable=True),
    )
    op.add_column(
        "harness_projects",
        sa.Column("git_credential_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column in (
        "git_credential_updated_at", "git_token_hint", "git_username",
        "git_token_vault", "git_auth_type",
    ):
        op.drop_column("harness_projects", column)
