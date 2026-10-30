"""minimax_code_profile

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


PROFILE = {
    "name": "minimax-m3.1-flash",
    "display_name": "MiniMax M3.1 Flash",
    "provider": "minimax",
    "model_id": "MiniMax-M3.1-Flash",
    "context_window_tokens": 1000000,
    "max_input_tokens": 128000,
    "max_output_tokens": 32000,
    "reserved_tokens": 840000,
    "chars_per_token": 3.8,
    "supports_tools": True,
    "is_default": False,
    "tokenizer_kind": "heuristic",
    "tokenizer_model": "MiniMax-M3.1-Flash",
    "tokenizer_status": "HEURISTIC",
}


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("""
            INSERT INTO harness_model_profiles
                (name, display_name, provider, model_id, context_window_tokens,
                 max_input_tokens, max_output_tokens, reserved_tokens,
                 chars_per_token, supports_tools, is_default,
                 tokenizer_kind, tokenizer_model, tokenizer_status)
            VALUES
                (:name, :display_name, :provider, :model_id, :context_window_tokens,
                 :max_input_tokens, :max_output_tokens, :reserved_tokens,
                 :chars_per_token, :supports_tools, :is_default,
                 :tokenizer_kind, :tokenizer_model, :tokenizer_status)
            ON CONFLICT (name) DO NOTHING
        """),
        PROFILE,
    )


def downgrade() -> None:
    op.execute("DELETE FROM harness_model_profiles WHERE name = 'minimax-m3.1-flash'")
