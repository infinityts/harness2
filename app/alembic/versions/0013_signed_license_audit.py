"""signed_license_and_audit

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Identidad de la instalacion, usada para atar la licencia al servidor.
    op.create_table(
        "system_installation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_system_installation_singleton"),
    )
    op.execute(
        sa.text("""
            INSERT INTO system_installation (id)
            VALUES (1)
            ON CONFLICT (id) DO NOTHING
        """)
    )

    # Cache del estado de la licencia firmada. No es la fuente de verdad:
    # la fuente es el archivo firmado + la clave publica del vendor.
    op.create_table(
        "system_license_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="MISSING"),
        sa.Column("license_id", sa.String(120), nullable=True),
        sa.Column("license_hash", sa.String(64), nullable=True),
        sa.Column("plan_code", sa.String(40), nullable=True),
        sa.Column("customer", sa.String(255), nullable=True),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("allowed_plans", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("signed_limits", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("features", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_license", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_system_license_state_singleton"),
        sa.CheckConstraint(
            "status IN ('VALID', 'GRACE', 'EXPIRED', 'INVALID', 'MISSING', 'DEVELOPMENT')",
            name="ck_system_license_state_status",
        ),
    )
    op.execute(
        sa.text("""
            INSERT INTO system_license_state (id, status)
            VALUES (1, 'MISSING')
            ON CONFLICT (id) DO NOTHING
        """)
    )

    # Cadena de auditoria append-only. Cada evento incluye el hash del anterior.
    op.create_table(
        "license_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(150), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_license_audit_type", "license_audit_events", ["event_type"])
    op.create_index("idx_license_audit_created", "license_audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_license_audit_created", table_name="license_audit_events")
    op.drop_index("idx_license_audit_type", table_name="license_audit_events")
    op.drop_table("license_audit_events")
    op.drop_table("system_license_state")
    op.drop_table("system_installation")
