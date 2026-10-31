import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import sqlalchemy as sa


def record_event(
    engine: sa.Engine,
    event_type: str,
    payload: Dict[str, Any],
    source_session_id: Optional[str] = None,
    target_session_id: Optional[str] = None,
) -> None:
    """Persiste un evento de auditoria. No rompe la operacion principal si falla."""
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                    INSERT INTO war_room_events
                        (event_type, source_session_id, target_session_id, payload)
                    VALUES
                        (:etype, CAST(:src AS UUID), CAST(:tgt AS UUID), CAST(:payload AS JSONB))
                """),
                {
                    "etype": event_type,
                    "src": source_session_id,
                    "tgt": target_session_id,
                    "payload": json.dumps(payload, default=str),
                }
            )
    except Exception as exc:
        print(f"[war-room] no se pudo registrar {event_type}: {exc}")


def get_topology(engine: sa.Engine, project_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    scoped = project_ids is not None
    params: Dict[str, Any] = {}
    node_filter = ""
    task_filter = ""
    lock_filter = ""
    event_filter = ""
    link_filter = "WHERE active = true"
    if scoped:
        params["pids"] = project_ids
        node_filter = "WHERE s.project_id = ANY(CAST(:pids AS UUID[]))"
        task_filter = "WHERE p.project_id = ANY(CAST(:pids AS UUID[]))"
        lock_filter = "WHERE pr.id = ANY(CAST(:pids AS UUID[]))"
        link_filter = """
            WHERE active = true
              AND (
                source_session_id IN (
                    SELECT id FROM agent_sessions WHERE project_id = ANY(CAST(:pids AS UUID[]))
                )
                OR target_session_id IN (
                    SELECT id FROM agent_sessions WHERE project_id = ANY(CAST(:pids AS UUID[]))
                )
              )
        """
        event_filter = """
            WHERE source_session_id IN (
                SELECT id FROM agent_sessions WHERE project_id = ANY(CAST(:pids AS UUID[]))
            )
            OR target_session_id IN (
                SELECT id FROM agent_sessions WHERE project_id = ANY(CAST(:pids AS UUID[]))
            )
        """
    with engine.connect() as conn:
        nodes = conn.execute(sa.text(f"""
            SELECT s.id::text AS session_id, s.engineer_username, s.agent_tool, s.llm_provider,
                   s.skills, s.status, s.current_context_tokens, s.max_context_tokens,
                   s.system_contract_rules, s.last_heartbeat, s.connected_at,
                   s.project_id::text AS project_id, p.project_slug,
                   s.is_headless_worker, s.session_kind, s.worker_role, s.model_profile,
                   s.context_compactions, s.last_compacted_at, mp.tokenizer_status
            FROM agent_sessions s
            LEFT JOIN harness_projects p ON p.id = s.project_id
            LEFT JOIN harness_model_profiles mp ON mp.name = s.model_profile
            {node_filter}
            ORDER BY s.connected_at DESC
        """), params).mappings().all()

        links = conn.execute(sa.text(f"""
            SELECT id::text, source_session_id::text, target_session_id::text,
                   link_type, trigger_event, active, created_at
            FROM agent_links
            {link_filter}
            ORDER BY created_at ASC
        """), params).mappings().all()

        tasks = conn.execute(sa.text(f"""
            SELECT t.id::text, t.plan_id::text, t.title, t.status, t.assigned_session_id::text,
                   t.git_branch, t.allowed_files, t.allowed_symbols, t.required_skills,
                   t.description, t.task_context, t.acceptance_criteria, t.test_commands,
                   t.depends_on, t.estimated_input_tokens, t.max_input_tokens,
                   t.handoff_summary, t.memory_epoch, t.task_kind, t.budget_status,
                   t.parent_task_id::text, t.split_index, t.split_count,
                   t.split_reason, t.split_status,
                   t.created_at,
                   p.project_id::text AS project_id, pr.project_slug
            FROM harness_tasks t
            LEFT JOIN feature_consensus_plans p ON p.id = t.plan_id
            LEFT JOIN harness_projects pr ON pr.id = p.project_id
            {task_filter}
            {('AND' if scoped else 'WHERE')} t.status IN ('PENDING', 'IN_PROGRESS', 'VALIDATING', 'CONFLICT')
            ORDER BY t.created_at ASC
        """), params).mappings().all()

        locks = conn.execute(sa.text(f"""
            SELECT m.repo_name, m.file_path, m.locked_by_task_id::text
            FROM repo_file_map m
            JOIN harness_projects pr ON pr.project_slug = m.repo_name
            {lock_filter}
            {('AND' if scoped else 'WHERE')} m.locked_by_task_id IS NOT NULL
            ORDER BY m.repo_name, m.file_path
        """), params).mappings().all()

        events = conn.execute(sa.text(f"""
            SELECT id, event_type, source_session_id::text, target_session_id::text,
                   payload, created_at
            FROM war_room_events
            {event_filter}
            ORDER BY id DESC
            LIMIT 30
        """), params).mappings().all()

    node_list = []
    for node in nodes:
        item = dict(node)
        max_tokens = item.get("max_context_tokens") or 0
        current = item.get("current_context_tokens") or 0
        item["context_pct"] = round((current / max_tokens) * 100, 1) if max_tokens else 0.0
        node_list.append(item)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": node_list,
        "links": [dict(x) for x in links],
        "tasks": [dict(x) for x in tasks],
        "locks": [dict(x) for x in locks],
        "events": [dict(x) for x in events],
    }
