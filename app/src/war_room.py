import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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


def get_topology(engine: sa.Engine) -> Dict[str, Any]:
    with engine.connect() as conn:
        nodes = conn.execute(sa.text("""
            SELECT s.id::text AS session_id, s.engineer_username, s.agent_tool, s.llm_provider,
                   s.skills, s.status, s.current_context_tokens, s.max_context_tokens,
                   s.system_contract_rules, s.last_heartbeat, s.connected_at,
                   s.project_id::text AS project_id, p.project_slug,
                   s.is_headless_worker, s.session_kind, s.worker_role
            FROM agent_sessions s
            LEFT JOIN harness_projects p ON p.id = s.project_id
            ORDER BY s.connected_at DESC
        """)).mappings().all()

        links = conn.execute(sa.text("""
            SELECT id::text, source_session_id::text, target_session_id::text,
                   link_type, trigger_event, active, created_at
            FROM agent_links
            WHERE active = true
            ORDER BY created_at ASC
        """)).mappings().all()

        tasks = conn.execute(sa.text("""
            SELECT t.id::text, t.plan_id::text, t.title, t.status, t.assigned_session_id::text,
                   t.git_branch, t.allowed_files, t.required_skills, t.created_at,
                   p.project_id::text AS project_id, pr.project_slug
            FROM harness_tasks t
            LEFT JOIN feature_consensus_plans p ON p.id = t.plan_id
            LEFT JOIN harness_projects pr ON pr.id = p.project_id
            WHERE t.status IN ('PENDING', 'IN_PROGRESS', 'VALIDATING', 'CONFLICT')
            ORDER BY t.created_at ASC
        """)).mappings().all()

        locks = conn.execute(sa.text("""
            SELECT repo_name, file_path, locked_by_task_id::text
            FROM repo_file_map
            WHERE locked_by_task_id IS NOT NULL
            ORDER BY repo_name, file_path
        """)).mappings().all()

        events = conn.execute(sa.text("""
            SELECT id, event_type, source_session_id::text, target_session_id::text,
                   payload, created_at
            FROM war_room_events
            ORDER BY id DESC
            LIMIT 30
        """)).mappings().all()

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
