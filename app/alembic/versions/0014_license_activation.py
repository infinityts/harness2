"""license_activation

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

STATUSES = ("NOT_CONFIGURED", "ACTIVE", "OFFLINE_GRACE", "EXPIRED", "REVOKED", "ERROR", "TEST_BYPASS")


def upgrade() -> None:
    op.create_table(
        "license_activation_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("server_url", sa.String(500), nullable=True),
        sa.Column("license_id", sa.String(120), nullable=True),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_id", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("lease_payload", postgresql.JSONB(), nullable=True),
        sa.Column("lease_signature", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_license_activation_singleton"),
        sa.CheckConstraint("status IN " + str(STATUSES), name="ck_license_activation_status"),
    )
    op.execute(
        sa.text("""
            INSERT INTO license_activation_state (id, status)
            VALUES (1, 'NOT_CONFIGURED')
            ON CONFLICT (id) DO NOTHING
        """)
    )
    op.create_index("idx_license_activation_status", "license_activation_state", ["status"])


def downgrade() -> None:
    op.drop_index("idx_license_activation_status", table_name="license_activation_state")
    op.drop_table("license_activation_state")
