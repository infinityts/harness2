"""Pool de workers Hermes por proyecto.

Una sesion `SYSTEM_WORKER` por proyecto activo. El contenedor `hermes-worker`
reclama jobs de `hermes_worker_jobs`, ejecuta Hermes y reporta el resultado.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import jwt
import sqlalchemy as sa

from src.security import open_secret, seal_secret

JWT_SECRET = os.getenv("HARNESS_JWT_SECRET", "change-me-secret")
SESSION_JWT_TTL_HOURS = int(os.getenv("SESSION_JWT_TTL_HOURS", "12"))

WORKER_SKILLS = ["rag-docs-curator", "tech-writer", "doc-analyzer"]
WORKER_USERNAME = "hermes-rag-curator"
WORKER_TOOL = "hermes"
WORKER_PROVIDER = "hermes-worker"
WORKER_ROLE = "RAG_CURATOR"
JOB_LEASE_SECONDS = 300
JOB_TYPES = ("RAG_BOOTSTRAP", "RAG_INGEST_SOURCE", "RAG_REFRESH_PROJECT", "RAG_UPDATE_ARCHITECTURE")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _worker_session_key(session_id: str, project_id: str, project_slug: str) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": session_id,
        "user_id": None,
        "project_id": project_id,
        "project_slug": project_slug,
        "email": WORKER_USERNAME,
        "tool": WORKER_TOOL,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + SESSION_JWT_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _valid_worker_session_key(key: str, session_id: str) -> bool:
    try:
        decoded = jwt.decode(
            key, JWT_SECRET, algorithms=["HS256"],
            options={"require": ["exp", "jti", "sub"]},
        )
        return decoded.get("sub") == session_id
    except Exception:
        return False


def ensure_hermes_worker_session(
    conn,
    project_id: str,
    set_online: bool = True,
    initial_status: str = "ONLINE",
) -> Optional[Dict[str, Any]]:
    project = conn.execute(
        sa.text("SELECT project_slug FROM harness_projects WHERE id = CAST(:pid AS UUID)"),
        {"pid": project_id},
    ).fetchone()
    if not project:
        return None
    project_slug = project[0]
    row = conn.execute(
        sa.text("""
            SELECT id::text, status, session_key_vault, engineer_username
            FROM agent_sessions
            WHERE project_id = CAST(:pid AS UUID)
              AND session_kind = 'SYSTEM_WORKER'
              AND agent_tool = 'hermes'
              AND worker_role = 'RAG_CURATOR'
              AND status <> 'REVOKED'
            ORDER BY connected_at DESC
            LIMIT 1
            FOR UPDATE
        """),
        {"pid": project_id},
    ).mappings().fetchone()

    if row:
        if set_online:
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET status = CASE WHEN status = 'BUSY' THEN 'BUSY' ELSE 'ONLINE' END,
                        last_heartbeat = NOW(),
                        skills = CAST(:skills AS JSONB),
                        max_context_tokens = GREATEST(max_context_tokens, 32000)
                    WHERE id = CAST(:sid AS UUID)
                """),
                {"sid": row["id"], "skills": json.dumps(WORKER_SKILLS)},
            )
        key = open_secret(row["session_key_vault"] or "")
        if not _valid_worker_session_key(key, row["id"]):
            key = _worker_session_key(row["id"], project_id, project_slug)
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET session_key_hash = :khash, session_key_vault = :vault
                    WHERE id = CAST(:sid AS UUID)
                """),
                {"sid": row["id"], "khash": _hash_key(key), "vault": seal_secret(key)},
            )
        return {
            "session_id": row["id"],
            "project_id": project_id,
            "session_key": key,
            "status": row["status"],
            "created": False,
        }

    session_id = str(uuid.uuid4())
    session_key = _worker_session_key(session_id, project_id, project_slug)
    key_hash = _hash_key(session_key)
    vault = seal_secret(session_key)
    conn.execute(
        sa.text("""
            INSERT INTO agent_sessions
                (id, engineer_username, agent_tool, llm_provider, session_key_hash,
                 session_key_vault, skills, status, max_context_tokens,
                 current_context_tokens, last_heartbeat, connected_at,
                 user_id, project_id, is_headless_worker, session_kind, worker_role)
            VALUES
                (CAST(:id AS UUID), :username, :tool, :provider, :khash,
                 :vault, CAST(:skills AS JSONB), :status, 32000,
                 0, NOW(), NOW(), NULL, CAST(:pid AS UUID), false,
                 'SYSTEM_WORKER', 'RAG_CURATOR')
        """),
        {
            "id": session_id,
            "username": WORKER_USERNAME,
            "tool": WORKER_TOOL,
            "provider": WORKER_PROVIDER,
            "khash": key_hash,
            "vault": vault,
            "skills": json.dumps(WORKER_SKILLS),
            "pid": project_id,
            "status": initial_status,
        },
    )
    return {
        "session_id": session_id,
        "project_id": project_id,
        "session_key": session_key,
        "status": initial_status,
        "created": True,
    }


def deactivate_hermes_session(conn, project_id: str, status: str = "OFFLINE") -> None:
    conn.execute(
        sa.text("""
            UPDATE agent_sessions
            SET status = :status, last_heartbeat = NOW()
            WHERE project_id = CAST(:pid AS UUID)
              AND session_kind = 'SYSTEM_WORKER'
              AND agent_tool = 'hermes'
              AND status <> 'REVOKED'
        """),
        {"pid": project_id, "status": status},
    )


def enqueue_job(
    conn,
    project_id: str,
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    priority: int = 100,
) -> Dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unsupported job_type: {job_type}")
    params = {
        "pid": project_id,
        "job_type": job_type,
        "payload": json.dumps(payload or {}, ensure_ascii=False),
        "idempotency_key": idempotency_key,
        "priority": int(priority),
    }
    if idempotency_key:
        row = conn.execute(
            sa.text("""
                INSERT INTO hermes_worker_jobs
                    (project_id, job_type, payload, idempotency_key, priority)
                VALUES
                    (CAST(:pid AS UUID), :job_type, CAST(:payload AS JSONB),
                     :idempotency_key, :priority)
                ON CONFLICT (project_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING id::text
            """),
            params,
        ).fetchone()
        if row:
            return {"id": row[0], "created": True}
        existing = conn.execute(
            sa.text("""
                SELECT id::text FROM hermes_worker_jobs
                WHERE project_id = CAST(:pid AS UUID) AND idempotency_key = :idempotency_key
            """),
            params,
        ).fetchone()
        return {"id": existing[0] if existing else None, "created": False}

    row = conn.execute(
        sa.text("""
            INSERT INTO hermes_worker_jobs
                (project_id, job_type, payload, priority)
            VALUES
                (CAST(:pid AS UUID), :job_type, CAST(:payload AS JSONB), :priority)
            RETURNING id::text
        """),
        params,
    ).fetchone()
    return {"id": row[0], "created": True}


def _recover_expired_jobs(conn) -> None:
    conn.execute(
        sa.text("""
            UPDATE hermes_worker_jobs
            SET status = 'QUEUED',
                locked_by = NULL,
                lease_expires_at = NULL,
                error_message = COALESCE(error_message, 'lease expired'),
                updated_at = NOW()
            WHERE status = 'RUNNING'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
              AND attempts < max_attempts
        """)
    )
    conn.execute(
        sa.text("""
            UPDATE hermes_worker_jobs
            SET status = 'FAILED',
                locked_by = NULL,
                lease_expires_at = NULL,
                completed_at = NOW(),
                error_message = COALESCE(error_message, 'lease expired and max attempts reached'),
                updated_at = NOW()
            WHERE status = 'RUNNING'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
              AND attempts >= max_attempts
        """)
    )


def claim_job(conn, worker_id: str) -> Optional[Dict[str, Any]]:
    _recover_expired_jobs(conn)
    job = conn.execute(
        sa.text("""
            SELECT id::text, project_id::text, job_type, payload, attempts, max_attempts
            FROM hermes_worker_jobs
            WHERE status = 'QUEUED'
              AND EXISTS (
                  SELECT 1 FROM harness_projects p
                  WHERE p.id = hermes_worker_jobs.project_id
                    AND p.is_active_slot = true
              )
            ORDER BY priority ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """)
    ).mappings().fetchone()
    if not job:
        return None

    worker = ensure_hermes_worker_session(conn, job["project_id"], set_online=False)
    if not worker:
        raise RuntimeError("Could not provision Hermes worker session")

    conn.execute(
        sa.text("""
            UPDATE hermes_worker_jobs
            SET status = 'RUNNING',
                session_id = CAST(:sid AS UUID),
                attempts = attempts + 1,
                started_at = COALESCE(started_at, NOW()),
                lease_expires_at = NOW() + make_interval(secs => :lease),
                locked_by = :worker_id,
                updated_at = NOW()
            WHERE id = CAST(:jid AS UUID)
        """),
        {
            "sid": worker["session_id"],
            "lease": JOB_LEASE_SECONDS,
            "worker_id": worker_id,
            "jid": job["id"],
        },
    )
    conn.execute(
        sa.text("""
            UPDATE agent_sessions
            SET status = 'BUSY', last_heartbeat = NOW()
            WHERE id = CAST(:sid AS UUID)
        """),
        {"sid": worker["session_id"]},
    )
    project = conn.execute(
        sa.text("""
            SELECT id::text, project_slug, display_name, description
            FROM harness_projects WHERE id = CAST(:pid AS UUID)
        """),
        {"pid": job["project_id"]},
    ).mappings().one()
    repo_map = conn.execute(
        sa.text("""
            SELECT file_path, quick_summary, exported_symbols
            FROM repo_file_map
            WHERE repo_name = :repo_name
            ORDER BY file_path ASC
            LIMIT 80
        """),
        {"repo_name": project["project_slug"]},
    ).mappings().all()
    return {
        "job": {
            "id": job["id"],
            "project_id": job["project_id"],
            "job_type": job["job_type"],
            "payload": job["payload"] or {},
            "attempts": int(job["attempts"]) + 1,
            "max_attempts": int(job["max_attempts"]),
        },
        "session": worker,
        "project": dict(project),
        "repo_map": [dict(row) for row in repo_map],
    }


def heartbeat_job(conn, job_id: str, worker_id: str) -> bool:
    result = conn.execute(
        sa.text("""
            UPDATE hermes_worker_jobs
            SET lease_expires_at = NOW() + make_interval(secs => :lease),
                updated_at = NOW()
            WHERE id = CAST(:jid AS UUID)
              AND status = 'RUNNING'
              AND locked_by = :worker_id
        """),
        {"jid": job_id, "worker_id": worker_id, "lease": JOB_LEASE_SECONDS},
    )
    return result.rowcount > 0


def complete_job(conn, job_id: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    job = conn.execute(
        sa.text("""
            UPDATE hermes_worker_jobs
            SET status = 'COMPLETED',
                completed_at = NOW(),
                lease_expires_at = NULL,
                locked_by = NULL,
                payload = payload || CAST(:result AS JSONB),
                updated_at = NOW()
            WHERE id = CAST(:jid AS UUID)
            RETURNING id::text, project_id::text, session_id::text, job_type
        """),
        {"jid": job_id, "result": json.dumps({"result": result or {}}, ensure_ascii=False)},
    ).mappings().fetchone()
    if not job:
        raise LookupError("job not found")
    if job["session_id"]:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'ONLINE', current_context_tokens = 0, last_heartbeat = NOW()
                WHERE id = CAST(:sid AS UUID) AND status <> 'REVOKED'
            """),
            {"sid": job["session_id"]},
        )
    return dict(job)


def fail_job(conn, job_id: str, error_message: str) -> Dict[str, Any]:
    job = conn.execute(
        sa.text("""
            SELECT id::text, project_id::text, session_id::text, attempts, max_attempts
            FROM hermes_worker_jobs WHERE id = CAST(:jid AS UUID) FOR UPDATE
        """),
        {"jid": job_id},
    ).mappings().fetchone()
    if not job:
        raise LookupError("job not found")
    can_retry = int(job["attempts"]) < int(job["max_attempts"])
    new_status = "QUEUED" if can_retry else "FAILED"
    conn.execute(
        sa.text("""
            UPDATE hermes_worker_jobs
            SET status = :status,
                error_message = :error,
                locked_by = NULL,
                lease_expires_at = NULL,
                completed_at = CASE WHEN :status = 'FAILED' THEN NOW() ELSE NULL END,
                updated_at = NOW()
            WHERE id = CAST(:jid AS UUID)
        """),
        {"jid": job_id, "status": new_status, "error": error_message[:4000]},
    )
    if job["session_id"]:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'ONLINE', current_context_tokens = 0, last_heartbeat = NOW()
                WHERE id = CAST(:sid AS UUID) AND status <> 'REVOKED'
            """),
            {"sid": job["session_id"]},
        )
    return {
        "job_id": job_id,
        "project_id": job["project_id"],
        "session_id": job["session_id"],
        "status": new_status,
        "retry": can_retry,
    }


def sync_pool(conn, policy: Dict[str, Any]) -> Dict[str, Any]:
    max_sessions = int(policy["max_hermes_sessions"])
    projects = conn.execute(
        sa.text("""
            SELECT id::text, project_slug, display_name, description
            FROM harness_projects
            WHERE is_active_slot = true
            ORDER BY created_at ASC
        """)
    ).mappings().all()
    sessions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    active_ids = [row["id"] for row in projects]
    for index, project in enumerate(projects):
        if index >= max_sessions:
            skipped.append({"project_id": project["id"], "project_slug": project["project_slug"],
                            "reason": "max_hermes_sessions reached"})
            continue
        worker = ensure_hermes_worker_session(conn, project["id"], set_online=True)
        if not worker:
            continue
        sessions.append({
            "session_id": worker["session_id"],
            "session_key": worker["session_key"],
            "project_id": project["id"],
            "project_slug": project["project_slug"],
            "project_name": project["display_name"],
            "description": project["description"],
            "created": worker["created"],
        })

    if active_ids:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'OFFLINE', last_heartbeat = NOW()
                WHERE session_kind = 'SYSTEM_WORKER'
                  AND agent_tool = 'hermes'
                  AND status <> 'REVOKED'
                  AND project_id <> ALL(CAST(:active_ids AS UUID[]))
            """),
            {"active_ids": "{" + ",".join(active_ids) + "}"},
        )
    else:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'OFFLINE', last_heartbeat = NOW()
                WHERE session_kind = 'SYSTEM_WORKER'
                  AND agent_tool = 'hermes'
                  AND status <> 'REVOKED'
            """)
        )
    return {
        "sessions": sessions,
        "skipped": skipped,
        "max_parallel_rag_jobs": int(policy["max_parallel_rag_jobs"]),
        "plan_code": policy["plan_code"],
    }
