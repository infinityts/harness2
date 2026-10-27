"""war_room_topology

Revision ID: 0003
Revises: 0002_align
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002_align'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_links',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('source_session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('agent_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('target_session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('agent_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('link_type', sa.String(40), nullable=False),
        sa.Column('trigger_event', sa.String(40), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('source_session_id', 'target_session_id', 'link_type', name='uq_agent_link')
    )
    op.create_index('idx_agent_links_active', 'agent_links', ['active'])

    op.create_table(
        'war_room_events',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('event_type', sa.String(60), nullable=False),
        sa.Column('source_session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('target_session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    )
    op.create_index('idx_war_room_events_created_at', 'war_room_events', ['created_at'])
    op.create_index('idx_war_room_events_type', 'war_room_events', ['event_type'])


def downgrade() -> None:
    op.drop_table('war_room_events')
    op.drop_table('agent_links')
