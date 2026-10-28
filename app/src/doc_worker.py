"""Worker interno de documentación RAG para Harness2.

No usa un contenedor ni un IDE: crea una sesión real en `agent_sessions` con
`is_headless_worker = true`, ligada a un único `project_id`, para que aparezca en
la topología del War Room y pueda revocarse con el Kill Switch.
"""
import hashlib
import json
import secrets
import uuid
from typing import Any, Dict

import sqlalchemy as sa

from src.doc_rag import bootstrap_project_docs
from src.war_room import record_event

HEADLESS_SKILLS = ["tech-writer", "doc-analyzer", "rag-docs-curator"]
WORKER_USERNAME = "docs-curator"
WORKER_TOOL = "headless-worker"
WORKER_PROVIDER = "internal-rag"


def _project(conn, project_id: str):
    return conn.execute(
        sa.text("""
            SELECT id::text, project_slug, display_name
            FROM harness_projects WHERE id = CAST(:pid AS UUID) FOR UPDATE
        """),
        {"pid": project_id}
    ).mappings().fetchone()


def ensure_headless_worker_session(
    conn,
    project_id: str,
    create_if_revoked: bool = False,
) -> Dict[str, Any]:
    project = _project(conn, project_id)
    if not project:
        raise ValueError("Project not found")

    rows = conn.execute(
        sa.text("""
            SELECT id::text, status FROM agent_sessions
            WHERE project_id = CAST(:pid AS UUID) AND is_headless_worker = true
            ORDER BY connected_at DESC
            FOR UPDATE
        """),
        {"pid": project_id}
    ).mappings().all()
    existing = next((row for row in rows if row["status"] != "REVOKED"), None)
    revoked = next((row for row in rows if row["status"] == "REVOKED"), None)

    if existing:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'ONLINE', last_heartbeat = NOW(),
                    skills = CAST(:skills AS JSONB), max_context_tokens = 32000
                WHERE id = CAST(:sid AS UUID)
            """),
            {"sid": existing["id"], "skills": json.dumps(HEADLESS_SKILLS)}
        )
        return {
            "session_id": existing["id"],
            "project_id": project_id,
            "project_slug": project["project_slug"],
            "created": False,
            "status": "ONLINE",
        }

    if revoked and not create_if_revoked:
        return {
            "session_id": revoked["id"],
            "project_id": project_id,
            "project_slug": project["project_slug"],
            "created": False,
            "status": "REVOKED",
            "skipped": True,
        }

    session_id = str(uuid.uuid4())
    session_key_hash = hashlib.sha256(secrets.token_urlsafe(48).encode("utf-8")).hexdigest()
    conn.execute(
        sa.text("""
            INSERT INTO agent_sessions
                (id, engineer_username, agent_tool, llm_provider, session_key_hash,
                 skills, status, max_context_tokens, current_context_tokens,
                 last_heartbeat, connected_at, user_id, project_id, is_headless_worker)
            VALUES
                (CAST(:id AS UUID), :username, :tool, :provider, :khash,
                 CAST(:skills AS JSONB), 'ONLINE', 32000, 0,
                 NOW(), NOW(), NULL, CAST(:pid AS UUID), true)
        """),
        {
            "id": session_id,
            "username": WORKER_USERNAME,
            "tool": WORKER_TOOL,
            "provider": WORKER_PROVIDER,
            "khash": session_key_hash,
            "skills": json.dumps(HEADLESS_SKILLS),
            "pid": project_id,
        }
    )
    return {
        "session_id": session_id,
        "project_id": project_id,
        "project_slug": project["project_slug"],
        "created": True,
        "status": "ONLINE",
    }


def run_doc_worker_job(
    engine,
    project_id: str,
    trigger: str = "manual",
    create_if_revoked: bool = False,
) -> Dict[str, Any]:
    try:
        with engine.begin() as conn:
            worker = ensure_headless_worker_session(conn, project_id, create_if_revoked)
        if worker.get("skipped"):
            return {"skipped": True, "reason": "headless worker revoked", **worker}

        record_event(
            engine,
            "HEADLESS_WORKER_READY",
            {
                "session_id": worker["session_id"],
                "project_id": project_id,
                "project_slug": worker["project_slug"],
                "skills": HEADLESS_SKILLS,
                "trigger": trigger,
                "created": worker["created"],
            },
            source_session_id=worker["session_id"],
        )

        with engine.begin() as conn:
            marked = conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET status = 'BUSY', last_heartbeat = NOW()
                    WHERE id = CAST(:sid AS UUID) AND status IN ('ONLINE', 'BUSY')
                """),
                {"sid": worker["session_id"]}
            )
            if marked.rowcount == 0:
                return {
                    "skipped": True,
                    "reason": "headless worker revoked",
                    "session_id": worker["session_id"],
                    "project_id": project_id,
                    "status": "REVOKED",
                }
            # Serializa los bootstrap del mismo proyecto (alta + manual + merge).
            conn.execute(
                sa.text("SELECT id FROM harness_projects WHERE id = CAST(:pid AS UUID) FOR UPDATE"),
                {"pid": project_id}
            )
            summary = bootstrap_project_docs(
                conn, project_id, worker["project_slug"],
                worker["session_id"], trigger,
            )

        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET status = 'ONLINE', last_heartbeat = NOW()
                    WHERE id = CAST(:sid AS UUID) AND status IN ('ONLINE', 'BUSY')
                """),
                {"sid": worker["session_id"]}
            )
        record_event(
            engine,
            "DOCS_BOOTSTRAP_COMPLETED",
            {
                "project_id": project_id,
                "project_slug": worker["project_slug"],
                "session_id": worker["session_id"],
                "trigger": trigger,
                "scanned": summary.get("scanned", 0),
                "created": summary.get("created", 0),
                "updated": summary.get("updated", 0),
                "skipped": summary.get("skipped", 0),
                "documents_count": summary.get("documents_count", 0),
                "generated": summary.get("generated", []),
                "errors": summary.get("errors", []),
            },
            source_session_id=worker["session_id"],
        )
        return summary
    except Exception as exc:
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text("""
                        UPDATE agent_sessions
                        SET status = 'ONLINE', last_heartbeat = NOW()
                        WHERE project_id = CAST(:pid AS UUID)
                          AND is_headless_worker = true AND status = 'BUSY'
                    """),
                    {"pid": project_id}
                )
        except Exception:
            pass
        record_event(
            engine,
            "DOCS_BOOTSTRAP_FAILED",
            {"project_id": project_id, "trigger": trigger, "error": str(exc)},
        )
        raise
