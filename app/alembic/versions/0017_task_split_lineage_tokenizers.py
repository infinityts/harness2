"""task_split_lineage_and_tokenizers

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_kind", sa.String(40), nullable=False, server_default="auto"),
    )
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_model", sa.String(150), nullable=True),
    )
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_timeout_ms", sa.Integer(), nullable=False, server_default="2000"),
    )
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_status", sa.String(30), nullable=False, server_default="HEURISTIC"),
    )
    op.add_column(
        "harness_model_profiles",
        sa.Column("tokenizer_calibration", postgresql.JSONB(), nullable=False, server_default="{}"),
    )

    op.add_column(
        "harness_tasks",
        sa.Column("parent_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_harness_tasks_parent_task",
        "harness_tasks",
        "harness_tasks",
        ["parent_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "harness_tasks",
        sa.Column("split_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "harness_tasks",
        sa.Column("split_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "harness_tasks",
        sa.Column("split_reason", sa.String(80), nullable=True),
    )
    op.add_column(
        "harness_tasks",
        sa.Column("split_status", sa.String(30), nullable=False, server_default="NONE"),
    )
    op.create_index("idx_harness_tasks_parent", "harness_tasks", ["parent_task_id"])

    op.create_table(
        "harness_tokenizer_samples",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("profile_name", sa.String(80), nullable=False),
        sa.Column("model_id", sa.String(150), nullable=True),
        sa.Column("sample_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chars_per_token", sa.Numeric(8, 4), nullable=True),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_tokenizer_samples_profile", "harness_tokenizer_samples", ["profile_name", "created_at"])

    op.execute("""
        UPDATE harness_model_profiles
        SET tokenizer_kind = 'openai_compatible',
            tokenizer_status = 'HEURISTIC'
        WHERE name = 'cloud-128k'
    """)
    op.execute("""
        UPDATE harness_model_profiles
        SET tokenizer_kind = 'auto',
            tokenizer_status = 'HEURISTIC'
        WHERE tokenizer_kind = 'auto'
    """)


def downgrade() -> None:
    op.drop_index("idx_tokenizer_samples_profile", table_name="harness_tokenizer_samples")
    op.drop_table("harness_tokenizer_samples")
    op.drop_index("idx_harness_tasks_parent", table_name="harness_tasks")
    op.drop_constraint("fk_harness_tasks_parent_task", "harness_tasks", type_="foreignkey")
    for column in (
        "split_status", "split_reason", "split_count", "split_index", "parent_task_id",
    ):
        op.drop_column("harness_tasks", column)
    for column in (
        "tokenizer_calibration", "tokenizer_status", "tokenizer_timeout_ms",
        "tokenizer_path", "tokenizer_model", "tokenizer_url", "tokenizer_kind",
    ):
        op.drop_column("harness_model_profiles", column)
