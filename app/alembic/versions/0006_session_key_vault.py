"""session_key_vault

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_sessions', sa.Column('session_key_vault', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_sessions', 'session_key_vault')
