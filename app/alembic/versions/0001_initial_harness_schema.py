"""initial harness schema

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

UUID_PK = dict(server_default=sa.text('uuid_generate_v4()'), primary_key=True)


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS btree_gin')

    # Sesiones efimeras de agentes (heartbeat via WebSocket)
    op.create_table(
        'agent_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), **UUID_PK),
        sa.Column('agent_name', sa.String(100), nullable=False),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('skills', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('status', sa.String(20), server_default='OFFLINE', nullable=False),  # ONLINE, OFFLINE, BUSY
        sa.Column('current_context_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_context_tokens', sa.Integer(), server_default='32000', nullable=False),
        sa.Column('last_heartbeat', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_agent_sessions_status_skills', 'agent_sessions',
                    ['status', 'skills'], postgresql_using='gin')

    # Mapa de archivos del repositorio
    op.create_table(
        'repo_file_map',
        sa.Column('id', postgresql.UUID(as_uuid=True), **UUID_PK),
        sa.Column('repo_name', sa.String(150), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('language', sa.String(50), nullable=True),
        sa.Column('content_hash', sa.String(64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('repo_name', 'file_path', name='uq_repo_file'),
    )

    # Embeddings de nodos AST del codigo
    op.create_table(
        'code_ast_vectors',
        sa.Column('id', postgresql.UUID(as_uuid=True), **UUID_PK),
        sa.Column('file_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('repo_file_map.id', ondelete='CASCADE'), nullable=False),
        sa.Column('node_type', sa.String(50), nullable=False),
        sa.Column('symbol_name', sa.String(255), nullable=True),
        sa.Column('start_line', sa.Integer(), nullable=True),
        sa.Column('end_line', sa.Integer(), nullable=True),
        sa.Column('embedding', Vector(1536), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute('CREATE INDEX idx_code_ast_vectors_embedding ON code_ast_vectors '
               'USING hnsw (embedding vector_cosine_ops)')

    # Planes de consenso por feature
    op.create_table(
        'feature_consensus_plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), **UUID_PK),
        sa.Column('feature_name', sa.String(200), nullable=False),
        sa.Column('plan', postgresql.JSONB(), nullable=False),
        sa.Column('approved_by', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('status', sa.String(30), server_default='DRAFT', nullable=False),  # DRAFT, APPROVED, REJECTED
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'harness_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), **UUID_PK),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('feature_consensus_plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assigned_session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('agent_sessions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('required_skills', postgresql.JSONB(), nullable=False),
        sa.Column('git_branch', sa.String(150), nullable=False),
        sa.Column('allowed_files', postgresql.JSONB(), nullable=False),  # Contrato estricto de archivos permitidos
        sa.Column('allow_dependency_changes', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('failed_attempts_memory', postgresql.JSONB(), server_default='[]', nullable=False),  # Memoria persistente tras Context Purge
        sa.Column('status', sa.String(30), nullable=False, server_default='PENDING'),  # PENDING, IN_PROGRESS, VALIDATING, MERGED, BLOCKED
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )


def downgrade() -> None:
    op.drop_table('harness_tasks')
    op.drop_table('feature_consensus_plans')
    op.drop_table('code_ast_vectors')
    op.drop_table('repo_file_map')
    op.drop_table('agent_sessions')
