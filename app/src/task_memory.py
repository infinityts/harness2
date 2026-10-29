"""Memoria episodica de tareas y planes.

La memoria no sustituye al modelo: guarda handoffs, decisiones, fallos y tests
para que una tarea pueda reanudarse despues de una compactacion o de un cambio
de agente sin reconstruir toda la conversacion.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import sqlalchemy as sa


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _task_row(conn, task_id: str):
    return conn.execute(
        sa.text("""
            SELECT t.id::text AS task_id, t.plan_id::text AS plan_id,
                   p.project_id::text AS project_id, t.title, t.status,
                   t.memory_epoch, t.handoff_summary, t.assigned_session_id::text AS assigned_session_id,
                   p.master_plan_c_json->>'repo_name' AS repo_name,
                   p.requirement_prompt
            FROM harness_tasks t
            JOIN feature_consensus_plans p ON p.id = t.plan_id
            WHERE t.id = CAST(:tid AS UUID)
        """),
        {"tid": task_id},
    ).mappings().first()


def record_memory(
    conn,
    task_id: str,
    kind: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    files: Optional[List[str]] = None,
    tests: Optional[List[str]] = None,
    next_steps: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    created_by: Optional[str] = None,
    token_estimate: int = 0,
) -> Dict[str, Any]:
    task = _task_row(conn, task_id)
    if not task:
        raise LookupError("task not found")
    epoch = int(task["memory_epoch"] or 0)
    body = {
        "task_id": task_id,
        "kind": kind,
        "summary": summary,
        "details": details or {},
        "files": files or [],
        "tests": tests or [],
        "next_steps": next_steps or [],
        "memory_epoch": epoch,
    }
    row = conn.execute(
        sa.text("""
            INSERT INTO harness_task_memory
                (task_id, plan_id, project_id, session_id, kind, summary, details,
                 files, tests, next_steps, token_estimate, memory_epoch, content_hash, created_by)
            VALUES
                (CAST(:task_id AS UUID), CAST(:plan_id AS UUID), CAST(:project_id AS UUID),
                 CAST(:session_id AS UUID), :kind, :summary, CAST(:details AS JSONB),
                 CAST(:files AS JSONB), CAST(:tests AS JSONB), CAST(:next_steps AS JSONB),
                 :token_estimate, :memory_epoch, :content_hash, :created_by)
            RETURNING id, task_id::text, kind, summary, details, files, tests, next_steps,
                      token_estimate, memory_epoch, content_hash, created_by, created_at
        """),
        {
            "task_id": task_id,
            "plan_id": task["plan_id"],
            "project_id": task["project_id"],
            "session_id": session_id,
            "kind": kind,
            "summary": summary,
            "details": _json(details or {}),
            "files": _json(files or []),
            "tests": _json(tests or []),
            "next_steps": _json(next_steps or []),
            "token_estimate": int(token_estimate or 0),
            "memory_epoch": epoch,
            "content_hash": _hash(body),
            "created_by": created_by,
        },
    ).mappings().first()
    return dict(row) if row else {}


def list_memory(conn, task_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = conn.execute(
        sa.text("""
            SELECT id, task_id::text, kind, summary, details, files, tests, next_steps,
                   token_estimate, memory_epoch, content_hash, created_by, created_at
            FROM harness_task_memory
            WHERE task_id = CAST(:tid AS UUID)
            ORDER BY id DESC
            LIMIT :limit
        """),
        {"tid": task_id, "limit": max(1, min(int(limit or 50), 200))},
    ).mappings().all()
    return [dict(row) for row in rows]


def latest_handoff(conn, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        sa.text("""
            SELECT id, task_id::text, kind, summary, details, files, tests, next_steps,
                   token_estimate, memory_epoch, content_hash, created_by, created_at
            FROM harness_task_memory
            WHERE task_id = CAST(:tid AS UUID)
              AND kind IN ('HANDOFF', 'CHECKPOINT', 'SUCCESS')
            ORDER BY id DESC
            LIMIT 1
        """),
        {"tid": task_id},
    ).mappings().first()
    return dict(row) if row else None


def task_contract(conn, task_id: str, include_memory: bool = True) -> Dict[str, Any]:
    task = _task_row(conn, task_id)
    if not task:
        raise LookupError("task not found")
    row = conn.execute(
        sa.text("""
            SELECT id::text, plan_id::text, title, required_skills, git_branch,
                   allowed_files, allowed_symbols, allow_dependency_changes, description,
                   task_context, acceptance_criteria, test_commands, depends_on,
                   estimated_input_tokens, max_input_tokens, handoff_summary,
                   context_manifest, memory_epoch, task_kind, budget_status, status,
                   failed_attempts_memory, created_at, updated_at
            FROM harness_tasks
            WHERE id = CAST(:tid AS UUID)
        """),
        {"tid": task_id},
    ).mappings().first()
    contract = dict(row) if row else {}
    dependencies = []
    for dep_id in (contract.get("depends_on") or []):
        dep = conn.execute(
            sa.text("""
                SELECT id::text, title, status, handoff_summary
                FROM harness_tasks WHERE id = CAST(:tid AS UUID)
            """),
            {"tid": dep_id},
        ).mappings().first()
        if dep:
            dependencies.append(dict(dep))
    memory = list_memory(conn, task_id, limit=20) if include_memory else []
    handoff = latest_handoff(conn, task_id) if include_memory else None
    return {
        "task": contract,
        "plan": {
            "plan_id": task["plan_id"],
            "project_id": task["project_id"],
            "repo_name": task["repo_name"],
            "requirement_prompt": task["requirement_prompt"],
        },
        "dependencies": dependencies,
        "handoff": handoff,
        "memory": memory,
    }


def checkpoint_task(
    engine: sa.Engine,
    task_id: str,
    session_id: Optional[str],
    summary: str,
    decisions: Optional[List[str]] = None,
    files: Optional[List[str]] = None,
    tests: Optional[List[str]] = None,
    next_steps: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
    created_by: Optional[str] = None,
    reset_context: bool = True,
    token_estimate: int = 0,
) -> Dict[str, Any]:
    with engine.begin() as conn:
        task = _task_row(conn, task_id)
        if not task:
            raise LookupError("task not found")
        if session_id and task["assigned_session_id"] and task["assigned_session_id"] != session_id:
            raise PermissionError("task assigned to another session")
        memory = record_memory(
            conn,
            task_id,
            "CHECKPOINT",
            summary,
            details={**(details or {}), "decisions": decisions or []},
            files=files,
            tests=tests,
            next_steps=next_steps,
            session_id=session_id,
            created_by=created_by,
            token_estimate=token_estimate,
        )
        epoch = conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET handoff_summary = :summary,
                    memory_epoch = memory_epoch + 1,
                    updated_at = NOW()
                WHERE id = CAST(:tid AS UUID)
                RETURNING memory_epoch
            """),
            {"summary": summary, "tid": task_id},
        ).scalar_one()
        if reset_context and session_id:
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET current_context_tokens = 0,
                        context_compactions = context_compactions + 1,
                        last_compacted_at = NOW(),
                        last_heartbeat = NOW()
                    WHERE id = CAST(:sid AS UUID)
                """),
                {"sid": session_id},
            )
    return {"task_id": task_id, "memory_epoch": int(epoch), "memory": memory}


def resume_task(engine: sa.Engine, task_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    with engine.connect() as conn:
        contract = task_contract(conn, task_id, include_memory=True)
    if session_id:
        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET current_context_tokens = 0,
                        last_heartbeat = NOW()
                    WHERE id = CAST(:sid AS UUID)
                """),
                {"sid": session_id},
            )
            try:
                record_memory(
                    conn, task_id, "RESUMED",
                    "Tarea reanudada desde handoff persistente",
                    details={"handoff_id": (contract.get("handoff") or {}).get("id")},
                    session_id=session_id,
                    created_by="resume",
                )
            except Exception:
                pass
    contract["resume"] = {
        "strategy": "HANDOFF_FIRST",
        "handoff": contract.get("handoff"),
        "memory_entries": len(contract.get("memory") or []),
        "context_manifest": (contract.get("task") or {}).get("context_manifest") or {},
    }
    return contract


def persist_success_memory(
    engine: sa.Engine,
    task_id: str,
    session_id: Optional[str],
    summary: str,
    files: Optional[List[str]] = None,
    tests: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    with engine.begin() as conn:
        memory = record_memory(
            conn, task_id, "HANDOFF", summary,
            details=details, files=files, tests=tests,
            session_id=session_id, created_by=created_by,
        )
        conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET handoff_summary = :summary, updated_at = NOW()
                WHERE id = CAST(:tid AS UUID)
            """),
            {"summary": summary, "tid": task_id},
        )
    return memory


def persist_failure_memory(
    engine: sa.Engine,
    task_id: str,
    session_id: Optional[str],
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    files: Optional[List[str]] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    with engine.begin() as conn:
        return record_memory(
            conn, task_id, "FAILURE", summary,
            details=details, files=files,
            session_id=session_id, created_by=created_by,
        )


def record_plan_memory(
    conn,
    plan_id: str,
    kind: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    row = conn.execute(
        sa.text("""
            INSERT INTO harness_plan_memory (plan_id, kind, summary, details, created_by)
            VALUES (CAST(:pid AS UUID), :kind, :summary, CAST(:details AS JSONB), :created_by)
            RETURNING id, plan_id::text, kind, summary, details, created_by, created_at
        """),
        {
            "pid": plan_id,
            "kind": kind,
            "summary": summary,
            "details": _json(details or {}),
            "created_by": created_by,
        },
    ).mappings().first()
    return dict(row) if row else {}


def list_plan_memory(conn, plan_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = conn.execute(
        sa.text("""
            SELECT id, plan_id::text, kind, summary, details, created_by, created_at
            FROM harness_plan_memory
            WHERE plan_id = CAST(:pid AS UUID)
            ORDER BY id DESC
            LIMIT :limit
        """),
        {"pid": plan_id, "limit": max(1, min(int(limit or 50), 200))},
    ).mappings().all()
    return [dict(row) for row in rows]
