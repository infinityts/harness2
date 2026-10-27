"""align_harness_schema

Revision ID: 0002_align
Revises: 0001
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = '0002_align'
down_revision = '0001'  # ID real de la revision 0001_initial_harness_schema.py
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Como la base esta recien creada y sin datos, recreamos las 5 tablas con el contrato exacto de columnas
    op.execute("DROP TABLE IF EXISTS harness_tasks CASCADE;")
    op.execute("DROP TABLE IF EXISTS feature_consensus_plans CASCADE;")
    op.execute("DROP TABLE IF EXISTS code_ast_vectors CASCADE;")
    op.execute("DROP TABLE IF EXISTS repo_file_map CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_sessions CASCADE;")

    # 1. Sesiones Conectadas de Agentes
    op.create_table(
        'agent_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('engineer_username', sa.String(100), nullable=False),
        sa.Column('agent_tool', sa.String(50), nullable=False), # opencode, claudecode, cursor, hermes, pi
        sa.Column('llm_provider', sa.String(100), nullable=False),
        sa.Column('session_key_hash', sa.String(256), unique=True, nullable=False),
        sa.Column('skills', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('status', sa.String(20), nullable=False, server_default='ONLINE'), # ONLINE, BUSY, OFFLINE, REVOKED
        sa.Column('max_context_tokens', sa.Integer(), nullable=False, server_default='32000'),
        sa.Column('current_context_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_heartbeat', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    )
    op.create_index('idx_agent_sessions_status_skills', 'agent_sessions', ['status', 'skills'], postgresql_using='gin')

    # 2. Mapa Rapido de Archivos del Repositorio
    op.create_table(
        'repo_file_map',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('repo_name', sa.String(150), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('commit_sha', sa.String(40), nullable=False),
        sa.Column('quick_summary', sa.Text(), nullable=False),
        sa.Column('exported_symbols', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('imports', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('locked_by_task_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint('repo_name', 'file_path', name='uq_repo_file_path')
    )

    # 3. Embeddings por Funcion/Metodo AST (768 dims para modelos locales/ONNX como nomic-embed-text / bge-base)
    op.create_table(
        'code_ast_vectors',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('file_id', sa.BigInteger(), sa.ForeignKey('repo_file_map.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol_name', sa.String(255), nullable=False),
        sa.Column('symbol_signature', sa.Text(), nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('code_snippet', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=False)
    )
    op.execute(
        "CREATE INDEX idx_code_ast_vectors_hnsw ON code_ast_vectors "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);"
    )

    # 4. Motor Obligatorio de Doble Plan + Juez Sintetizador
    op.create_table(
        'feature_consensus_plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('requested_by', sa.String(100), nullable=False),
        sa.Column('requirement_prompt', sa.Text(), nullable=False),
        sa.Column('plan_a_json', postgresql.JSONB(), nullable=False),
        sa.Column('plan_b_json', postgresql.JSONB(), nullable=False),
        sa.Column('arbiter_justification', sa.Text(), nullable=False),
        sa.Column('master_plan_c_json', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='APPROVED'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )

    # 5. Micro-tareas Acotadas y Contratos de Ejecucion
    op.create_table(
        'harness_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('feature_consensus_plans.id'), nullable=False),
        sa.Column('assigned_session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agent_sessions.id'), nullable=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('required_skills', postgresql.JSONB(), nullable=False),
        sa.Column('git_branch', sa.String(150), nullable=False),
        sa.Column('allowed_files', postgresql.JSONB(), nullable=False),
        sa.Column('allow_dependency_changes', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('failed_attempts_memory', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )

def downgrade() -> None:
    pass
