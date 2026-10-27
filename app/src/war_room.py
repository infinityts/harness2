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
            SELECT id::text AS session_id, engineer_username, agent_tool, llm_provider,
                   skills, status, current_context_tokens, max_context_tokens,
                   last_heartbeat, connected_at
            FROM agent_sessions
            ORDER BY connected_at DESC
        """)).mappings().all()

        links = conn.execute(sa.text("""
            SELECT id::text, source_session_id::text, target_session_id::text,
                   link_type, trigger_event, active, created_at
            FROM agent_links
            WHERE active = true
            ORDER BY created_at ASC
        """)).mappings().all()

        tasks = conn.execute(sa.text("""
            SELECT id::text, plan_id::text, title, status, assigned_session_id::text,
                   git_branch, allowed_files, required_skills, created_at
            FROM harness_tasks
            WHERE status IN ('PENDING', 'IN_PROGRESS', 'VALIDATING', 'CONFLICT')
            ORDER BY created_at ASC
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
