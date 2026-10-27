"""iam_and_projects_governance

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""
import os
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from src.security import hash_password

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Usuarios (ingenieros y administradores)
    op.create_table(
        'harness_users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('full_name', sa.String(150), nullable=False),
        sa.Column('password_hash', sa.String(256), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='ENGINEER'),
        sa.Column('status', sa.String(20), nullable=False, server_default='INVITED'),
        sa.Column('invite_token', sa.String(128), nullable=True),
        sa.Column('default_skills', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2. Proyectos gobernados desde el Portal
    op.create_table(
        'harness_projects',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('project_slug', sa.String(150), nullable=False, unique=True),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('repo_url', sa.Text(), nullable=True),
        sa.Column('default_branch', sa.String(80), nullable=False, server_default='main'),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('harness_users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 3. Membresias y skills aprobados por proyecto
    op.create_table(
        'project_memberships',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('project_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('harness_projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('harness_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('approved_by_admin_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('harness_users.id'), nullable=True),
        sa.Column('allowed_skills', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('status', sa.String(20), nullable=False, server_default='APPROVED'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('project_id', 'user_id', name='uq_project_user_membership'),
    )
    op.create_index('idx_project_memberships_user', 'project_memberships', ['user_id'])
    op.create_index('idx_project_memberships_project', 'project_memberships', ['project_id'])

    # 4. Aislamiento sesion -> usuario/proyecto
    op.add_column('agent_sessions', sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('agent_sessions', sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_agent_sessions_user_id', 'agent_sessions', 'harness_users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_agent_sessions_project_id', 'agent_sessions', 'harness_projects', ['project_id'], ['id'], ondelete='CASCADE')
    op.create_index('idx_agent_sessions_user_id', 'agent_sessions', ['user_id'])
    op.create_index('idx_agent_sessions_project_id', 'agent_sessions', ['project_id'])

    # 5. Aislamiento de planes por proyecto
    op.add_column('feature_consensus_plans', sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_feature_plans_project_id', 'feature_consensus_plans', 'harness_projects', ['project_id'], ['id'], ondelete='CASCADE')
    op.create_index('idx_feature_plans_project_id', 'feature_consensus_plans', ['project_id'])

    # 6. Seed: administrador inicial
    conn = op.get_bind()
    admin_email = os.getenv("HARNESS_INITIAL_ADMIN_EMAIL", "admin@harness.local")
    admin_password = os.getenv("HARNESS_INITIAL_ADMIN_PASSWORD", "AdminHarness2026!")
    admin_id = str(uuid.uuid4())

    conn.execute(
        sa.text("""
            INSERT INTO harness_users
                (id, email, full_name, password_hash, role, status, default_skills)
            VALUES
                (CAST(:id AS UUID), :email, 'Harness Admin', :password_hash, 'ADMIN', 'ACTIVE',
                 CAST('[]' AS JSONB))
            ON CONFLICT (email) DO NOTHING
        """),
        {"id": admin_id, "email": admin_email, "password_hash": hash_password(admin_password)}
    )
    admin_id = str(conn.execute(
        sa.text("SELECT id::text FROM harness_users WHERE email = :email"),
        {"email": admin_email}
    ).scalar_one())

    # 7. Seed: proyecto inicial harness-self
    project_id = str(uuid.uuid4())
    conn.execute(
        sa.text("""
            INSERT INTO harness_projects
                (id, project_slug, display_name, description, repo_url, default_branch, created_by_user_id)
            VALUES
                (CAST(:id AS UUID), 'harness-self', 'Harness Self',
                 'Proyecto inicial de auto-indexacion del Control Plane',
                 NULL, 'main', CAST(:admin_id AS UUID))
            ON CONFLICT (project_slug) DO NOTHING
        """),
        {"id": project_id, "admin_id": admin_id}
    )
    project_id = str(conn.execute(
        sa.text("SELECT id::text FROM harness_projects WHERE project_slug = 'harness-self'")
    ).scalar_one())

    # 8. Membership APPROVED del admin en el proyecto inicial
    conn.execute(
        sa.text("""
            INSERT INTO project_memberships
                (id, project_id, user_id, approved_by_admin_id, allowed_skills, status)
            VALUES
                (CAST(:id AS UUID), CAST(:project_id AS UUID), CAST(:admin_id AS UUID),
                 CAST(:admin_id AS UUID), CAST('[]' AS JSONB), 'APPROVED')
            ON CONFLICT (project_id, user_id) DO NOTHING
        """),
        {"id": str(uuid.uuid4()), "project_id": project_id, "admin_id": admin_id}
    )


def downgrade() -> None:
    op.drop_index('idx_feature_plans_project_id', table_name='feature_consensus_plans')
    op.drop_constraint('fk_feature_plans_project_id', 'feature_consensus_plans', type_='foreignkey')
    op.drop_column('feature_consensus_plans', 'project_id')

    op.drop_index('idx_agent_sessions_project_id', table_name='agent_sessions')
    op.drop_index('idx_agent_sessions_user_id', table_name='agent_sessions')
    op.drop_constraint('fk_agent_sessions_project_id', 'agent_sessions', type_='foreignkey')
    op.drop_constraint('fk_agent_sessions_user_id', 'agent_sessions', type_='foreignkey')
    op.drop_column('agent_sessions', 'project_id')
    op.drop_column('agent_sessions', 'user_id')

    op.drop_index('idx_project_memberships_project', table_name='project_memberships')
    op.drop_index('idx_project_memberships_user', table_name='project_memberships')
    op.drop_table('project_memberships')
    op.drop_table('harness_projects')
    op.drop_table('harness_users')
