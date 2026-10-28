"""license_hermes_pool

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

JOB_TYPES = ("RAG_BOOTSTRAP", "RAG_INGEST_SOURCE", "RAG_REFRESH_PROJECT", "RAG_UPDATE_ARCHITECTURE")
JOB_STATUS = ("QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
SESSION_KINDS = ("INTERACTIVE", "SYSTEM_WORKER")


def upgrade() -> None:
    # 1. Licenciamiento por planes
    op.create_table(
        "system_license_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_code", sa.String(40), nullable=False, server_default="ENTERPRISE_10"),
        sa.Column("max_active_projects", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_hermes_sessions", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_engineers_per_project", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_total_engineer_sessions", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_registered_users", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_parallel_rag_jobs", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_license_policy_singleton"),
        sa.CheckConstraint("max_active_projects >= 0", name="ck_license_active_projects_nonnegative"),
        sa.CheckConstraint("max_hermes_sessions >= 0", name="ck_license_hermes_sessions_nonnegative"),
        sa.CheckConstraint("max_engineers_per_project >= 0", name="ck_license_engineers_project_nonnegative"),
        sa.CheckConstraint("max_total_engineer_sessions >= 0", name="ck_license_engineers_total_nonnegative"),
        sa.CheckConstraint("max_registered_users >= 0", name="ck_license_users_nonnegative"),
        sa.CheckConstraint("max_parallel_rag_jobs >= 1", name="ck_license_parallel_jobs_positive"),
    )
    op.execute(
        sa.text("""
            INSERT INTO system_license_policy
                (id, plan_code, max_active_projects, max_hermes_sessions,
                 max_engineers_per_project, max_total_engineer_sessions,
                 max_registered_users, max_parallel_rag_jobs)
            VALUES
                (1, 'ENTERPRISE_10', 10, 10, 10, 30, 30, 3)
            ON CONFLICT (id) DO NOTHING
        """)
    )

    # 2. Slots activos por proyecto
    op.add_column(
        "harness_projects",
        sa.Column("is_active_slot", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("idx_harness_projects_active_slot", "harness_projects", ["is_active_slot"])

    # 3. Tipo de sesion y rol de worker
    op.add_column(
        "agent_sessions",
        sa.Column("session_kind", sa.String(30), nullable=False, server_default="INTERACTIVE"),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("worker_role", sa.String(40), nullable=True),
    )
    op.execute(
        sa.text("""
            UPDATE agent_sessions
            SET session_kind = 'SYSTEM_WORKER',
                worker_role = 'DOCS_CURATOR'
            WHERE is_headless_worker = true
              AND session_kind = 'INTERACTIVE'
        """)
    )
    op.create_index("idx_agent_sessions_kind_status", "agent_sessions", ["session_kind", "status"])
    op.execute(
        sa.text("""
            CREATE UNIQUE INDEX uq_agent_sessions_hermes_project
            ON agent_sessions(project_id)
            WHERE session_kind = 'SYSTEM_WORKER'
              AND agent_tool = 'hermes'
              AND status <> 'REVOKED'
        """)
    )

    # 4. Cola de trabajos del pool Hermes
    op.create_table(
        "hermes_worker_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(30), nullable=False, server_default="QUEUED"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("locked_by", sa.String(150), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("job_type IN " + str(JOB_TYPES), name="ck_hermes_jobs_type"),
        sa.CheckConstraint("status IN " + str(JOB_STATUS), name="ck_hermes_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_hermes_jobs_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_hermes_jobs_max_attempts_positive"),
    )
    op.create_index("idx_hermes_jobs_status_priority", "hermes_worker_jobs",
                    ["status", "priority", "created_at"])
    op.create_index("idx_hermes_jobs_project_status", "hermes_worker_jobs",
                    ["project_id", "status"])
    op.execute(
        sa.text("""
            CREATE UNIQUE INDEX uq_hermes_jobs_idempotency
            ON hermes_worker_jobs(project_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """)
    )


def downgrade() -> None:
    op.drop_index("uq_hermes_jobs_idempotency", table_name="hermes_worker_jobs")
    op.drop_index("idx_hermes_jobs_project_status", table_name="hermes_worker_jobs")
    op.drop_index("idx_hermes_jobs_status_priority", table_name="hermes_worker_jobs")
    op.drop_table("hermes_worker_jobs")

    op.drop_index("uq_agent_sessions_hermes_project", table_name="agent_sessions")
    op.drop_index("idx_agent_sessions_kind_status", table_name="agent_sessions")
    op.drop_column("agent_sessions", "worker_role")
    op.drop_column("agent_sessions", "session_kind")

    op.drop_index("idx_harness_projects_active_slot", table_name="harness_projects")
    op.drop_column("harness_projects", "is_active_slot")

    op.drop_table("system_license_policy")
