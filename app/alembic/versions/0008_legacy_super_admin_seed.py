"""legacy_super_admin_seed

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""
import os
import uuid

from alembic import op
import sqlalchemy as sa

from src.security import hash_password

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Credencial legacy solicitada para el primer acceso. Debe cambiarse desde
    # el portal inmediatamente; el hash PBKDF2 es lo unico que se persiste.
    admin_email = os.getenv("HARNESS_LEGACY_ADMIN_EMAIL", "admin@local").strip().lower()
    admin_password = os.getenv("HARNESS_LEGACY_ADMIN_PASSWORD", "admin")
    admin_id = str(uuid.uuid4())

    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO harness_users
                (id, email, full_name, password_hash, role, status, default_skills)
            VALUES
                (CAST(:id AS UUID), :email, 'Super Administrator', :password_hash,
                 'ADMIN', 'ACTIVE', CAST('[]' AS JSONB))
            ON CONFLICT (email) DO NOTHING
        """),
        {
            "id": admin_id,
            "email": admin_email,
            "password_hash": hash_password(admin_password),
        },
    )
    admin_id = str(conn.execute(
        sa.text("SELECT id::text FROM harness_users WHERE email = :email"),
        {"email": admin_email},
    ).scalar_one())

    # Membresia en el proyecto inicial para que el portal sea usable al primer login.
    conn.execute(
        sa.text("""
            INSERT INTO project_memberships
                (id, project_id, user_id, approved_by_admin_id, allowed_skills, status)
            SELECT
                CAST(:membership_id AS UUID), p.id, CAST(:admin_id AS UUID),
                CAST(:admin_id AS UUID), CAST('[]' AS JSONB), 'APPROVED'
            FROM harness_projects p
            WHERE p.project_slug = 'harness-self'
            ON CONFLICT (project_id, user_id) DO NOTHING
        """),
        {"membership_id": str(uuid.uuid4()), "admin_id": admin_id},
    )


def downgrade() -> None:
    # No se elimina la cuenta en downgrade: pudo haber cambiado su password y
    # tener trabajo asociado. La semilla es intencionalmente idempotente.
    pass
