"""Licenciamiento por planes y enforcement atomico de cuotas.

La fuente de verdad es la fila singleton `system_license_policy`. Las funciones
de cuota deben llamarse dentro de una transaccion que haya bloqueado esa fila
con `SELECT ... FOR UPDATE` para evitar carreras entre sesiones concurrentes.
"""
from typing import Any, Dict, List, Optional

import sqlalchemy as sa

DEFAULT_PLAN = "ENTERPRISE_10"

LICENSE_TIERS: Dict[str, Dict[str, int]] = {
    "PERSONAL_1": {
        "max_active_projects": 1,
        "max_hermes_sessions": 1,
        "max_engineers_per_project": 1,
        "max_total_engineer_sessions": 1,
        "max_registered_users": 1,
        "max_parallel_rag_jobs": 1,
    },
    "TEAM_3": {
        "max_active_projects": 3,
        "max_hermes_sessions": 3,
        "max_engineers_per_project": 3,
        "max_total_engineer_sessions": 6,
        "max_registered_users": 5,
        "max_parallel_rag_jobs": 2,
    },
    "BUSINESS_5": {
        "max_active_projects": 5,
        "max_hermes_sessions": 5,
        "max_engineers_per_project": 5,
        "max_total_engineer_sessions": 15,
        "max_registered_users": 15,
        "max_parallel_rag_jobs": 3,
    },
    "ENTERPRISE_10": {
        "max_active_projects": 10,
        "max_hermes_sessions": 10,
        "max_engineers_per_project": 10,
        "max_total_engineer_sessions": 30,
        "max_registered_users": 30,
        "max_parallel_rag_jobs": 3,
    },
    "MAX_20": {
        "max_active_projects": 10,
        "max_hermes_sessions": 10,
        "max_engineers_per_project": 20,
        "max_total_engineer_sessions": 60,
        "max_registered_users": 100,
        "max_parallel_rag_jobs": 4,
    },
}

QUOTA_FIELDS = (
    "max_active_projects",
    "max_hermes_sessions",
    "max_engineers_per_project",
    "max_total_engineer_sessions",
    "max_registered_users",
    "max_parallel_rag_jobs",
)


class LicenseError(Exception):
    def __init__(self, message: str, status_code: int = 403, code: str = "LICENSE_QUOTA_REACHED"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def plan_limits(plan_code: str) -> Dict[str, int]:
    code = str(plan_code or "").strip().upper()
    if code not in LICENSE_TIERS:
        raise LicenseError(
            f"Unknown license plan: {plan_code}. Valid plans: {', '.join(LICENSE_TIERS)}",
            status_code=400,
            code="LICENSE_UNKNOWN_PLAN",
        )
    return dict(LICENSE_TIERS[code])


def get_policy(conn, for_update: bool = False) -> Dict[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        sa.text(f"""
            SELECT id, plan_code, max_active_projects, max_hermes_sessions,
                   max_engineers_per_project, max_total_engineer_sessions,
                   max_registered_users, max_parallel_rag_jobs, updated_at
            FROM system_license_policy
            WHERE id = 1{suffix}
        """)
    ).mappings().fetchone()
    if not row:
        raise LicenseError("system_license_policy is not seeded", status_code=500, code="LICENSE_POLICY_MISSING")
    return dict(row)


def update_policy(conn, plan_code: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    code = str(plan_code or "").strip().upper()
    limits = plan_limits(code)
    overrides = overrides or {}
    for field in QUOTA_FIELDS:
        if field in overrides and overrides[field] is not None:
            try:
                value = int(overrides[field])
            except (TypeError, ValueError):
                raise LicenseError(f"{field} must be an integer", status_code=400, code="LICENSE_INVALID_QUOTA")
            if value < 0:
                raise LicenseError(f"{field} cannot be negative", status_code=400, code="LICENSE_INVALID_QUOTA")
            limits[field] = value
    if limits["max_parallel_rag_jobs"] < 1:
        raise LicenseError("max_parallel_rag_jobs must be >= 1", status_code=400, code="LICENSE_INVALID_QUOTA")
    conn.execute(
        sa.text("""
            UPDATE system_license_policy
            SET plan_code = :plan_code,
                max_active_projects = :max_active_projects,
                max_hermes_sessions = :max_hermes_sessions,
                max_engineers_per_project = :max_engineers_per_project,
                max_total_engineer_sessions = :max_total_engineer_sessions,
                max_registered_users = :max_registered_users,
                max_parallel_rag_jobs = :max_parallel_rag_jobs,
                updated_at = NOW()
            WHERE id = 1
        """),
        {"plan_code": code, **limits},
    )
    return get_policy(conn)


def get_usage(conn) -> Dict[str, Any]:
    active_projects = conn.execute(
        sa.text("SELECT count(*) FROM harness_projects WHERE is_active_slot = true")
    ).scalar_one()
    hermes_workers = conn.execute(
        sa.text("""
            SELECT count(*) FROM agent_sessions
            WHERE session_kind = 'SYSTEM_WORKER'
              AND agent_tool = 'hermes'
              AND status IN ('ONLINE', 'BUSY')
        """)
    ).scalar_one()
    engineers_by_project = conn.execute(
        sa.text("""
            SELECT project_id::text AS project_id,
                   count(DISTINCT user_id) AS engineers
            FROM agent_sessions
            WHERE session_kind = 'INTERACTIVE'
              AND status IN ('ONLINE', 'BUSY')
              AND user_id IS NOT NULL
            GROUP BY project_id
            ORDER BY project_id
        """)
    ).mappings().all()
    totals = conn.execute(
        sa.text("""
            SELECT count(*) AS sessions,
                   count(DISTINCT user_id) AS users
            FROM agent_sessions
            WHERE session_kind = 'INTERACTIVE'
              AND status IN ('ONLINE', 'BUSY')
              AND user_id IS NOT NULL
        """)
    ).mappings().one()
    registered = conn.execute(
        sa.text("""
            SELECT count(*) FROM harness_users
            WHERE role = 'ENGINEER'
              AND status IN ('ACTIVE', 'INVITED', 'PENDING')
        """)
    ).scalar_one()
    jobs = conn.execute(
        sa.text("""
            SELECT count(*) FILTER (WHERE status = 'QUEUED') AS queued,
                   count(*) FILTER (WHERE status = 'RUNNING') AS running,
                   count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed
            FROM hermes_worker_jobs
        """)
    ).mappings().one()
    return {
        "active_projects_used": int(active_projects),
        "hermes_workers_online": int(hermes_workers),
        "engineers_online_by_project": [
            {"project_id": row["project_id"], "engineers": int(row["engineers"])}
            for row in engineers_by_project
        ],
        "total_engineers_online": int(totals["users"] or 0),
        "total_engineer_sessions_online": int(totals["sessions"] or 0),
        "registered_engineers": int(registered),
        "hermes_jobs": {
            "queued": int(jobs["queued"] or 0),
            "running": int(jobs["running"] or 0),
            "completed": int(jobs["completed"] or 0),
            "failed": int(jobs["failed"] or 0),
        },
    }


def license_response(conn, policy: Dict[str, Any]) -> Dict[str, Any]:
    usage = get_usage(conn)
    over_limit = (
        usage["active_projects_used"] > policy["max_active_projects"]
        or usage["hermes_workers_online"] > policy["max_hermes_sessions"]
        or usage["total_engineer_sessions_online"] > policy["max_total_engineer_sessions"]
        or usage["registered_engineers"] > policy["max_registered_users"]
    )
    return {
        "plan_code": policy["plan_code"],
        "limits": {field: policy[field] for field in QUOTA_FIELDS},
        "usage": usage,
        "over_limit": over_limit,
        "updated_at": policy.get("updated_at"),
    }


def assert_project_slot_available(conn, policy: Dict[str, Any]) -> None:
    used = conn.execute(
        sa.text("SELECT count(*) FROM harness_projects WHERE is_active_slot = true")
    ).scalar_one()
    limit = int(policy["max_active_projects"])
    if used >= limit:
        raise LicenseError(
            f"License Quota Reached: active projects ({used}/{limit}) for plan {policy['plan_code']}"
        )


def assert_hermes_slot_available(conn, policy: Dict[str, Any], exclude_session_id: Optional[str] = None) -> None:
    sql = """
        SELECT count(*) FROM agent_sessions
        WHERE session_kind = 'SYSTEM_WORKER'
          AND agent_tool = 'hermes'
          AND status IN ('ONLINE', 'BUSY')
    """
    params = {}
    if exclude_session_id:
        sql += " AND id <> CAST(:exclude_sid AS UUID)"
        params["exclude_sid"] = exclude_session_id
    used = conn.execute(sa.text(sql), params).scalar_one()
    limit = int(policy["max_hermes_sessions"])
    if used >= limit:
        raise LicenseError(
            f"License Quota Reached: Hermes worker sessions ({used}/{limit}) for plan {policy['plan_code']}"
        )


def _exclude_clause(exclude_session_ids: List[str], params: Dict[str, Any]) -> str:
    if not exclude_session_ids:
        return ""
    placeholders = []
    for index, session_id in enumerate(exclude_session_ids):
        key = f"exclude_sid_{index}"
        placeholders.append(f"CAST(:{key} AS UUID)")
        params[key] = session_id
    return " AND id NOT IN (" + ", ".join(placeholders) + ")"


def assert_engineer_quota(
    conn,
    policy: Dict[str, Any],
    project_id: str,
    user_id: str,
    exclude_session_ids: Optional[List[str]] = None,
) -> None:
    params: Dict[str, Any] = {"pid": project_id}
    exclude = _exclude_clause(exclude_session_ids or [], params)
    project_users = conn.execute(
        sa.text(f"""
            SELECT DISTINCT user_id::text
            FROM agent_sessions
            WHERE project_id = CAST(:pid AS UUID)
              AND session_kind = 'INTERACTIVE'
              AND status IN ('ONLINE', 'BUSY')
              AND user_id IS NOT NULL
              {exclude}
        """),
        params,
    ).scalars().all()
    project_users = {row for row in project_users if row}
    if user_id not in project_users and len(project_users) >= int(policy["max_engineers_per_project"]):
        used = len(project_users)
        limit = int(policy["max_engineers_per_project"])
        raise LicenseError(
            f"License Quota Reached: engineers in project ({used}/{limit}) for plan {policy['plan_code']}"
        )

    global_params: Dict[str, Any] = {}
    global_exclude = _exclude_clause(exclude_session_ids or [], global_params)
    total_sessions = conn.execute(
        sa.text(f"""
            SELECT count(*) FROM agent_sessions
            WHERE session_kind = 'INTERACTIVE'
              AND status IN ('ONLINE', 'BUSY')
              AND user_id IS NOT NULL
              {global_exclude}
        """),
        global_params,
    ).scalar_one()
    total_limit = int(policy["max_total_engineer_sessions"])
    if total_sessions >= total_limit:
        raise LicenseError(
            f"License Quota Reached: total engineer sessions ({total_sessions}/{total_limit}) for plan {policy['plan_code']}"
        )


def assert_registered_user_quota(conn, policy: Dict[str, Any]) -> None:
    used = conn.execute(
        sa.text("""
            SELECT count(*) FROM harness_users
            WHERE role = 'ENGINEER'
              AND status IN ('ACTIVE', 'INVITED', 'PENDING')
        """)
    ).scalar_one()
    limit = int(policy["max_registered_users"])
    if used >= limit:
        raise LicenseError(
            f"License Quota Reached: registered users ({used}/{limit}) for plan {policy['plan_code']}"
        )


def assert_downgrade_allowed(conn, new_limits: Dict[str, int]) -> None:
    """Bloquea un downgrade si proyectos o workers Hermes ya exceden el nuevo plan.

    Los usuarios existentes se mantienen (grandfathering): solo se bloquean
    nuevas invitaciones/altas cuando el plan baja.
    """
    active_projects = conn.execute(
        sa.text("SELECT count(*) FROM harness_projects WHERE is_active_slot = true")
    ).scalar_one()
    if active_projects > int(new_limits["max_active_projects"]):
        raise LicenseError(
            f"License Downgrade Blocked: {active_projects} active projects exceed "
            f"max_active_projects={new_limits['max_active_projects']}",
            status_code=409,
            code="LICENSE_DOWNGRADE_BLOCKED",
        )
    hermes = conn.execute(
        sa.text("""
            SELECT count(*) FROM agent_sessions
            WHERE session_kind = 'SYSTEM_WORKER'
              AND agent_tool = 'hermes'
              AND status IN ('ONLINE', 'BUSY')
        """)
    ).scalar_one()
    if hermes > int(new_limits["max_hermes_sessions"]):
        raise LicenseError(
            f"License Downgrade Blocked: {hermes} Hermes worker sessions exceed "
            f"max_hermes_sessions={new_limits['max_hermes_sessions']}",
            status_code=409,
            code="LICENSE_DOWNGRADE_BLOCKED",
        )
