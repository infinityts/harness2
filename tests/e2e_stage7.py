"""E2E Etapa 7 - Parte A: migracion 0005 IAM/proyectos y seeds."""
import json
import os
import sys
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

sys.path.insert(0, "/app")
from src.security import verify_password

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_INITIAL_ADMIN_EMAIL", "admin@harness.local")
ADMIN_PASSWORD = os.environ.get("HARNESS_INITIAL_ADMIN_PASSWORD", "AdminHarness2026!")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def main():
    print("== ETAPA 7 - PARTE A ==")

    print("\n--- 1. Alembic 0005 ---")
    version = rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    print("version:", version)
    assert version == "0005"

    print("\n--- 2. Tablas y columnas de aislamiento ---")
    tables = {r["table_name"] for r in rows("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_name IN ('harness_users','harness_projects','project_memberships')
    """)}
    print("tables:", sorted(tables))
    assert tables == {"harness_users", "harness_projects", "project_memberships"}

    columns = {(r["table_name"], r["column_name"]) for r in rows("""
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema='public' AND (
            (table_name='agent_sessions' AND column_name IN ('user_id','project_id')) OR
            (table_name='feature_consensus_plans' AND column_name='project_id')
        )
    """)}
    print("columns:", sorted(columns))
    assert ("agent_sessions", "user_id") in columns
    assert ("agent_sessions", "project_id") in columns
    assert ("feature_consensus_plans", "project_id") in columns

    constraints = {r["conname"] for r in rows("""
        SELECT conname FROM pg_constraint
        WHERE conname IN (
            'harness_users_email_key',
            'harness_projects_project_slug_key',
            'uq_project_user_membership',
            'fk_agent_sessions_user_id',
            'fk_agent_sessions_project_id',
            'fk_feature_plans_project_id'
        )
    """)}
    print("constraints:", sorted(constraints))
    assert len(constraints) == 6

    print("\n--- 3. Seed del administrador ---")
    admin = rows("SELECT * FROM harness_users WHERE email = :email", email=ADMIN_EMAIL)
    print("admin:", [(r["email"], r["role"], r["status"]) for r in admin])
    assert admin
    admin = admin[0]
    assert admin["role"] == "ADMIN" and admin["status"] == "ACTIVE"
    assert admin["default_skills"] == []
    assert admin["password_hash"].startswith("pbkdf2_sha256$")
    assert verify_password(ADMIN_PASSWORD, admin["password_hash"]) is True
    assert verify_password(ADMIN_PASSWORD + "-wrong", admin["password_hash"]) is False

    print("\n--- 4. Seed del proyecto y membership ---")
    project = rows("SELECT * FROM harness_projects WHERE project_slug = 'harness-self'")
    print("project:", [(r["project_slug"], r["default_branch"], r["created_by_user_id"]) for r in project])
    assert project
    project = project[0]
    assert project["project_slug"] == "harness-self"
    assert project["default_branch"] == "main"
    assert project["created_by_user_id"] == admin["id"]

    membership = rows("""
        SELECT m.status, m.allowed_skills, m.approved_by_admin_id::text AS approved_by
        FROM project_memberships m
        WHERE m.project_id = :pid AND m.user_id = :uid
    """, pid=project["id"], uid=admin["id"])
    print("membership:", [dict(m) for m in membership])
    assert membership and membership[0]["status"] == "APPROVED"
    assert membership[0]["allowed_skills"] == []
    assert membership[0]["approved_by"] == str(admin["id"])

    print("\n--- 5. App y repos existentes ---")
    with urllib.request.urlopen(BASE + "/health", timeout=10) as resp:
        health = json.loads(resp.read().decode())
    print("health:", health["status"], health["service"], "ttl:", health.get("session_jwt_ttl_hours"))
    assert resp.status == 200 and health["status"] == "ok"

    repos = rows("SELECT DISTINCT repo_name FROM repo_file_map ORDER BY repo_name")
    print("repos indexados:", [r["repo_name"] for r in repos])
    assert any(r["repo_name"] == "harness-self" for r in repos)

    print("\nE2E STAGE 7A: OK")


main()
