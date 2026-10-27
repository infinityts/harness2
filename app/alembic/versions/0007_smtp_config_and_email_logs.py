"""smtp_config_and_email_dispatch_logs

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_smtp_config",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("host", sa.String(255), nullable=False, server_default="smtp.gmail.com"),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("password", sa.String(255), nullable=True),
        sa.Column("from_email", sa.String(255), nullable=False, server_default="harness-bot@controlplane.local"),
        sa.Column("use_tls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_smtp_singleton"),
    )

    op.create_table(
        "email_dispatch_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("template_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="SENT"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_email_dispatch_logs_sent_at", "email_dispatch_logs", ["sent_at"])
    op.create_index("idx_email_dispatch_logs_status", "email_dispatch_logs", ["status"])

    op.execute("""
        INSERT INTO system_smtp_config
            (id, host, port, username, password, from_email, use_tls, use_ssl)
        VALUES
            (1, 'smtp.gmail.com', 587, NULL, NULL, 'harness-bot@controlplane.local', true, false)
        ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("idx_email_dispatch_logs_status", table_name="email_dispatch_logs")
    op.drop_index("idx_email_dispatch_logs_sent_at", table_name="email_dispatch_logs")
    op.drop_table("email_dispatch_logs")
    op.drop_table("system_smtp_config")
