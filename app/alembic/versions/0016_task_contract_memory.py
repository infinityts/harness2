"""task_contract_memory

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


MODEL_PROFILES = [
    {
        "name": "local-32k",
        "display_name": "Local 32k",
        "provider": "local",
        "model_id": "local-32k",
        "context_window_tokens": 32000,
        "max_input_tokens": 20000,
        "max_output_tokens": 6000,
        "reserved_tokens": 6000,
        "chars_per_token": 4.0,
        "is_default": True,
    },
    {
        "name": "local-64k",
        "display_name": "Local 64k",
        "provider": "local",
        "model_id": "local-64k",
        "context_window_tokens": 64000,
        "max_input_tokens": 40000,
        "max_output_tokens": 8000,
        "reserved_tokens": 16000,
        "chars_per_token": 3.7,
        "is_default": False,
    },
    {
        "name": "local-120k",
        "display_name": "Local 120k",
        "provider": "local",
        "model_id": "local-120k",
        "context_window_tokens": 120000,
        "max_input_tokens": 64000,
        "max_output_tokens": 16000,
        "reserved_tokens": 40000,
        "chars_per_token": 3.6,
        "is_default": False,
    },
    {
        "name": "cloud-128k",
        "display_name": "Cloud 128k",
        "provider": "cloud",
        "model_id": "cloud-128k",
        "context_window_tokens": 128000,
        "max_input_tokens": 72000,
        "max_output_tokens": 16000,
        "reserved_tokens": 40000,
        "chars_per_token": 3.8,
        "is_default": False,
    },
]


def upgrade() -> None:
    op.create_table(
        "harness_model_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model_id", sa.String(150), nullable=False),
        sa.Column("context_window_tokens", sa.Integer(), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("chars_per_token", sa.Numeric(4, 2), nullable=False, server_default="3.6"),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for profile in MODEL_PROFILES:
        op.get_bind().execute(
            sa.text("""
                INSERT INTO harness_model_profiles
                    (name, display_name, provider, model_id, context_window_tokens,
                     max_input_tokens, max_output_tokens, reserved_tokens,
                     chars_per_token, is_default)
                VALUES
                    (:name, :display_name, :provider, :model_id, :context_window_tokens,
                     :max_input_tokens, :max_output_tokens, :reserved_tokens,
                     :chars_per_token, :is_default)
                ON CONFLICT (name) DO NOTHING
            """),
            profile,
        )

    op.add_column("agent_sessions", sa.Column("model_profile", sa.String(80), nullable=False, server_default="local-32k"))
    op.add_column("agent_sessions", sa.Column("context_compactions", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_sessions", sa.Column("last_compacted_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("feature_consensus_plans", sa.Column("model_profile", sa.String(80), nullable=False, server_default="local-32k"))
    op.add_column("feature_consensus_plans", sa.Column("plan_budget_tokens", sa.Integer(), nullable=False, server_default="20000"))
    op.add_column("feature_consensus_plans", sa.Column("plan_metrics", postgresql.JSONB(), nullable=False, server_default="{}"))

    op.add_column("harness_tasks", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("harness_tasks", sa.Column("task_context", sa.Text(), nullable=True))
    op.add_column("harness_tasks", sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("harness_tasks", sa.Column("test_commands", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("harness_tasks", sa.Column("depends_on", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("harness_tasks", sa.Column("allowed_symbols", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("harness_tasks", sa.Column("estimated_input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("harness_tasks", sa.Column("max_input_tokens", sa.Integer(), nullable=False, server_default="20000"))
    op.add_column("harness_tasks", sa.Column("handoff_summary", sa.Text(), nullable=True))
    op.add_column("harness_tasks", sa.Column("context_manifest", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("harness_tasks", sa.Column("memory_epoch", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("harness_tasks", sa.Column("task_kind", sa.String(30), nullable=False, server_default="IMPLEMENT"))
    op.add_column("harness_tasks", sa.Column("budget_status", sa.String(30), nullable=False, server_default="OK"))
    op.add_column("harness_tasks", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))

    op.create_table(
        "harness_task_memory",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("harness_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("files", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tests", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("next_steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_task_memory_task_created", "harness_task_memory", ["task_id", "created_at"])
    op.create_index("idx_task_memory_plan", "harness_task_memory", ["plan_id"])

    op.create_table(
        "harness_plan_memory",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("feature_consensus_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_plan_memory_plan_created", "harness_plan_memory", ["plan_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_plan_memory_plan_created", table_name="harness_plan_memory")
    op.drop_table("harness_plan_memory")
    op.drop_index("idx_task_memory_plan", table_name="harness_task_memory")
    op.drop_index("idx_task_memory_task_created", table_name="harness_task_memory")
    op.drop_table("harness_task_memory")

    for column in (
        "updated_at", "budget_status", "task_kind", "memory_epoch", "context_manifest",
        "handoff_summary", "max_input_tokens", "estimated_input_tokens", "allowed_symbols",
        "depends_on", "test_commands", "acceptance_criteria", "task_context", "description",
    ):
        op.drop_column("harness_tasks", column)

    op.drop_column("feature_consensus_plans", "plan_metrics")
    op.drop_column("feature_consensus_plans", "plan_budget_tokens")
    op.drop_column("feature_consensus_plans", "model_profile")

    op.drop_column("agent_sessions", "last_compacted_at")
    op.drop_column("agent_sessions", "context_compactions")
    op.drop_column("agent_sessions", "model_profile")

    op.drop_table("harness_model_profiles")
