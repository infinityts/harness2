"""persist_contract_rules

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'agent_sessions',
        sa.Column(
            'system_contract_rules',
            postgresql.JSONB(),
            nullable=False,
            server_default='[]'
        )
    )


def downgrade() -> None:
    op.drop_column('agent_sessions', 'system_contract_rules')
