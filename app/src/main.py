import os
import json
import re
import secrets
import shutil
import string
import uuid
import hashlib
import hmac
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, List, Dict, Optional

import jwt
import sqlalchemy as sa
from fastapi import BackgroundTasks, Depends, FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from pathlib import Path

from src.indexer import (
    REPOS_BASE_DIR,
    compute_embedding_768,
    ensure_project_repo,
    index_repository,
    validate_repo_name,
    validate_repo_url,
)
from src.security import hash_password, open_secret, seal_secret, verify_password
from src.mailer import (
    render_admin_password_reset,
    render_password_changed,
    render_project_invite,
    render_user_welcome,
    send_transactional_email,
)
from src.skill_compiler import (
    SKILL_CATEGORIES,
    compile_standard_skill_md,
    draft_skill,
    estimate_tokens,
    slugify,
    validate_skill_fields,
)
from src.doc_rag import (
    bootstrap_project_docs,
    ingest_document,
    list_documents,
    search_documents,
)
from src.doc_worker import ensure_headless_worker_session
from src.licensing import (
    LICENSE_TIERS,
    QUOTA_FIELDS,
    LicenseError,
    assert_downgrade_allowed,
    assert_engineer_quota,
    assert_hermes_slot_available,
    assert_project_slot_available,
    assert_registered_user_quota,
    get_policy,
    license_response,
    update_policy,
)
from src.signed_license import LicenseManager
from src.license_activation import ActivationManager
from src import totp
from src.token_budget import context_budget, estimate_tokens as estimate_token_budget, fit_text, get_profile, resolve_profile
from src import task_memory
from src.hermes_pool import (
    claim_job,
    complete_job,
    deactivate_hermes_session,
    enqueue_job,
    ensure_hermes_worker_session,
    fail_job,
    heartbeat_job,
    sync_pool,
)
from src.git_guardian import (
    GitBarrierError,
    GitTaskPermissionError,
    cleanup_task_worktree,
    prepare_task_worktree,
    prepare_worktree_for_task,
    submit_task_changes,
)
from src.orchestrator import (
    TaskPermissionError,
    dispatch_pending_tasks,
    fail_task_checkpoint,
    generate_dual_plans_and_synthesize,
    release_task_assignment,
)
from src.war_room import get_topology, record_event
from src.war_room_ui import WAR_ROOM_HTML

ADMIN_API_KEY = os.getenv("HARNESS_ADMIN_API_KEY", "")
WORKER_API_KEY = os.getenv("HARNESS_WORKER_KEY", "")
war_room_clients = set()


async def broadcast_war(event: Dict[str, Any]) -> None:
    dead = []
    for ws in list(war_room_clients):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        war_room_clients.discard(ws)


async def emit_war(
    event_type: str,
    payload: Dict[str, Any],
    source_session_id: Optional[str] = None,
    target_session_id: Optional[str] = None,
) -> None:
    safe_payload = json.loads(json.dumps(payload, default=str))
    record_event(engine, event_type, safe_payload, source_session_id, target_session_id)
    await broadcast_war({
        "event": event_type,
        "data": safe_payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("HARNESS_JWT_SECRET", "change-me-secret")
HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "10"))
MAX_CONTEXT_RATIO = float(os.getenv("MAX_CONTEXT_RATIO", "0.65"))
SESSION_JWT_TTL_HOURS = int(os.getenv("SESSION_JWT_TTL_HOURS", "12"))
PORTAL_JWT_TTL_HOURS = int(os.getenv("PORTAL_JWT_TTL_HOURS", "12"))
HARNESS_PUBLIC_URL = os.getenv("HARNESS_PUBLIC_URL", "http://11.0.0.3:8444").rstrip("/")

engine = sa.create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
LICENSE_MANAGER = LicenseManager(engine)
ACTIVATION_MANAGER = ActivationManager(engine, LICENSE_MANAGER)


def assert_license_operational() -> None:
    LICENSE_MANAGER.assert_operational()
    ACTIVATION_MANAGER.assert_operational()
    ACTIVATION_MANAGER.assert_operational()

# Registro en memoria de conexiones WebSocket activas en este nodo
active_connections: Dict[str, WebSocket] = {}


class SessionRegisterRequest(BaseModel):
    engineer_username: str = Field(..., min_length=2, max_length=100)
    agent_tool: str = Field(..., pattern="^(opencode|claudecode|cursor|hermes|pi|custom)$")
    llm_provider: str = Field(..., min_length=2, max_length=100)
    skills: List[str] = Field(default_factory=list)
    max_context_tokens: int = Field(default=32000, gt=1024)
    model_profile: Optional[str] = Field(default=None, max_length=80)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_auth(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Admin por X-Harness-Admin-Key o agente por Authorization: Bearer <session_key>."""
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "admin", "session_id": None, "user_id": None, "project_id": None, "project_slug": None}

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            decoded = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=["HS256"],
                options={"require": ["exp", "jti", "sub"]},
            )
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        if decoded.get("typ") == "portal":
            uid = decoded.get("sub")
            with engine.connect() as conn:
                portal_user = conn.execute(
                    sa.text("""
                        SELECT id::text, email, full_name, role, status, default_skills
                        FROM harness_users
                        WHERE id = CAST(:uid AS UUID)
                    """),
                    {"uid": uid}
                ).mappings().fetchone()
            if not portal_user or portal_user["status"] != "ACTIVE" or portal_user["role"] != "ADMIN":
                raise HTTPException(status_code=403, detail="Admin role required")
            return {
                "role": "admin",
                "session_id": None,
                "user_id": portal_user["id"],
                "email": portal_user["email"],
                "project_id": None,
                "project_slug": None,
                "user": dict(portal_user),
            }

        session_id = decoded.get("sub")
        if not session_id:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("""
                    SELECT s.id::text, s.status, s.user_id::text, s.project_id::text, p.project_slug
                    FROM agent_sessions s
                    LEFT JOIN harness_projects p ON p.id = s.project_id
                    WHERE s.id = CAST(:sid AS UUID)
                      AND s.session_key_hash = :khash
                      AND s.status IN ('ONLINE', 'BUSY')
                """),
                {"sid": session_id, "khash": hash_token(token)}
            ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Session not active")
        return {
            "role": "agent",
            "session_id": row[0],
            "status": row[1],
            "user_id": row[2],
            "project_id": row[3],
            "project_slug": row[4],
        }

    raise HTTPException(
        status_code=401,
        detail="Missing credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )


def require_admin(auth: Dict[str, Any] = Depends(verify_auth)) -> Dict[str, Any]:
    if auth.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin key required")
    return auth


def require_worker(
    x_harness_worker_key: Optional[str] = Header(default=None),
    x_harness_admin_key: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Autentica al supervisor del pool Hermes.

    Acepta una clave de servicio dedicada y, como fallback operativo, la Admin
    API key. No acepta tokens de portal ni session keys de agentes.
    """
    if WORKER_API_KEY and x_harness_worker_key and hmac.compare_digest(x_harness_worker_key, WORKER_API_KEY):
        return {"role": "worker", "role_name": "WORKER"}
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "worker", "role_name": "WORKER", "via": "admin_key"}
    raise HTTPException(status_code=403, detail="Worker service key required")


def _portal_auth_from_header(authorization: Optional[str]) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Portal bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        decoded = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "jti", "sub"]},
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid portal token")
    if decoded.get("typ") != "portal":
        raise HTTPException(status_code=401, detail="Invalid portal token")
    with engine.connect() as conn:
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, full_name, role, status, default_skills,
                       password_change_allowed, mfa_enabled
                FROM harness_users
                WHERE id = CAST(:uid AS UUID)
            """),
            {"uid": decoded.get("sub")}
        ).mappings().fetchone()
    if not user or user["status"] != "ACTIVE":
        raise HTTPException(status_code=401, detail="User not active")
    role = "admin" if user["role"] == "ADMIN" else ("license_admin" if user["role"] == "LICENSE_ADMIN" else "engineer")
    return {
        "role": role,
        "role_name": user["role"],
        "user_id": user["id"],
        "email": user["email"],
        "user": dict(user),
    }


def verify_portal(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    return _portal_auth_from_header(authorization)


def verify_portal_or_admin(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "admin", "role_name": "ADMIN", "user_id": None, "email": None, "user": None}
    return _portal_auth_from_header(authorization)


def verify_portal_admin(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "admin", "role_name": "ADMIN", "user_id": None, "email": None, "user": None}
    auth = _portal_auth_from_header(authorization)
    if auth["role_name"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin role required")
    return auth


def verify_portal_admin_or_license(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Lectura de licencia: ADMIN normal o LICENSE_ADMIN.

    El ADMIN conserva visibilidad y auditoria, pero no puede mutar la licencia.
    """
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "admin", "role_name": "ADMIN", "user_id": None, "email": None, "user": None}
    auth = _portal_auth_from_header(authorization)
    if auth["role_name"] not in ("ADMIN", "LICENSE_ADMIN"):
        raise HTTPException(status_code=403, detail="Admin or license operator role required")
    return auth


def verify_license_admin(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Mutacion de licencia: solo LICENSE_ADMIN con token de portal y MFA.

    La Admin API Key no sirve aqui a proposito: es una credencial de
    automatizacion y no debe saltarse la separacion de funciones.
    """
    if not authorization and x_harness_admin_key:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LICENSE_ADMIN_REQUIRED",
                "message": "The Admin API Key cannot change the license; use LICENSE_ADMIN with MFA",
            },
        )
    auth = _portal_auth_from_header(authorization)
    if auth["role_name"] != "LICENSE_ADMIN":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LICENSE_ADMIN_REQUIRED",
                "message": "Only LICENSE_ADMIN can change the license or activation",
            },
        )
    return auth


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None
    return request.client.host


def assert_repo_access(auth: Dict[str, Any], repo_name: str) -> None:
    if auth.get("role") != "agent":
        return
    project_slug = auth.get("project_slug")
    if project_slug and project_slug != repo_name:
        raise HTTPException(status_code=403, detail="Cross-Project Isolation Violation")


def assert_task_project_access(auth: Dict[str, Any], task_id: str) -> None:
    if auth.get("role") != "agent":
        return
    project_id = auth.get("project_id")
    if not project_id:
        return
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("""
                SELECT p.project_id::text
                FROM harness_tasks t
                JOIN feature_consensus_plans p ON p.id = t.plan_id
                WHERE t.id = CAST(:tid AS UUID)
            """),
            {"tid": task_id}
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    task_project = row[0]
    if task_project and task_project != project_id:
        raise HTTPException(status_code=403, detail="Cross-Project Isolation Violation")


def assert_plan_project_access(auth: Dict[str, Any], plan_id: str) -> None:
    if auth.get("role") != "agent":
        return
    project_id = auth.get("project_id")
    if not project_id:
        return
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT project_id::text FROM feature_consensus_plans WHERE id = CAST(:pid AS UUID)"),
            {"pid": plan_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="plan not found")
    if row[0] and row[0] != project_id:
        raise HTTPException(status_code=403, detail="Cross-Project Isolation Violation")


def cleanup_disconnected_sessions(conn, session_ids: List[str], target_status: str = "OFFLINE"):
    """Pasa sesiones a OFFLINE/REVOKED y libera tareas y bloqueos de archivos en una sola transaccion."""
    if not session_ids:
        return
    for sid in session_ids:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = :st
                WHERE id = CAST(:sid AS UUID) AND status != 'REVOKED'
            """),
            {"st": target_status, "sid": sid}
        )
        # Liberar bloqueos de archivos de tareas activas de esta sesion
        conn.execute(
            sa.text("""
                UPDATE repo_file_map
                SET locked_by_task_id = NULL
                WHERE locked_by_task_id IN (
                    SELECT id FROM harness_tasks
                    WHERE assigned_session_id = CAST(:sid AS UUID)
                      AND status = 'IN_PROGRESS'
                )
            """),
            {"sid": sid}
        )
        # Devolver las tareas en progreso a PENDING para reasignacion
        conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET status = 'PENDING',
                    assigned_session_id = NULL
                WHERE assigned_session_id = CAST(:sid AS UUID)
                  AND status = 'IN_PROGRESS'
            """),
            {"sid": sid}
        )


async def session_watchdog_loop():
    """Watchdog que corre cada 3s y desconecta sesiones que superen HEARTBEAT_TIMEOUT."""
    cycle = 0
    while True:
        cycle += 1
        expired_ids = []
        try:
            with engine.begin() as conn:
                rows = conn.execute(
                    sa.text("""
                        SELECT id::text FROM agent_sessions
                        WHERE status IN ('ONLINE', 'BUSY')
                          AND is_headless_worker = false
                          AND last_heartbeat < NOW() - make_interval(secs => :timeout)
                    """),
                    {"timeout": HEARTBEAT_TIMEOUT}
                ).fetchall()
                expired_ids = [r[0] for r in rows]
                if expired_ids:
                    cleanup_disconnected_sessions(conn, expired_ids, target_status="OFFLINE")
            for sid in expired_ids:
                await emit_war(
                    "SESSION_OFFLINE",
                    {"session_id": sid, "reason": "heartbeat_timeout"},
                    source_session_id=sid,
                )
            # Cerrar sockets colgados si existieran en memoria
            for sid in expired_ids:
                ws = active_connections.pop(sid, None)
                if ws:
                    try:
                        await ws.close(code=4008, reason="Heartbeat timeout")
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[Watchdog Error] {exc}")
        if cycle % 20 == 0:
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("""
                        DELETE FROM war_room_events
                        WHERE id < COALESCE(
                            (SELECT id FROM war_room_events ORDER BY id DESC OFFSET 1000 LIMIT 1),
                            0
                        )
                    """))
            except Exception as exc:
                print(f"[war-room] prune error: {exc}")
        await asyncio.sleep(3)


async def headless_heartbeat_loop():
    # Recupera workers que quedaron BUSY/OFFLINE por un reinicio durante un job.
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("""
                UPDATE agent_sessions
                SET status = 'ONLINE', last_heartbeat = NOW()
                WHERE is_headless_worker = true AND status IN ('BUSY', 'OFFLINE')
            """))
    except Exception as exc:
        print(f"[docs-worker startup] {exc}")
    while True:
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("""
                    UPDATE agent_sessions
                    SET last_heartbeat = NOW()
                    WHERE is_headless_worker = true AND status IN ('ONLINE', 'BUSY')
                """))
        except Exception as exc:
            print(f"[docs-worker heartbeat] {exc}")
        await asyncio.sleep(10)


async def license_reload_loop():
    seconds = int(os.getenv("HARNESS_LICENSE_RELOAD_SECONDS", "300"))
    while True:
        await asyncio.sleep(seconds)
        try:
            await asyncio.to_thread(LICENSE_MANAGER.reload, "periodic", False)
        except Exception as exc:
            print(f"[license] periodic reload failed: {exc}")


async def activation_loop():
    while True:
        try:
            await asyncio.to_thread(ACTIVATION_MANAGER.activate, "periodic", False)
        except Exception as exc:
            print(f"[license] activation failed: {exc}")
        delay = int(os.getenv("HARNESS_ACTIVATION_RENEW_SECONDS", "300"))
        if not ACTIVATION_MANAGER.is_operational():
            delay = min(delay, 30)
        await asyncio.sleep(max(15, delay))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await asyncio.to_thread(LICENSE_MANAGER.reload, "startup", True)
    except Exception as exc:
        print(f"[license] startup reload failed: {exc}")
    try:
        await asyncio.to_thread(ACTIVATION_MANAGER.activate, "startup", True)
    except Exception as exc:
        print(f"[license] startup activation failed: {exc}")
    watchdog_task = asyncio.create_task(session_watchdog_loop())
    headless_task = asyncio.create_task(headless_heartbeat_loop())
    license_task = asyncio.create_task(license_reload_loop())
    activation_task = asyncio.create_task(activation_loop())
    yield
    watchdog_task.cancel()
    headless_task.cancel()
    license_task.cancel()
    activation_task.cancel()


app = FastAPI(title="Harness Control Plane", version="0.2.0", lifespan=lifespan)


@app.exception_handler(LicenseError)
async def license_error_handler(request, exc: LicenseError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.get("/health")
def health_check():
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))
    return {
        "status": "ok",
        "service": "harness-control-plane",
        "heartbeat_timeout_seconds": HEARTBEAT_TIMEOUT,
        "max_context_ratio": MAX_CONTEXT_RATIO,
        "session_jwt_ttl_hours": SESSION_JWT_TTL_HOURS,
        "active_ws_connections": len(active_connections),
        "license_status": LICENSE_MANAGER.state.get("status"),
        "license_activation_status": ACTIVATION_MANAGER.state.get("status"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/api/v1/sessions/register", dependencies=[Depends(require_admin)])
async def register_agent_session(payload: SessionRegisterRequest):
    session_id = str(uuid.uuid4())
    now_ts = int(datetime.now(timezone.utc).timestamp())
    try:
        with engine.connect() as conn:
            profile = resolve_profile(conn, payload.model_profile, payload.max_context_tokens)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    max_tokens = int(profile["context_window_tokens"]) if payload.model_profile else int(payload.max_context_tokens)
    jwt_payload = {
        "sub": session_id,
        "user": payload.engineer_username,
        "tool": payload.agent_tool,
        "iat": now_ts,
        "jti": str(uuid.uuid4()),
        "exp": now_ts + SESSION_JWT_TTL_HOURS * 3600,
    }
    session_key = jwt.encode(jwt_payload, JWT_SECRET, algorithm="HS256")
    key_hash = hash_token(session_key)

    with engine.begin() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO agent_sessions (
                    id, engineer_username, agent_tool, llm_provider,
                    session_key_hash, skills, status, max_context_tokens, current_context_tokens,
                    model_profile
                ) VALUES (
                    CAST(:id AS UUID), :user, :tool, :provider,
                    :khash, CAST(:skills AS JSONB), 'ONLINE', :max_tok, 0, :model_profile
                )
            """),
            {
                "id": session_id,
                "user": payload.engineer_username,
                "tool": payload.agent_tool,
                "provider": payload.llm_provider,
                "khash": key_hash,
                "skills": json.dumps(payload.skills),
                "max_tok": max_tokens,
                "model_profile": profile["name"],
            }
        )

    await emit_war(
        "SESSION_REGISTERED",
        {
            "session_id": session_id,
            "engineer_username": payload.engineer_username,
            "agent_tool": payload.agent_tool,
            "llm_provider": payload.llm_provider,
            "skills": payload.skills,
        },
        source_session_id=session_id,
    )

    return {
        "session_id": session_id,
        "session_key": session_key,
        "status": "ONLINE",
        "max_context_tokens": max_tokens,
        "model_profile": profile["name"],
        "ws_url": f"/ws/agent/{session_id}"
    }


@app.get("/api/v1/sessions", dependencies=[Depends(verify_auth)])
def list_sessions():
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT s.id::text, s.engineer_username, s.agent_tool, s.llm_provider,
                       s.skills, s.status, s.current_context_tokens, s.max_context_tokens,
                       s.system_contract_rules, s.last_heartbeat, s.connected_at,
                       s.project_id::text AS project_id, p.project_slug, s.is_headless_worker,
                       s.session_kind, s.worker_role, s.model_profile,
                       s.context_compactions, s.last_compacted_at
                FROM agent_sessions s
                LEFT JOIN harness_projects p ON p.id = s.project_id
                ORDER BY s.connected_at DESC
            """)
        ).mappings().all()
    return {"sessions": [dict(r) for r in rows]}


@app.post("/api/v1/sessions/{session_id}/context-purged")
def confirm_context_purged(session_id: str, auth: Dict[str, Any] = Depends(verify_auth)):
    if auth["role"] == "agent" and auth.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="Cannot purge another session")
    with engine.begin() as conn:
        res = conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET current_context_tokens = 0,
                    context_compactions = context_compactions + 1,
                    last_compacted_at = NOW(),
                    last_heartbeat = NOW()
                WHERE id = CAST(:sid AS UUID) AND status IN ('ONLINE', 'BUSY')
            """),
            {"sid": session_id}
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Active session not found")
    return {"session_id": session_id, "current_context_tokens": 0, "status": "CONTEXT_RESET_OK"}


@app.post("/api/v1/sessions/{session_id}/revoke")
async def revoke_session(session_id: str, auth: Dict[str, Any] = Depends(verify_auth)):
    if auth["role"] == "agent" and auth.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="Cannot revoke another session")
    with engine.begin() as conn:
        cleanup_disconnected_sessions(conn, [session_id], target_status="REVOKED")
    ws = active_connections.pop(session_id, None)
    if ws:
        await ws.close(code=4003, reason="Session revoked by Control Plane")
    await emit_war(
        "SESSION_REVOKED",
        {"session_id": session_id, "status": "REVOKED"},
        source_session_id=session_id,
    )
    return {"session_id": session_id, "status": "REVOKED"}


@app.websocket("/ws/agent/{session_id}")
async def agent_heartbeat_ws(websocket: WebSocket, session_id: str):
    # 1. Solo header Authorization o primer mensaje AUTH. Sin ?token= (evita fugas en logs).
    pre_accepted = False
    auth_header = websocket.headers.get("authorization") or ""
    token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else None
    if not token:
        await websocket.accept()
        pre_accepted = True
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        except Exception:
            await websocket.close(code=4003, reason="Missing session_key")
            return
        if not isinstance(first, dict) or first.get("type") != "AUTH" or not first.get("token"):
            await websocket.close(code=4003, reason="AUTH message required")
            return
        token = str(first["token"])

    # 2. Validar firma JWT y hash en la base de datos
    try:
        decoded = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "jti", "sub"]},
        )
        if decoded.get("sub") != session_id:
            await websocket.close(code=4003, reason="Token subject mismatch")
            return
    except Exception:
        await websocket.close(code=4003, reason="Invalid session_key token")
        return

    token_hash = hash_token(token)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("""
                    SELECT status, max_context_tokens, skills, system_contract_rules,
                           user_id::text AS user_id, project_id::text AS project_id,
                           session_kind
                    FROM agent_sessions
                    WHERE id = CAST(:sid AS UUID) AND session_key_hash = :khash
                """),
                {"sid": session_id, "khash": token_hash}
            ).mappings().fetchone()

            if not row or row["status"] == "REVOKED":
                await websocket.close(code=4003, reason="Session unauthorized or revoked")
                return

            if row["session_kind"] == "INTERACTIVE" and row["project_id"] and row["user_id"]:
                policy = get_policy(conn, for_update=True)
                assert_engineer_quota(
                    conn, policy, row["project_id"], row["user_id"],
                    exclude_session_ids=[session_id],
                )

            # Al reconectar con key valida (y no revocada), asegurar estado ONLINE y heartbeat fresco
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET status = 'ONLINE', last_heartbeat = NOW()
                    WHERE id = CAST(:sid AS UUID)
                """),
                {"sid": session_id}
            )
    except LicenseError as exc:
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text("""
                        UPDATE agent_sessions
                        SET status = 'OFFLINE', last_heartbeat = NOW()
                        WHERE id = CAST(:sid AS UUID)
                    """),
                    {"sid": session_id}
                )
        except Exception:
            pass
        await websocket.close(code=4003, reason=exc.message[:120])
        return

    if not pre_accepted:
        await websocket.accept()
    active_connections[session_id] = websocket
    await websocket.send_json({
        "event": "SESSION_SYNC",
        "session_id": session_id,
        "skills": row["skills"] or [],
        "max_context_tokens": row["max_context_tokens"] or 32000,
        "system_contract_rules": row["system_contract_rules"] or [],
    })

    try:
        while True:
            data = await websocket.receive_json()
            current_tokens = int(data.get("current_context_tokens", 0))
            agent_state = data.get("status", "ONLINE")
            if agent_state not in ("ONLINE", "BUSY"):
                agent_state = "ONLINE"

            with engine.begin() as conn:
                check = conn.execute(
                    sa.text("""
                        SELECT status, max_context_tokens
                        FROM agent_sessions
                        WHERE id = CAST(:sid AS UUID)
                    """),
                    {"sid": session_id}
                ).fetchone()

                if not check or check[0] in ("OFFLINE", "REVOKED"):
                    await websocket.send_json({
                        "status": "ERROR",
                        "action": "TERMINATE_SESSION",
                        "reason": f"Session is {check[0] if check else 'MISSING'}."
                    })
                    await websocket.close(code=4003)
                    break

                max_tokens = int(data.get("max_context_tokens", check[1]))

                conn.execute(
                    sa.text("""
                        UPDATE agent_sessions
                        SET last_heartbeat = NOW(),
                            status = :st,
                            current_context_tokens = :curr,
                            max_context_tokens = :max_t
                        WHERE id = CAST(:sid AS UUID)
                    """),
                    {"st": agent_state, "curr": current_tokens, "max_t": max_tokens, "sid": session_id}
                )

            ratio = (current_tokens / max_tokens) if max_tokens > 0 else 0.0
            action = "KEEP_ALIVE"
            if ratio >= MAX_CONTEXT_RATIO:
                action = "PURGE_AND_CHECKPOINT_CONTEXT"

            await websocket.send_json({
                "status": "ACK",
                "action": action,
                "context_ratio": round(ratio, 3),
                "server_time": datetime.now(timezone.utc).isoformat()
            })

            await broadcast_war({
                "event": "CONTEXT_UPDATE",
                "data": {
                    "session_id": session_id,
                    "current_context_tokens": current_tokens,
                    "max_context_tokens": max_tokens,
                    "context_pct": round(ratio * 100, 1),
                    "status": agent_state,
                    "action": action,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            if action == "PURGE_AND_CHECKPOINT_CONTEXT":
                await emit_war(
                    "CONTEXT_PURGE",
                    {
                        "session_id": session_id,
                        "current_context_tokens": current_tokens,
                        "max_context_tokens": max_tokens,
                        "context_pct": round(ratio * 100, 1),
                    },
                    source_session_id=session_id,
                )
    except WebSocketDisconnect:
        pass
    finally:
        active_connections.pop(session_id, None)
        with engine.begin() as conn:
            cleanup_disconnected_sessions(conn, [session_id], target_status="OFFLINE")
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT status FROM agent_sessions WHERE id = CAST(:sid AS UUID)"),
                    {"sid": session_id}
                ).fetchone()
            if row and row[0] == "OFFLINE":
                await emit_war(
                    "SESSION_OFFLINE",
                    {"session_id": session_id, "reason": "websocket_closed"},
                    source_session_id=session_id,
                )
        except Exception:
            pass


class RepoIndexRequest(BaseModel):
    repo_name: str = Field(..., min_length=2, max_length=150, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    repo_url: Optional[str] = None
    branch: str = Field(default="main", max_length=150)


class ContextPackRequest(BaseModel):
    repo_name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    query: str = Field(..., min_length=1, max_length=4000)
    allowed_files: Optional[List[str]] = None
    token_budget: int = Field(default=2000, ge=300, le=64000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_docs: bool = True
    task_id: Optional[str] = None
    model_profile: Optional[str] = Field(default=None, max_length=80)
    include_handoff: bool = True


class SymbolValidationRequest(BaseModel):
    repo_name: str = Field(..., min_length=2, max_length=150)
    referenced_symbols: List[str]


@app.get("/api/v1/repos", dependencies=[Depends(verify_auth)])
def list_indexed_repos():
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT repo_name, count(*) AS files, max(commit_sha) AS commit_sha
                FROM repo_file_map
                GROUP BY repo_name
                ORDER BY repo_name
            """)
        ).mappings().all()
    repos = [dict(r) for r in rows]
    default = next((r["repo_name"] for r in repos if r["repo_name"] == "harness-self"), (repos[0]["repo_name"] if repos else None))
    return {"repos": repos, "default": default}


@app.post("/api/v1/repos/index", dependencies=[Depends(require_admin)])
def trigger_repo_index(payload: RepoIndexRequest):
    try:
        result = index_repository(
            engine=engine,
            repo_name=payload.repo_name,
            repo_url=payload.repo_url,
            branch=payload.branch
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"status": "INDEXED", **result}


@app.get("/api/v1/repos/{repo_name}/map", dependencies=[Depends(verify_auth)])
def get_repository_quick_map(repo_name: str, auth: Dict[str, Any] = Depends(verify_auth)):
    assert_repo_access(auth, repo_name)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT file_path, commit_sha, quick_summary, exported_symbols, imports,
                       locked_by_task_id::text AS locked_by_task_id
                FROM repo_file_map
                WHERE repo_name = :rname
                ORDER BY file_path ASC
            """),
            {"rname": repo_name}
        ).mappings().all()
    return {
        "repo_name": repo_name,
        "total_files": len(rows),
        "files": [dict(r) for r in rows]
    }


@app.post("/api/v1/context/pack", dependencies=[Depends(verify_auth)])
def build_surgical_context_pack(payload: ContextPackRequest, auth: Dict[str, Any] = Depends(verify_auth)):
    contract = None
    task = None
    handoff = None
    repo_name = payload.repo_name
    allowed_files = list(payload.allowed_files or [])
    allowed_symbols: List[str] = []
    profile_name = payload.model_profile

    with engine.connect() as conn:
        if payload.task_id:
            assert_task_project_access(auth, payload.task_id)
            try:
                contract = task_memory.task_contract(conn, payload.task_id, include_memory=True)
            except LookupError:
                raise HTTPException(status_code=404, detail="task not found")
            task = contract["task"]
            repo_name = repo_name or contract["plan"].get("repo_name")
            allowed_files = allowed_files or list(task.get("allowed_files") or [])
            allowed_symbols = list(task.get("allowed_symbols") or [])
            manifest = task.get("context_manifest") or {}
            profile_name = profile_name or manifest.get("model_profile")
            if payload.include_handoff:
                handoff = contract.get("handoff")
        if not repo_name:
            raise HTTPException(status_code=400, detail="repo_name or task_id is required")
        assert_repo_access(auth, repo_name)
        try:
            profile = get_profile(conn, profile_name or "local-32k")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        budget = context_budget(profile, payload.token_budget)
        if payload.task_id and payload.token_budget <= 2000:
            budget = context_budget(
                profile,
                min(int(task.get("max_input_tokens") or budget), int(profile["max_input_tokens"])),
            )
        if task:
            budget = max(300, min(budget, int(task.get("max_input_tokens") or budget)))

        q_vec = compute_embedding_768(payload.query)
        q_vec_str = "[" + ",".join(str(x) for x in q_vec) + "]"

        map_rows = conn.execute(
            sa.text("""
                SELECT file_path, quick_summary, locked_by_task_id::text AS locked_by_task_id
                FROM repo_file_map
                WHERE repo_name = :rname
                ORDER BY file_path ASC
            """),
            {"rname": repo_name}
        ).mappings().all()
        project_id = contract["plan"]["project_id"] if contract else None
        if not project_id:
            project = conn.execute(
                sa.text("SELECT id::text FROM harness_projects WHERE project_slug = :rname"),
                {"rname": repo_name}
            ).fetchone()
            project_id = project[0] if project else None
        doc_rows = []
        if payload.include_docs and project_id:
            doc_rows = search_documents(
                conn, project_id, payload.query,
                limit=max(payload.top_k, 8), qvec=q_vec,
            )

        if allowed_symbols and any("::" in item for item in allowed_symbols):
            symbol_names = [item.split("::", 1)[1] for item in allowed_symbols if "::" in item]
            symbol_files = [item.split("::", 1)[0] for item in allowed_symbols if "::" in item]
            vec_rows = conn.execute(
                sa.text("""
                    SELECT m.file_path, v.symbol_name, v.symbol_signature,
                           v.start_line, v.end_line, v.code_snippet,
                           ROUND((1 - (v.embedding <=> CAST(:qvec AS vector)))::numeric, 4) AS similarity
                    FROM code_ast_vectors v
                    JOIN repo_file_map m ON m.id = v.file_id
                    WHERE m.repo_name = :rname
                      AND m.file_path = ANY(:afiles)
                      AND v.symbol_name = ANY(:symbols)
                    ORDER BY v.embedding <=> CAST(:qvec AS vector) ASC
                    LIMIT :topk
                """),
                {"qvec": q_vec_str, "rname": repo_name, "afiles": symbol_files or allowed_files,
                 "symbols": symbol_names, "topk": max(payload.top_k, len(symbol_names))}
            ).mappings().all()
        elif allowed_files:
            vec_rows = conn.execute(
                sa.text("""
                    SELECT m.file_path, v.symbol_name, v.symbol_signature,
                           v.start_line, v.end_line, v.code_snippet,
                           ROUND((1 - (v.embedding <=> CAST(:qvec AS vector)))::numeric, 4) AS similarity
                    FROM code_ast_vectors v
                    JOIN repo_file_map m ON m.id = v.file_id
                    WHERE m.repo_name = :rname
                      AND m.file_path = ANY(:afiles)
                    ORDER BY v.embedding <=> CAST(:qvec AS vector) ASC
                    LIMIT :topk
                """),
                {"qvec": q_vec_str, "rname": repo_name, "afiles": allowed_files, "topk": payload.top_k}
            ).mappings().all()
        else:
            vec_rows = conn.execute(
                sa.text("""
                    SELECT m.file_path, v.symbol_name, v.symbol_signature,
                           v.start_line, v.end_line, v.code_snippet,
                           ROUND((1 - (v.embedding <=> CAST(:qvec AS vector)))::numeric, 4) AS similarity
                    FROM code_ast_vectors v
                    JOIN repo_file_map m ON m.id = v.file_id
                    WHERE m.repo_name = :rname
                    ORDER BY v.embedding <=> CAST(:qvec AS vector) ASC
                    LIMIT :topk
                """),
                {"qvec": q_vec_str, "rname": repo_name, "topk": payload.top_k}
            ).mappings().all()

    chars_per_token = float(profile.get("chars_per_token") or 3.6)
    contract_json = json.dumps(task, ensure_ascii=False, default=str) if task else ""
    handoff_json = json.dumps(handoff, ensure_ascii=False, default=str) if handoff else ""
    contract_tokens = estimate_token_budget(contract_json, chars_per_token) if contract_json else 0
    handoff_tokens = estimate_token_budget(handoff_json, chars_per_token) if handoff_json else 0
    if contract_tokens + handoff_tokens + 600 > budget:
        budget = min(
            int(profile["max_input_tokens"]),
            contract_tokens + handoff_tokens + 600,
        )
    docs_budget = int(budget * 0.25) if payload.include_docs else 0
    map_budget = int(budget * 0.10)
    ast_budget = max(0, budget - contract_tokens - handoff_tokens - docs_budget - map_budget)

    repo_overview = []
    used_map = 0
    for r in map_rows:
        line = f"{r['quick_summary']} [LOCK={r['locked_by_task_id'] or 'FREE'}]"
        line_tokens = estimate_token_budget(line, chars_per_token)
        if used_map + line_tokens > map_budget:
            break
        repo_overview.append(line)
        used_map += line_tokens

    selected_snippets = []
    used_ast = 0
    for vr in vec_rows:
        remaining = ast_budget - used_ast
        if remaining <= 40:
            break
        snippet_text = fit_text(
            vr["code_snippet"], remaining,
            chars_per_token=chars_per_token,
            suffix="\n# ... [truncated by token_budget]",
        )
        snippet_tokens = estimate_token_budget(snippet_text, chars_per_token)
        if snippet_tokens > remaining:
            snippet_text = fit_text(snippet_text, remaining, chars_per_token=chars_per_token)
            snippet_tokens = estimate_token_budget(snippet_text, chars_per_token)
        used_ast += snippet_tokens
        selected_snippets.append({
            "file_path": vr["file_path"],
            "symbol_name": vr["symbol_name"],
            "symbol_signature": vr["symbol_signature"],
            "lines": [vr["start_line"], vr["end_line"]],
            "similarity": float(vr["similarity"]),
            "code_snippet": snippet_text,
            "token_estimate": snippet_tokens,
        })

    relevant_doc_chunks = []
    used_docs = 0
    for doc in doc_rows:
        remaining = docs_budget - used_docs
        if remaining <= 40:
            break
        text = fit_text(
            doc["chunk_text"], remaining,
            chars_per_token=chars_per_token,
            suffix="\n[...truncated by documentation budget]",
        )
        text_tokens = estimate_token_budget(text, chars_per_token)
        used_docs += text_tokens
        relevant_doc_chunks.append({
            "document_id": doc["document_id"],
            "source_uri": doc["source_uri"],
            "title": doc["title"],
            "doc_type": doc["doc_type"],
            "section_heading": doc["section_heading"],
            "similarity": float(doc["similarity"]),
            "chunk_text": text,
            "token_estimate": text_tokens,
        })

    total_tokens = min(budget, used_map + used_ast + used_docs + contract_tokens + handoff_tokens)
    manifest = {
        "model_profile": profile["name"],
        "context_window_tokens": int(profile["context_window_tokens"]),
        "max_input_tokens": int(profile["max_input_tokens"]),
        "token_budget": int(budget),
        "reserved_tokens": int(profile["reserved_tokens"]),
        "chars_per_token": chars_per_token,
        "sections": {
            "contract": contract_tokens,
            "handoff": handoff_tokens,
            "repo_quick_map": used_map,
            "ast": used_ast,
            "documentation": used_docs,
        },
        "task_contract": bool(task),
        "allowed_symbols": allowed_symbols,
    }
    return {
        "repo_name": repo_name,
        "task_id": payload.task_id,
        "model_profile": profile["name"],
        "token_budget": budget,
        "estimated_tokens_used": total_tokens,
        "documentation_tokens_used": used_docs,
        "include_docs": payload.include_docs,
        "manifest": manifest,
        "allowed_files_contract": allowed_files or "READ_ONLY_UNRESTRICTED",
        "allowed_symbols_contract": allowed_symbols or "READ_ONLY_UNRESTRICTED",
        "repo_quick_map": repo_overview,
        "surgical_ast_chunks": selected_snippets,
        "relevant_doc_chunks": relevant_doc_chunks,
        "task_contract": task,
        "handoff": handoff if payload.include_handoff else None,
    }


@app.post("/api/v1/guardrails/validate-symbols", dependencies=[Depends(verify_auth)])
def validate_symbols_against_hallucination(payload: SymbolValidationRequest, auth: Dict[str, Any] = Depends(verify_auth)):
    assert_repo_access(auth, payload.repo_name)
    """Verifica si los metodos/clases que el agente intenta usar existen realmente en el AST del repositorio."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT file_path, exported_symbols
                FROM repo_file_map
                WHERE repo_name = :rname
            """),
            {"rname": payload.repo_name}
        ).mappings().all()

    known_symbols = {}
    known_qualified = {}
    known_classes = set()
    for r in rows:
        for sym in (r["exported_symbols"] or []):
            info = {
                "file_path": r["file_path"],
                "signature": sym["signature"],
                "type": sym["type"]
            }
            known_symbols[sym["name"]] = info
            if sym.get("qualified_name"):
                known_qualified[sym["qualified_name"]] = info
            if sym.get("type") == "class":
                known_classes.add(sym["name"])

    valid = {}
    hallucinated = []
    for raw_ref in payload.referenced_symbols:
        ref = raw_ref.strip()
        if ref in known_qualified:
            valid[ref] = known_qualified[ref]
            continue
        # Permite prefijos tipo modulo: src.main.register_agent_session
        suffix_match = next((qn for qn in known_qualified if ref.endswith("." + qn)), None)
        if suffix_match:
            valid[ref] = known_qualified[suffix_match]
            continue
        if "." in ref:
            prefix, last = ref.rsplit(".", 1)
            # Clase.metodo inexistente: no vale con que exista una funcion top-level homonima
            if prefix.split(".")[-1] in known_classes:
                hallucinated.append(ref)
            elif last in known_symbols:
                valid[ref] = known_symbols[last]
            else:
                hallucinated.append(ref)
            continue
        if ref in known_symbols:
            valid[ref] = known_symbols[ref]
        else:
            hallucinated.append(ref)

    return {
        "repo_name": payload.repo_name,
        "is_clean": len(hallucinated) == 0,
        "valid_symbols": valid,
        "hallucinated_symbols": hallucinated,
        "available_symbols_sample": list(known_symbols.keys())[:30]
    }


class PlanProposeRequest(BaseModel):
    requested_by: str = Field(..., min_length=2, max_length=100)
    repo_name: str = Field(..., min_length=2, max_length=150)
    requirement_prompt: str = Field(..., min_length=5, max_length=8000)
    required_skills_hint: Optional[List[str]] = None
    plan_a_override: Optional[Dict[str, Any]] = None
    plan_b_override: Optional[Dict[str, Any]] = None
    project_id: Optional[str] = None
    model_profile: Optional[str] = Field(default="local-32k", max_length=80)


class TaskFailRequest(BaseModel):
    error_reason: str = Field(..., min_length=1, max_length=2000)
    git_diff_summary: Optional[str] = None
    release: bool = True


class TaskSubmitRequest(BaseModel):
    file_changes: Optional[Dict[str, str]] = None
    unified_diff: Optional[str] = None
    commit_message: str = Field(default="", max_length=500)
    summary: Optional[str] = Field(default=None, max_length=4000)
    tests: List[str] = Field(default_factory=list)


class TaskCheckpointRequest(BaseModel):
    summary: str = Field(..., min_length=1, max_length=4000)
    decisions: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    tests: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    details: Optional[Dict[str, Any]] = None
    reset_context: bool = True
    token_estimate: int = Field(default=0, ge=0)


@app.post("/api/v1/plans/propose", dependencies=[Depends(require_admin)])
async def propose_dual_plan(payload: PlanProposeRequest):
    project_id = payload.project_id
    if project_id:
        with engine.connect() as conn:
            project = conn.execute(
                sa.text("SELECT id::text, project_slug FROM harness_projects WHERE id = CAST(:pid AS UUID)"),
                {"pid": project_id}
            ).mappings().fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project["project_slug"] != payload.repo_name:
            raise HTTPException(status_code=400, detail="repo_name no coincide con el project_slug")
    try:
        with engine.begin() as conn:
            result = generate_dual_plans_and_synthesize(
                conn=conn,
                repo_name=payload.repo_name,
                requested_by=payload.requested_by,
                requirement_prompt=payload.requirement_prompt,
                required_skills_hint=payload.required_skills_hint,
                plan_a_override=payload.plan_a_override,
                plan_b_override=payload.plan_b_override,
                project_id=project_id,
                model_profile=payload.model_profile or "local-32k",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await emit_war(
        "PLAN_APPROVED",
        {
            "plan_id": result["plan_id"],
            "repo_name": payload.repo_name,
            "requested_by": payload.requested_by,
            "scores": result["master_plan_c"].get("scores"),
            "tasks": result["created_tasks"],
        }
    )
    return result


@app.post("/api/v1/tasks/dispatch", dependencies=[Depends(require_admin)])
async def dispatch_tasks():
    with engine.begin() as conn:
        dispatched = dispatch_pending_tasks(conn, HEARTBEAT_TIMEOUT, MAX_CONTEXT_RATIO)

    prepared = []
    failed = []
    for item in dispatched:
        try:
            worktree = prepare_task_worktree(item["repo_name"], item["task_id"], item["git_branch"])
            item["worktree"] = str(worktree)
            prepared.append(item)
        except Exception as exc:
            release_task_assignment(engine, item["task_id"], item["assigned_to"]["session_id"])
            failed.append({"task_id": item["task_id"], "error": str(exc)})

    dispatched = prepared
    with engine.connect() as conn:
        for item in dispatched:
            item["active_task_skill_manuals"] = _skill_manuals(
                conn, item.get("required_skills") or []
            )
    for item in dispatched:
        ws = active_connections.get(item["assigned_to"]["session_id"])
        if ws:
            try:
                await ws.send_json({"event": "TASK_ASSIGNED", "task": item})
            except Exception:
                pass
        await emit_war(
            "TASK_ASSIGNED",
            {
                "task_id": item["task_id"],
                "title": item["title"],
                "required_skills": item.get("required_skills") or [],
                "active_task_skill_manuals": item.get("active_task_skill_manuals") or {},
                "git_branch": item["git_branch"],
                "allowed_files": item["allowed_files"],
                "worktree": item["worktree"],
                "assigned_to": item["assigned_to"],
            },
            target_session_id=item["assigned_to"]["session_id"],
        )

    return {"dispatched": dispatched, "count": len(dispatched), "failed": failed}


@app.get("/api/v1/tasks", dependencies=[Depends(verify_auth)])
def list_tasks(
    status: Optional[str] = None,
    plan_id: Optional[str] = None,
    assigned_session_id: Optional[str] = None,
    auth: Dict[str, Any] = Depends(verify_auth),
):
    if auth["role"] == "agent":
        assigned_session_id = auth["session_id"]
    clauses = []
    params: Dict[str, Any] = {}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if plan_id:
        clauses.append("plan_id = CAST(:plan_id AS UUID)")
        params["plan_id"] = plan_id
    if assigned_session_id:
        clauses.append("assigned_session_id = CAST(:assigned_session_id AS UUID)")
        params["assigned_session_id"] = assigned_session_id
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(f"""
                SELECT id::text, plan_id::text, assigned_session_id::text, title,
                       required_skills, git_branch, allowed_files, allowed_symbols,
                       allow_dependency_changes, description, task_context,
                       acceptance_criteria, test_commands, depends_on,
                       estimated_input_tokens, max_input_tokens, handoff_summary,
                       context_manifest, memory_epoch, task_kind, budget_status,
                       failed_attempts_memory, status, created_at, updated_at
                FROM harness_tasks{where}
                ORDER BY created_at ASC
            """),
            params
        ).mappings().all()
        tasks = []
        for row in rows:
            item = dict(row)
            item["skill_manuals"] = _skill_manuals(conn, item.get("required_skills") or [])
            tasks.append(item)

    return {"tasks": tasks}


@app.get("/api/v1/plans/{plan_id}")
def get_plan_detail(plan_id: str, auth: Dict[str, Any] = Depends(verify_auth)):
    assert_plan_project_access(auth, plan_id)
    with engine.connect() as conn:
        plan = conn.execute(
            sa.text("""
                SELECT id::text, requested_by, requirement_prompt, plan_a_json, plan_b_json,
                       arbiter_justification, master_plan_c_json, status, project_id::text,
                       model_profile, plan_budget_tokens, plan_metrics, created_at
                FROM feature_consensus_plans
                WHERE id = CAST(:pid AS UUID)
            """),
            {"pid": plan_id},
        ).mappings().first()
        if not plan:
            raise HTTPException(status_code=404, detail="plan not found")
        tasks = conn.execute(
            sa.text("""
                SELECT id::text, plan_id::text, assigned_session_id::text, title, required_skills,
                       git_branch, allowed_files, allowed_symbols, allow_dependency_changes,
                       description, task_context, acceptance_criteria, test_commands, depends_on,
                       estimated_input_tokens, max_input_tokens, handoff_summary, context_manifest,
                       memory_epoch, task_kind, budget_status, failed_attempts_memory, status,
                       created_at, updated_at
                FROM harness_tasks
                WHERE plan_id = CAST(:pid AS UUID)
                ORDER BY created_at ASC, title ASC
            """),
            {"pid": plan_id},
        ).mappings().all()
        memory = task_memory.list_plan_memory(conn, plan_id, limit=20)
    return {"plan": dict(plan), "tasks": [dict(t) for t in tasks], "memory": memory}


@app.get("/api/v1/tasks/{task_id}/contract")
def get_task_contract(task_id: str, auth: Dict[str, Any] = Depends(verify_auth)):
    assert_task_project_access(auth, task_id)
    with engine.connect() as conn:
        try:
            return task_memory.task_contract(conn, task_id, include_memory=True)
        except LookupError:
            raise HTTPException(status_code=404, detail="task not found")


@app.get("/api/v1/tasks/{task_id}/memory")
def get_task_memory(
    task_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    auth: Dict[str, Any] = Depends(verify_auth),
):
    assert_task_project_access(auth, task_id)
    with engine.connect() as conn:
        try:
            return {
                "task_id": task_id,
                "memory": task_memory.list_memory(conn, task_id, limit=limit),
                "handoff": task_memory.latest_handoff(conn, task_id),
            }
        except Exception as exc:
            print(f"[memory] {exc}")
            raise HTTPException(status_code=404, detail="task not found")


@app.post("/api/v1/tasks/{task_id}/checkpoint")
async def checkpoint_task(
    task_id: str,
    payload: TaskCheckpointRequest,
    auth: Dict[str, Any] = Depends(verify_auth),
):
    assert_task_project_access(auth, task_id)
    session_id = auth.get("session_id") if auth["role"] == "agent" else None
    try:
        result = await asyncio.to_thread(
            task_memory.checkpoint_task,
            engine, task_id, session_id, payload.summary,
            payload.decisions, payload.files, payload.tests, payload.next_steps,
            payload.details, auth.get("email") or "agent", payload.reset_context,
            payload.token_estimate,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="task not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    await emit_war("TASK_CHECKPOINT", {
        "task_id": task_id,
        "memory_epoch": result["memory_epoch"],
        "summary": payload.summary,
    }, source_session_id=session_id)
    return result


@app.post("/api/v1/tasks/{task_id}/resume")
async def resume_task(
    task_id: str,
    auth: Dict[str, Any] = Depends(verify_auth),
):
    assert_task_project_access(auth, task_id)
    session_id = auth.get("session_id") if auth["role"] == "agent" else None
    try:
        result = await asyncio.to_thread(task_memory.resume_task, engine, task_id, session_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="task not found")
    await emit_war("TASK_RESUMED", {
        "task_id": task_id,
        "has_handoff": bool(result.get("handoff")),
        "memory_entries": len(result.get("memory") or []),
    }, source_session_id=session_id)
    return result


@app.get("/api/v1/model-profiles")
def list_model_profiles(auth: Dict[str, Any] = Depends(verify_auth)):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT name, display_name, provider, model_id, context_window_tokens,
                       max_input_tokens, max_output_tokens, reserved_tokens,
                       chars_per_token, supports_tools, is_default
                FROM harness_model_profiles
                ORDER BY context_window_tokens ASC
            """)
        ).mappings().all()
    profiles = []
    for row in rows:
        item = dict(row)
        item["chars_per_token"] = float(item.get("chars_per_token") or 3.6)
        profiles.append(item)
    return {"profiles": profiles}


@app.post("/api/v1/tasks/{task_id}/fail-checkpoint")
async def fail_task_endpoint(
    task_id: str,
    payload: TaskFailRequest,
    auth: Dict[str, Any] = Depends(verify_auth),
):
    session_id = auth.get("session_id") if auth["role"] == "agent" else None
    try:
        with engine.begin() as conn:
            result = fail_task_checkpoint(
                conn=conn,
                task_id=task_id,
                session_id=session_id,
                error_reason=payload.error_reason,
                git_diff_summary=payload.git_diff_summary,
                release=payload.release,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TaskPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if result["released"]:
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("""
                        SELECT t.git_branch, p.master_plan_c_json->>'repo_name' AS repo_name
                        FROM harness_tasks t
                        JOIN feature_consensus_plans p ON p.id = t.plan_id
                        WHERE t.id = CAST(:tid AS UUID)
                    """),
                    {"tid": task_id}
                ).mappings().fetchone()
            if row and row["repo_name"]:
                cleanup_task_worktree(row["repo_name"], task_id, row["git_branch"])
        except Exception:
            pass

    if result["released"] and result["assigned_session_id"]:
        ws = active_connections.get(result["assigned_session_id"])
        if ws:
            try:
                await ws.send_json({"event": "CLEAR_LLM_CONTEXT", "task_id": task_id})
            except Exception:
                pass

    return result


@app.post("/api/v1/tasks/{task_id}/worktree", dependencies=[Depends(require_admin)])
def prepare_worktree_endpoint(task_id: str):
    try:
        return prepare_worktree_for_task(engine, task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except GitBarrierError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": exc.message, **exc.details}
        )


@app.post("/api/v1/tasks/{task_id}/submit")
async def submit_task_endpoint(
    task_id: str,
    payload: TaskSubmitRequest,
    auth: Dict[str, Any] = Depends(verify_auth),
):
    session_id = auth.get("session_id") if auth["role"] == "agent" else None
    assert_task_project_access(auth, task_id)
    try:
        result = await asyncio.to_thread(
            submit_task_changes,
            engine,
            task_id,
            session_id,
            payload.file_changes,
            payload.unified_diff,
            payload.commit_message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except GitTaskPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except GitBarrierError as exc:
        if exc.code == "MERGE_CONFLICT":
            await emit_war(
                "TASK_CONFLICT",
                {"task_id": task_id, "files": exc.details.get("files", []), "message": exc.message},
                source_session_id=session_id,
            )
        else:
            await emit_war(
                "TASK_BARRIER_FAILED",
                {
                    "task_id": task_id,
                    "barrier": exc.code,
                    "message": exc.message,
                    "files": exc.details.get("files", []),
                },
                source_session_id=session_id,
            )
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": exc.message, **exc.details}
        )

    if result["assigned_session_id"]:
        ws = active_connections.get(result["assigned_session_id"])
        if ws:
            try:
                await ws.send_json({"event": "CLEAR_LLM_CONTEXT", "task_id": task_id})
            except Exception:
                pass

    try:
        handoff_summary = payload.summary or (
            f"Tarea completada en commit {str(result.get('task_commit') or '')[:8]}. "
            f"Archivos: {', '.join(result.get('changed_files') or []) or 'sin cambios'}."
        )
        task_memory.persist_success_memory(
            engine, task_id, session_id, handoff_summary,
            files=result.get("changed_files") or [],
            tests=payload.tests,
            details={
                "task_commit": result.get("task_commit"),
                "main_commit": result.get("main_commit"),
                "author": result.get("author"),
            },
            created_by=auth.get("email") or "agent",
        )
    except Exception as exc:
        print(f"[memory] no se pudo persistir handoff: {exc}")

    await emit_war(
        "TASK_MERGED",
        {
            "task_id": task_id,
            "task_commit": result["task_commit"],
            "main_commit": result["main_commit"],
            "changed_files": result["changed_files"],
            "assigned_session_id": result["assigned_session_id"],
        },
        source_session_id=result["assigned_session_id"],
    )

    with engine.begin() as conn:
        project_row = conn.execute(
            sa.text("""
                SELECT p.project_id::text
                FROM harness_tasks t
                JOIN feature_consensus_plans p ON p.id = t.plan_id
                WHERE t.id = CAST(:tid AS UUID)
            """),
            {"tid": task_id}
        ).fetchone()
        if project_row and project_row[0]:
            job = enqueue_job(
                conn, project_row[0], "RAG_UPDATE_ARCHITECTURE",
                {"task_id": task_id, "reason": "task_merged"},
                idempotency_key=f"task-merged:{task_id}",
                priority=30,
            )
            record_event(
                engine,
                "HERMES_JOB_ENQUEUED",
                {"project_id": project_row[0], "task_id": task_id,
                 "job_type": "RAG_UPDATE_ARCHITECTURE", **job},
            )

    if result["assigned_session_id"]:
        with engine.connect() as conn:
            links = conn.execute(
                sa.text("""
                    SELECT target_session_id::text
                    FROM agent_links
                    WHERE source_session_id = CAST(:src AS UUID)
                      AND active = true
                      AND trigger_event = 'ON_TASK_MERGED'
                """),
                {"src": result["assigned_session_id"]}
            ).fetchall()
        for (target_id,) in links:
            target_ws = active_connections.get(target_id)
            delivered = False
            if target_ws:
                try:
                    await target_ws.send_json({
                        "event": "UPSTREAM_TASK_MERGED",
                        "from_session_id": result["assigned_session_id"],
                        "task_id": task_id,
                        "commit_sha": result["main_commit"],
                    })
                    delivered = True
                except Exception:
                    pass
            await emit_war(
                "AGENT_TO_AGENT_RELAY",
                {
                    "action": "upstream_task_merged",
                    "task_id": task_id,
                    "commit_sha": result["main_commit"],
                    "delivered": delivered,
                },
                source_session_id=result["assigned_session_id"],
                target_session_id=target_id,
            )

    return result


class SessionControlRequest(BaseModel):
    skills: Optional[List[str]] = None
    max_context_tokens: Optional[int] = Field(default=None, ge=1024)
    system_contract_rules: Optional[List[str]] = None


class WarRoomLinkRequest(BaseModel):
    source_session_id: str
    target_session_id: str
    link_type: str = Field(..., pattern="^(PIPELINE_HANDOFF|PEER_REVIEW|CONTEXT_RELAY|SUPERVISOR)$")
    trigger_event: str = Field(..., pattern="^(ON_TASK_MERGED|ON_CONFLICT|MANUAL_RELAY)$")


class WarRoomRelayRequest(BaseModel):
    target_session_id: str
    message_type: str = Field(..., min_length=1, max_length=60)
    content: str = Field(..., min_length=1, max_length=8000)
    task_id: Optional[str] = None


@app.patch("/api/v1/sessions/{session_id}/control", dependencies=[Depends(require_admin)])
async def control_session(session_id: str, payload: SessionControlRequest):
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("""
                SELECT id::text, skills, max_context_tokens, system_contract_rules
                FROM agent_sessions
                WHERE id = CAST(:sid AS UUID)
                FOR UPDATE
            """),
            {"sid": session_id}
        ).mappings().fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        if payload.skills is not None:
            conn.execute(
                sa.text("UPDATE agent_sessions SET skills = CAST(:skills AS JSONB) WHERE id = CAST(:sid AS UUID)"),
                {"skills": json.dumps(payload.skills), "sid": session_id}
            )
        if payload.max_context_tokens is not None:
            conn.execute(
                sa.text("UPDATE agent_sessions SET max_context_tokens = :max_tok WHERE id = CAST(:sid AS UUID)"),
                {"max_tok": payload.max_context_tokens, "sid": session_id}
            )
        if payload.system_contract_rules is not None:
            conn.execute(
                sa.text("UPDATE agent_sessions SET system_contract_rules = CAST(:rules AS JSONB) WHERE id = CAST(:sid AS UUID)"),
                {"rules": json.dumps(payload.system_contract_rules), "sid": session_id}
            )
        current = conn.execute(
            sa.text("SELECT skills, max_context_tokens, system_contract_rules FROM agent_sessions WHERE id = CAST(:sid AS UUID)"),
            {"sid": session_id}
        ).mappings().fetchone()

    event = {
        "event": "CONTROL_RULES_UPDATED",
        "skills": current["skills"],
        "max_context_tokens": current["max_context_tokens"],
        "system_contract_rules": current["system_contract_rules"],
    }
    ws = active_connections.get(session_id)
    delivered = False
    if ws:
        try:
            await ws.send_json(event)
            delivered = True
        except Exception:
            pass

    await emit_war(
        "SKILLS_INJECTED",
        {
            "session_id": session_id,
            "skills": current["skills"],
            "max_context_tokens": current["max_context_tokens"],
            "system_contract_rules": current["system_contract_rules"],
            "delivered": delivered,
        },
        target_session_id=session_id,
    )
    return {"session_id": session_id, **event, "delivered": delivered}


@app.post("/api/v1/war-room/links", dependencies=[Depends(require_admin)])
async def create_war_room_link(payload: WarRoomLinkRequest):
    if payload.source_session_id == payload.target_session_id:
        raise HTTPException(status_code=400, detail="source y target no pueden ser la misma sesion")
    with engine.begin() as conn:
        sessions = conn.execute(
            sa.text("""
                SELECT id::text FROM agent_sessions
                WHERE id IN (CAST(:src AS UUID), CAST(:tgt AS UUID))
            """),
            {"src": payload.source_session_id, "tgt": payload.target_session_id}
        ).fetchall()
        if len(sessions) != 2:
            raise HTTPException(status_code=404, detail="Session not found")
        row = conn.execute(
            sa.text("""
                INSERT INTO agent_links
                    (source_session_id, target_session_id, link_type, trigger_event, active)
                VALUES
                    (CAST(:src AS UUID), CAST(:tgt AS UUID), :ltype, :trigger, true)
                ON CONFLICT (source_session_id, target_session_id, link_type)
                DO UPDATE SET active = true, trigger_event = EXCLUDED.trigger_event
                RETURNING id::text, source_session_id::text, target_session_id::text,
                          link_type, trigger_event, active, created_at
            """),
            {
                "src": payload.source_session_id,
                "tgt": payload.target_session_id,
                "ltype": payload.link_type,
                "trigger": payload.trigger_event,
            }
        ).mappings().fetchone()

    result = dict(row)
    await emit_war(
        "AGENT_TO_AGENT_RELAY",
        {"action": "link_created", **result},
        source_session_id=payload.source_session_id,
        target_session_id=payload.target_session_id,
    )
    return result


@app.delete("/api/v1/war-room/links/{link_id}", dependencies=[Depends(require_admin)])
async def delete_war_room_link(link_id: str):
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("""
                SELECT id::text, source_session_id::text, target_session_id::text, link_type
                FROM agent_links WHERE id = CAST(:lid AS UUID)
            """),
            {"lid": link_id}
        ).mappings().fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Link not found")
        conn.execute(sa.text("DELETE FROM agent_links WHERE id = CAST(:lid AS UUID)"), {"lid": link_id})

    await emit_war(
        "AGENT_TO_AGENT_RELAY",
        {"action": "link_deleted", **dict(row)},
        source_session_id=row["source_session_id"],
        target_session_id=row["target_session_id"],
    )
    return {"deleted": link_id}


@app.get("/api/v1/war-room/topology", dependencies=[Depends(require_admin)])
def war_room_topology():
    return get_topology(engine)


@app.post("/api/v1/war-room/relay")
async def relay_agent_message(payload: WarRoomRelayRequest, auth: Dict[str, Any] = Depends(verify_auth)):
    if auth["role"] != "agent":
        raise HTTPException(status_code=403, detail="Agent bearer required")
    source_id = auth["session_id"]
    with engine.connect() as conn:
        target = conn.execute(
            sa.text("SELECT id::text, status, project_id::text AS project_id FROM agent_sessions WHERE id = CAST(:tid AS UUID)"),
            {"tid": payload.target_session_id}
        ).mappings().fetchone()
        link = conn.execute(
            sa.text("""
                SELECT id::text FROM agent_links
                WHERE source_session_id = CAST(:src AS UUID)
                  AND target_session_id = CAST(:tgt AS UUID)
                  AND active = true
            """),
            {"src": source_id, "tgt": payload.target_session_id}
        ).fetchone()
    if not target or target["status"] not in ("ONLINE", "BUSY") or not link:
        raise HTTPException(status_code=403, detail="Link Not Authorized in War Room Topology")
    if auth.get("project_id") and target.get("project_id") and auth["project_id"] != target["project_id"]:
        raise HTTPException(status_code=403, detail="Cross-Project Isolation Violation")

    ws = active_connections.get(payload.target_session_id)
    delivered = False
    if ws:
        try:
            await ws.send_json({
                "event": "AGENT_MESSAGE",
                "from_session_id": source_id,
                "message_type": payload.message_type,
                "content": payload.content,
                "task_id": payload.task_id,
            })
            delivered = True
        except Exception:
            pass

    await emit_war(
        "AGENT_TO_AGENT_RELAY",
        {
            "action": "relay",
            "message_type": payload.message_type,
            "content": payload.content,
            "task_id": payload.task_id,
            "delivered": delivered,
        },
        source_session_id=source_id,
        target_session_id=payload.target_session_id,
    )
    return {
        "delivered": delivered,
        "source_session_id": source_id,
        "target_session_id": payload.target_session_id,
    }


def _is_portal_admin_token(token: str) -> bool:
    try:
        decoded = jwt.decode(
            token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "jti", "sub"]}
        )
    except Exception:
        return False
    if decoded.get("typ") != "portal":
        return False
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT role, status FROM harness_users WHERE id = CAST(:uid AS UUID)"),
            {"uid": decoded.get("sub")}
        ).fetchone()
    return bool(row and row[0] == "ADMIN" and row[1] == "ACTIVE")


@app.websocket("/ws/war-room")
async def war_room_socket(websocket: WebSocket):
    header_key = websocket.headers.get("x-harness-admin-key") or ""
    authorized = bool(ADMIN_API_KEY and header_key and hmac.compare_digest(header_key, ADMIN_API_KEY))
    if not authorized:
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        except Exception:
            await websocket.close(code=4003, reason="Missing admin credentials")
            return
        if isinstance(first, dict) and first.get("type") == "AUTH":
            candidate = first.get("admin_key")
            portal_token = first.get("portal_token")
            if candidate and ADMIN_API_KEY and hmac.compare_digest(str(candidate), ADMIN_API_KEY):
                authorized = True
            elif portal_token and _is_portal_admin_token(str(portal_token)):
                authorized = True
        if not authorized:
            await websocket.close(code=4003, reason="Invalid admin credentials")
            return
    else:
        await websocket.accept()

    war_room_clients.add(websocket)
    try:
        await websocket.send_json({
            "event": "WAR_ROOM_CONNECTED",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        war_room_clients.discard(websocket)


@app.get("/war-room", response_class=HTMLResponse)
@app.get("/portal", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def war_room_page():
    return WAR_ROOM_HTML


@app.get("/api/v1/client/bridge.py", response_class=PlainTextResponse)
def download_bridge():
    bridge_path = Path(__file__).resolve().parent.parent / "client" / "harness_mcp_bridge.py"
    if not bridge_path.exists():
        raise HTTPException(status_code=404, detail="bridge not found")
    return PlainTextResponse(
        bridge_path.read_text(encoding="utf-8"),
        media_type="text/x-python",
        headers={"Content-Disposition": 'attachment; filename="harness_mcp_bridge.py"'},
    )


@app.post("/api/v1/tasks/{task_id}/reopen-from-main")
async def reopen_task_from_main(task_id: str, auth: Dict[str, Any] = Depends(verify_auth)):
    with engine.connect() as conn:
        task = conn.execute(
            sa.text("""
                SELECT t.id::text, t.status, t.assigned_session_id::text AS assigned_session_id,
                       t.git_branch, t.allowed_files,
                       p.master_plan_c_json->>'repo_name' AS repo_name
                FROM harness_tasks t
                JOIN feature_consensus_plans p ON p.id = t.plan_id
                WHERE t.id = CAST(:tid AS UUID)
            """),
            {"tid": task_id}
        ).mappings().fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if auth["role"] == "agent" and auth.get("session_id") != task["assigned_session_id"]:
        raise HTTPException(status_code=403, detail="task assigned to another session")
    if task["status"] != "CONFLICT":
        raise HTTPException(status_code=400, detail=f"Task status is {task['status']}, expected CONFLICT")
    session_id = task["assigned_session_id"]
    repo_name = task["repo_name"]
    if not session_id or not repo_name:
        raise HTTPException(status_code=400, detail="Task has no assigned session or repo_name")

    def norm(p: str) -> str:
        value = str(p).replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        return value

    allowed = [norm(p) for p in (task["allowed_files"] or [])]

    with engine.connect() as conn:
        locked = conn.execute(
            sa.text("""
                SELECT file_path FROM repo_file_map
                WHERE repo_name = :rname
                  AND file_path = ANY(:files)
                  AND locked_by_task_id IS NOT NULL
                  AND locked_by_task_id <> CAST(:tid AS UUID)
            """),
            {"rname": repo_name, "files": allowed, "tid": task_id}
        ).fetchall()
    if locked:
        raise HTTPException(
            status_code=409,
            detail={"error": "FILES_LOCKED", "files": [x[0] for x in locked]}
        )

    try:
        worktree = await asyncio.to_thread(
            prepare_task_worktree, repo_name, task_id, task["git_branch"]
        )
    except GitBarrierError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": exc.code, "message": exc.message})

    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:rname))"), {"rname": repo_name})
            locked = conn.execute(
                sa.text("""
                    SELECT file_path FROM repo_file_map
                    WHERE repo_name = :rname
                      AND file_path = ANY(:files)
                      AND locked_by_task_id IS NOT NULL
                      AND locked_by_task_id <> CAST(:tid AS UUID)
                """),
                {"rname": repo_name, "files": allowed, "tid": task_id}
            ).fetchall()
            if locked:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "FILES_LOCKED", "files": [x[0] for x in locked]}
                )
            conn.execute(
                sa.text("UPDATE harness_tasks SET status = 'IN_PROGRESS' WHERE id = CAST(:tid AS UUID)"),
                {"tid": task_id}
            )
            if allowed:
                conn.execute(
                    sa.text("""
                        UPDATE repo_file_map
                        SET locked_by_task_id = CAST(:tid AS UUID)
                        WHERE repo_name = :rname AND file_path = ANY(:files)
                    """),
                    {"tid": task_id, "rname": repo_name, "files": allowed}
                )
            conn.execute(
                sa.text("UPDATE agent_sessions SET status = 'BUSY' WHERE id = CAST(:sid AS UUID)"),
                {"sid": session_id}
            )
    except HTTPException:
        try:
            cleanup_task_worktree(repo_name, task_id, task["git_branch"])
        except Exception:
            pass
        raise

    await emit_war(
        "TASK_ASSIGNED",
        {
            "action": "reopen_from_main",
            "task_id": task_id,
            "worktree": str(worktree),
            "git_branch": task["git_branch"],
            "allowed_files": allowed,
            "assigned_session_id": session_id,
        },
        target_session_id=session_id,
    )
    return {
        "task_id": task_id,
        "status": "IN_PROGRESS",
        "worktree": str(worktree),
        "git_branch": task["git_branch"],
        "allowed_files": allowed,
        "assigned_session_id": session_id,
        "reacquired_files": allowed,
    }


# ---------------------------------------------------------------------------
# Etapa 7B - Portal: auth por correo, proyectos, membresias y sesiones 1:1
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch((email or "").strip().lower()))


def _password_policy_error(password: str) -> Optional[str]:
    if len(password) < 8:
        return "La contrasena debe tener al menos 8 caracteres"
    if len(password) > 128:
        return "La contrasena no puede superar 128 caracteres"
    if not re.search(r"[A-Z]", password):
        return "La contrasena debe incluir al menos una mayuscula"
    if not re.search(r"[a-z]", password):
        return "La contrasena debe incluir al menos una minuscula"
    if not re.search(r"[0-9]", password):
        return "La contrasena debe incluir al menos un numero"
    if not re.search(r"[^A-Za-z0-9]", password):
        return "La contrasena debe incluir al menos un simbolo"
    return None


def _generate_temporary_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%&*+-_"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if _password_policy_error(candidate) is None:
            return candidate


def _skill_capabilities(conn, user_id: Optional[str]) -> Dict[str, bool]:
    defaults = {
        "can_create_skills": False,
        "can_clone_skills": False,
        "can_edit_own_skills": True,
        "can_grant_skills": False,
    }
    if not user_id:
        return defaults
    row = conn.execute(
        sa.text("""
            SELECT can_create_skills, can_clone_skills, can_edit_own_skills, can_grant_skills
            FROM user_skill_capabilities WHERE user_id = CAST(:uid AS UUID)
        """),
        {"uid": user_id}
    ).mappings().fetchone()
    return dict(row) if row else defaults


def _skill_acl_row(conn, skill_id: str, user_id: Optional[str]):
    if not user_id:
        return None
    return conn.execute(
        sa.text("""
            SELECT can_use, can_edit, can_clone, can_grant
            FROM skill_permissions
            WHERE skill_id = CAST(:sid AS UUID) AND user_id = CAST(:uid AS UUID)
        """),
        {"sid": skill_id, "uid": user_id}
    ).mappings().fetchone()


def _skill_flags(conn, skill, auth: Dict[str, Any]) -> Dict[str, bool]:
    is_admin = auth.get("role_name") == "ADMIN"
    uid = auth.get("user_id")
    caps = _skill_capabilities(conn, uid)
    acl = _skill_acl_row(conn, skill["id"], uid)
    owner = bool(uid and skill.get("owner_user_id") == uid)
    acl_edit = bool(acl and acl["can_edit"])
    acl_clone = bool(acl and acl["can_clone"])
    acl_grant = bool(acl and acl["can_grant"])
    acl_use = bool(acl and acl["can_use"])
    can_edit = is_admin or (
        not skill["is_official"] and (
            (owner and caps["can_edit_own_skills"]) or acl_edit
        )
    )
    can_clone = is_admin or caps["can_clone_skills"] or acl_clone
    can_grant = is_admin or (
        not skill["is_official"] and (
            (owner and caps["can_grant_skills"]) or acl_grant
        )
    )
    assigned = False
    if uid:
        assigned = bool(conn.execute(
            sa.text("""
                SELECT 1 FROM membership_skills
                WHERE skill_id = CAST(:sid AS UUID) AND user_id = CAST(:uid AS UUID)
                LIMIT 1
            """),
            {"sid": skill["id"], "uid": uid}
        ).fetchone())
    can_use = bool(
        is_admin or skill["is_official"] or skill["visibility"] == "SHARED"
        or owner or acl_use or assigned
    )
    return {
        "can_use": can_use,
        "can_edit": can_edit,
        "can_clone": can_clone,
        "can_grant": can_grant,
        "is_owner": owner,
    }


def _can_view_skill(conn, skill, auth: Dict[str, Any]) -> bool:
    flags = _skill_flags(conn, skill, auth)
    if flags["can_use"] or flags["can_edit"] or flags["can_clone"] or flags["can_grant"]:
        return True
    uid = auth.get("user_id")
    if uid:
        assigned = conn.execute(
            sa.text("""
                SELECT 1 FROM membership_skills
                WHERE skill_id = CAST(:sid AS UUID) AND user_id = CAST(:uid AS UUID)
                LIMIT 1
            """),
            {"sid": skill["id"], "uid": uid}
        ).fetchone()
        if assigned:
            return True
    return False


def _skill_summary(conn, skill, auth: Dict[str, Any]) -> Dict[str, Any]:
    flags = _skill_flags(conn, skill, auth)
    return {
        "id": skill["id"],
        "slug": skill["slug"],
        "display_name": skill["display_name"],
        "category": skill["category"],
        "trigger_description": skill["trigger_description"],
        "is_official": skill["is_official"],
        "status": skill["status"],
        "visibility": skill["visibility"],
        "owner_user_id": skill["owner_user_id"],
        "version": skill["version"],
        "compiled_tokens": skill["compiled_tokens"],
        "updated_at": skill["updated_at"],
        **flags,
        "is_read_only": not flags["can_edit"],
    }


def _unique_skill_slug(conn, base: str) -> str:
    base = slugify(base, 90)
    candidate = base
    suffix = 2
    while conn.execute(
        sa.text("SELECT 1 FROM harness_skills WHERE slug = :slug"),
        {"slug": candidate}
    ).fetchone():
        candidate = f"{base[:85]}-{suffix}"
        suffix += 1
    return candidate


def _ensure_skill_exists(conn, slug: str, user_id: Optional[str]) -> str:
    slug = slugify(slug, 100)
    existing = conn.execute(
        sa.text("SELECT id::text FROM harness_skills WHERE slug = :slug"),
        {"slug": slug}
    ).fetchone()
    if existing:
        return existing[0]
    compiled = compile_standard_skill_md(
        slug,
        slug.replace("-", " ").title(),
        "general",
        f"Usa esta habilidad cuando la tarea requiera {slug.replace('-', ' ')}.",
        f"Ejecutar de forma verificable la habilidad {slug}.",
        [
            "Aclara el objetivo y el resultado esperado.",
            "Ejecuta el trabajo siguiendo las convenciones del proyecto.",
            "Verifica el resultado antes de declararlo completado.",
        ],
        ["No inventes informacion.", "No declares completado algo que no verificaste."],
        "Entrega el resultado y la evidencia de verificacion.",
    )
    skill_id = str(uuid.uuid4())
    conn.execute(
        sa.text("""
            INSERT INTO harness_skills
                (id, slug, display_name, category, trigger_description, objective,
                 workflow_steps, guardrails_forbidden, output_criteria, compiled_skill_md,
                 created_by_user_id, owner_user_id, is_official, status, visibility,
                 version, compiled_tokens)
            VALUES
                (CAST(:id AS UUID), :slug, :name, 'general', :trigger, :objective,
                 CAST(:steps AS JSONB), CAST(:guardrails AS JSONB), :output, :compiled,
                 CAST(:created_by AS UUID), NULL, false, 'PUBLISHED', 'PRIVATE', 1, :tokens)
        """),
        {
            "id": skill_id, "slug": slug, "name": slug.replace("-", " ").title(),
            "trigger": f"Usa esta habilidad cuando la tarea requiera {slug.replace('-', ' ')}.",
            "objective": f"Ejecutar de forma verificable la habilidad {slug}.",
            "steps": json.dumps([
                "Aclara el objetivo y el resultado esperado.",
                "Ejecuta el trabajo siguiendo las convenciones del proyecto.",
                "Verifica el resultado antes de declararlo completado.",
            ]),
            "guardrails": json.dumps(["No inventes informacion.", "No declares completado algo que no verificaste."]),
            "output": "Entrega el resultado y la evidencia de verificacion.",
            "compiled": compiled,
            "created_by": user_id,
            "tokens": estimate_tokens(compiled),
        }
    )
    return skill_id


def _sync_membership_skills(
    conn,
    project_id: str,
    user_id: str,
    skill_slugs: List[str],
    assigned_by: Optional[str],
    replace: bool = True,
) -> List[str]:
    normalized = []
    for raw in skill_slugs or []:
        slug = slugify(str(raw), 100)
        if slug and slug not in normalized:
            normalized.append(slug)
    if replace:
        conn.execute(
            sa.text("""
                DELETE FROM membership_skills
                WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
            """),
            {"pid": project_id, "uid": user_id}
        )
    for slug in normalized:
        skill_id = _ensure_skill_exists(conn, slug, assigned_by)
        conn.execute(
            sa.text("""
                INSERT INTO project_skills (id, project_id, skill_id, assigned_by_user_id)
                VALUES (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:sid AS UUID),
                        CAST(:by AS UUID))
                ON CONFLICT (project_id, skill_id) DO NOTHING
            """),
            {"id": str(uuid.uuid4()), "pid": project_id, "sid": skill_id, "by": assigned_by}
        )
        conn.execute(
            sa.text("""
                INSERT INTO membership_skills (id, project_id, user_id, skill_id, assigned_by_user_id)
                VALUES (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:uid AS UUID),
                        CAST(:sid AS UUID), CAST(:by AS UUID))
                ON CONFLICT (project_id, user_id, skill_id) DO NOTHING
            """),
            {
                "id": str(uuid.uuid4()), "pid": project_id, "uid": user_id,
                "sid": skill_id, "by": assigned_by,
            }
        )
    conn.execute(
        sa.text("""
            UPDATE project_memberships
            SET allowed_skills = CAST(:skills AS JSONB)
            WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
        """),
        {"skills": json.dumps(normalized), "pid": project_id, "uid": user_id}
    )
    return normalized


def _skill_manuals(conn, slugs: List[str]) -> Dict[str, Dict[str, Any]]:
    unique = list(dict.fromkeys(str(s) for s in (slugs or []) if str(s).strip()))
    if not unique:
        return {}
    params = {f"s{idx}": slug for idx, slug in enumerate(unique)}
    placeholders = ", ".join(f":s{idx}" for idx in range(len(unique)))
    rows = conn.execute(
        sa.text(f"""
            SELECT slug, display_name, category, compiled_skill_md, compiled_tokens
            FROM harness_skills
            WHERE slug IN ({placeholders}) AND status = 'PUBLISHED'
        """),
        params
    ).mappings().all()
    return {row["slug"]: dict(row) for row in rows}


def _public_user(user) -> Dict[str, Any]:
    item = dict(user)
    item.pop("password_hash", None)
    item.pop("invite_token", None)
    item.pop("mfa_totp_secret_vault", None)
    item.pop("mfa_last_used_step", None)
    item.pop("failed_login_attempts", None)
    item.pop("locked_until", None)
    return item


def _issue_portal_token(user) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "typ": "portal",
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + PORTAL_JWT_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


class PortalLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    # Se permite cualquier longitud en login para la credencial legacy admin/admin.
    # La politica de complejidad solo se exige al crear o cambiar contrasenas.
    password: str = Field(..., min_length=1, max_length=200)
    totp_code: Optional[str] = Field(default=None, max_length=10)


class PortalRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=200)
    full_name: str = Field(..., min_length=2, max_length=150)
    invite_token: Optional[str] = Field(default=None, max_length=128)


class PortalSessionRequest(BaseModel):
    project_id: str
    agent_tool: str = Field(..., pattern="^(opencode|claudecode|cursor|hermes|pi|custom)$")
    llm_provider: str = Field(..., min_length=2, max_length=100)
    max_context_tokens: int = Field(default=32000, gt=1024)
    model_profile: Optional[str] = Field(default=None, max_length=80)


class ProjectCreateRequest(BaseModel):
    project_slug: str = Field(..., min_length=2, max_length=150)
    display_name: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = None
    repo_url: Optional[str] = None
    default_branch: str = Field(default="main", max_length=80)


class ProjectMemberRequest(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = Field(default=None, max_length=150)
    password: Optional[str] = Field(default=None, min_length=8, max_length=200)
    invite_token: Optional[str] = Field(default=None, max_length=128)
    allowed_skills: List[str] = Field(default_factory=list)


LICENSE_LOGIN_MAX_ATTEMPTS = 5
LICENSE_LOGIN_LOCK_MINUTES = 15


def _license_audit_login(event_type: str, email: Optional[str], data: Dict[str, Any], request: Optional[Request]) -> None:
    try:
        LICENSE_MANAGER.record_audit(
            event_type,
            email,
            data,
            context={"actor_ip": _client_ip(request)},
        )
    except Exception as exc:
        print(f"[license] audit login event failed: {exc}")


def _register_license_login_failure(user, reason: str, request: Optional[Request]) -> None:
    attempts = None
    locked_until = None
    try:
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("""
                    UPDATE harness_users
                    SET failed_login_attempts = failed_login_attempts + 1,
                        locked_until = CASE
                            WHEN failed_login_attempts + 1 >= :max_attempts THEN :lock_until
                            ELSE locked_until
                        END
                    WHERE id = CAST(:uid AS UUID)
                    RETURNING failed_login_attempts, locked_until
                """),
                {
                    "uid": user["id"],
                    "max_attempts": LICENSE_LOGIN_MAX_ATTEMPTS,
                    "lock_until": now + timedelta(minutes=LICENSE_LOGIN_LOCK_MINUTES),
                },
            ).mappings().first()
            if row:
                attempts = int(row["failed_login_attempts"] or 0)
                locked_until = row["locked_until"]
    except Exception as exc:
        print(f"[license] failed to register login failure: {exc}")
    _license_audit_login(
        "LICENSE_ADMIN_LOGIN_FAILED",
        user["email"],
        {
            "reason": reason,
            "failed_attempts": attempts,
            "locked_until": locked_until.isoformat() if locked_until else None,
        },
        request,
    )


def _license_login_success(user, step: int, request: Optional[Request]) -> bool:
    try:
        with engine.begin() as conn:
            changed = conn.execute(
                sa.text("""
                    UPDATE harness_users
                    SET mfa_last_used_step = :step,
                        failed_login_attempts = 0,
                        locked_until = NULL
                    WHERE id = CAST(:uid AS UUID)
                      AND (mfa_last_used_step IS NULL OR mfa_last_used_step < :step)
                """),
                {"uid": user["id"], "step": step},
            ).rowcount
        if not changed:
            return False
    except Exception as exc:
        print(f"[license] failed to persist MFA step: {exc}")
        return False
    _license_audit_login(
        "LICENSE_ADMIN_LOGIN",
        user["email"],
        {"mfa_step": step},
        request,
    )
    return True


@app.post("/api/v1/auth/login")
def portal_login(payload: PortalLoginRequest, request: Request):
    email = payload.email.strip().lower()
    with engine.connect() as conn:
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, full_name, role, status, password_hash, default_skills,
                       mfa_enabled, mfa_totp_secret_vault, password_change_allowed,
                       mfa_last_used_step, failed_login_attempts, locked_until
                FROM harness_users WHERE email = :email
            """),
            {"email": email}
        ).mappings().fetchone()
    if user and user["role"] == "LICENSE_ADMIN" and user["locked_until"]:
        locked_until = user["locked_until"]
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > datetime.now(timezone.utc):
            _license_audit_login(
                "LICENSE_ADMIN_LOGIN_LOCKED",
                user["email"],
                {"locked_until": locked_until.isoformat()},
                request,
            )
            raise HTTPException(
                status_code=423,
                detail={
                    "code": "ACCOUNT_LOCKED",
                    "message": "License operator account is temporarily locked",
                    "locked_until": locked_until.isoformat(),
                },
            )
    if not user or not verify_password(payload.password, user["password_hash"]):
        if user and user["role"] == "LICENSE_ADMIN":
            _register_license_login_failure(user, "invalid_password", request)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user["status"] != "ACTIVE":
        raise HTTPException(status_code=403, detail="User not active")
    if user["role"] == "LICENSE_ADMIN":
        if not user["mfa_enabled"] or not user["mfa_totp_secret_vault"]:
            raise HTTPException(status_code=403, detail="License operator MFA is not configured")
        if not payload.totp_code:
            raise HTTPException(status_code=401, detail={
                "code": "MFA_REQUIRED",
                "message": "TOTP code required for license operator",
            })
        secret = open_secret(user["mfa_totp_secret_vault"])
        step = totp.matching_step(secret, payload.totp_code) if secret else None
        if step is None:
            _register_license_login_failure(user, "invalid_totp", request)
            raise HTTPException(status_code=401, detail={
                "code": "MFA_INVALID",
                "message": "Invalid TOTP code",
            })
        if not _license_login_success(user, step, request):
            _register_license_login_failure(user, "totp_replay", request)
            raise HTTPException(status_code=401, detail={
                "code": "MFA_REPLAY",
                "message": "TOTP code has already been used",
            })
    token = _issue_portal_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_hours": PORTAL_JWT_TTL_HOURS,
        "user": _public_user(user),
    }


@app.post("/api/v1/auth/register-invite")
def portal_register_invite(payload: PortalRegisterRequest):
    assert_license_operational()
    email = payload.email.strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email")
    activated = False
    with engine.begin() as conn:
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, full_name, role, status, invite_token, default_skills
                FROM harness_users WHERE email = :email FOR UPDATE
            """),
            {"email": email}
        ).mappings().fetchone()
        if user:
            if user["status"] == "ACTIVE":
                raise HTTPException(status_code=409, detail="User already active")
            if user["status"] == "DISABLED":
                raise HTTPException(status_code=403, detail="User disabled")
            if user["status"] == "INVITED":
                if not payload.invite_token or not user["invite_token"] or not hmac.compare_digest(payload.invite_token, user["invite_token"]):
                    raise HTTPException(status_code=403, detail="Invalid invite token")
                conn.execute(
                    sa.text("""
                        UPDATE harness_users
                        SET password_hash = :ph, full_name = :name, status = 'ACTIVE', invite_token = NULL
                        WHERE id = CAST(:uid AS UUID)
                    """),
                    {"ph": hash_password(payload.password), "name": payload.full_name, "uid": user["id"]}
                )
                activated = True
            elif user["status"] == "PENDING":
                raise HTTPException(status_code=409, detail="Registration pending administrator approval")
        else:
            if payload.invite_token:
                raise HTTPException(status_code=403, detail="Invalid invite token")
            policy = get_policy(conn, for_update=True)
            assert_registered_user_quota(conn, policy)
            conn.execute(
                sa.text("""
                    INSERT INTO harness_users
                        (id, email, full_name, password_hash, role, status, default_skills)
                    VALUES
                        (CAST(:id AS UUID), :email, :name, :ph, 'ENGINEER', 'PENDING', CAST('[]' AS JSONB))
                """),
                {
                    "id": str(uuid.uuid4()),
                    "email": email,
                    "name": payload.full_name,
                    "ph": hash_password(payload.password),
                }
            )

    with engine.connect() as conn:
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, full_name, role, status, default_skills
                FROM harness_users WHERE email = :email
            """),
            {"email": email}
        ).mappings().fetchone()
    if activated:
        return {"status": "ACTIVE", "access_token": _issue_portal_token(user), "token_type": "bearer", "user": _public_user(user)}
    return {"status": "PENDING", "user": _public_user(user), "message": "Awaiting administrator approval"}


@app.get("/api/v1/auth/me")
def portal_me(auth: Dict[str, Any] = Depends(verify_portal)):
    uid = auth["user_id"]
    with engine.connect() as conn:
        projects = conn.execute(
            sa.text("""
                SELECT p.id::text, p.project_slug, p.display_name, p.description,
                       p.default_branch, m.allowed_skills, m.status AS membership_status,
                       m.created_at AS membership_created_at
                FROM project_memberships m
                JOIN harness_projects p ON p.id = m.project_id
                WHERE m.user_id = CAST(:uid AS UUID) AND m.status = 'APPROVED'
                ORDER BY p.project_slug
            """),
            {"uid": uid}
        ).mappings().all()
        sessions = conn.execute(
            sa.text("""
                SELECT s.id::text, s.project_id::text, p.project_slug, s.agent_tool,
                       s.status, s.current_context_tokens, s.max_context_tokens, s.last_heartbeat
                FROM agent_sessions s
                JOIN harness_projects p ON p.id = s.project_id
                WHERE s.user_id = CAST(:uid AS UUID) AND s.status IN ('ONLINE', 'BUSY')
                ORDER BY p.project_slug, s.agent_tool
            """),
            {"uid": uid}
        ).mappings().all()
    return {
        "user": auth["user"],
        "projects": [dict(x) for x in projects],
        "sessions": [dict(x) for x in sessions],
    }


@app.post("/api/v1/projects")
def create_project(payload: ProjectCreateRequest, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    assert_license_operational()
    try:
        slug = validate_repo_name(payload.project_slug)
        repo_url = validate_repo_url(payload.repo_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", payload.default_branch):
        raise HTTPException(status_code=400, detail="Invalid default_branch")
    with engine.connect() as conn:
        exists = conn.execute(sa.text("SELECT id FROM harness_projects WHERE project_slug = :slug"), {"slug": slug}).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail="Project already exists")

    # Pre-check antes de clonar/indexar: un plan lleno no debe dejar side effects.
    with engine.begin() as conn:
        policy = get_policy(conn, for_update=True)
        assert_project_slot_available(conn, policy)
        assert_hermes_slot_available(conn, policy)

    try:
        ensure_project_repo(slug, repo_url, payload.default_branch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Repository setup failed: {exc}")
    try:
        index_result = index_repository(engine, slug)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Initial index failed: {exc}")

    try:
        with engine.begin() as conn:
            policy = get_policy(conn, for_update=True)
            assert_project_slot_available(conn, policy)
            assert_hermes_slot_available(conn, policy)
            project = conn.execute(
                sa.text("""
                    INSERT INTO harness_projects
                        (id, project_slug, display_name, description, repo_url,
                         default_branch, created_by_user_id, is_active_slot)
                    VALUES
                        (CAST(:id AS UUID), :slug, :display_name, :description, :repo_url,
                         :branch, CAST(:created_by AS UUID), true)
                    RETURNING id::text, project_slug, display_name, description, repo_url,
                              default_branch, created_by_user_id::text, created_at, is_active_slot
                """),
                {
                    "id": str(uuid.uuid4()),
                    "slug": slug,
                    "display_name": payload.display_name,
                    "description": payload.description,
                    "repo_url": repo_url,
                    "branch": payload.default_branch,
                    "created_by": auth.get("user_id"),
                }
            ).mappings().fetchone()
            if auth.get("user_id"):
                conn.execute(
                    sa.text("""
                        INSERT INTO project_memberships
                            (id, project_id, user_id, approved_by_admin_id, allowed_skills, status)
                        VALUES
                            (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:uid AS UUID), CAST(:uid AS UUID),
                             CAST('[]' AS JSONB), 'APPROVED')
                        ON CONFLICT (project_id, user_id) DO NOTHING
                    """),
                    {"id": str(uuid.uuid4()), "pid": project["id"], "uid": auth["user_id"]}
                )
            worker = ensure_hermes_worker_session(
                conn, project["id"], set_online=False, initial_status="OFFLINE"
            )
            # Fallback determinista historico (Etapa 9): sesion de gobernanza
            # sin proceso LLM. No consume cupo de Hermes.
            headless_worker = ensure_headless_worker_session(conn, project["id"], False)
            job = enqueue_job(
                conn, project["id"], "RAG_BOOTSTRAP",
                {"reason": "project_created", "project_slug": slug},
                idempotency_key=f"project-created:{project['id']}",
                priority=20,
            )
    except LicenseError:
        shutil.rmtree(REPOS_BASE_DIR / slug, ignore_errors=True)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("DELETE FROM repo_file_map WHERE repo_name = :slug"), {"slug": slug})
        except Exception:
            pass
        raise
    if headless_worker:
        record_event(
            engine,
            "HEADLESS_WORKER_READY",
            {
                "session_id": headless_worker["session_id"],
                "project_id": project["id"],
                "project_slug": slug,
                "skills": ["tech-writer", "doc-analyzer", "rag-docs-curator"],
                "trigger": "project_created",
                "created": headless_worker.get("created"),
            },
            source_session_id=headless_worker["session_id"],
        )
    return {
        "project": dict(project),
        "index": index_result,
        "hermes_worker": {k: v for k, v in (worker or {}).items() if k != "session_key"},
        "job": job,
    }


@app.get("/api/v1/projects")
def list_projects(auth: Dict[str, Any] = Depends(verify_portal_or_admin)):
    is_admin = auth["role_name"] == "ADMIN"
    uid = auth.get("user_id")
    with engine.connect() as conn:
        if is_admin:
            projects = conn.execute(sa.text("""
                SELECT id::text, project_slug, display_name, description, repo_url, default_branch,
                       created_by_user_id::text, created_at, is_active_slot
                FROM harness_projects ORDER BY project_slug
            """)).mappings().all()
        else:
            projects = conn.execute(sa.text("""
                SELECT p.id::text, p.project_slug, p.display_name, p.description, p.repo_url,
                       p.default_branch, p.created_by_user_id::text, p.created_at,
                       p.is_active_slot,
                       m.allowed_skills, m.status AS membership_status
                FROM harness_projects p
                JOIN project_memberships m ON m.project_id = p.id
                WHERE m.user_id = CAST(:uid AS UUID) AND m.status = 'APPROVED'
                ORDER BY p.project_slug
            """), {"uid": uid}).mappings().all()

        result = []
        for project in projects:
            item = dict(project)
            worker = conn.execute(sa.text("""
                SELECT s.id::text, s.status, s.agent_tool, s.worker_role,
                       s.current_context_tokens, s.max_context_tokens, s.last_heartbeat
                FROM agent_sessions s
                WHERE s.project_id = CAST(:pid AS UUID)
                  AND s.session_kind = 'SYSTEM_WORKER'
                  AND s.agent_tool = 'hermes'
                  AND s.status <> 'REVOKED'
                ORDER BY s.connected_at DESC
                LIMIT 1
            """), {"pid": project["id"]}).mappings().fetchone()
            item["hermes_worker"] = dict(worker) if worker else None
            if is_admin:
                members = conn.execute(sa.text("""
                    SELECT m.user_id::text, u.email, u.full_name, m.status, m.allowed_skills,
                           m.approved_by_admin_id::text
                    FROM project_memberships m
                    JOIN harness_users u ON u.id = m.user_id
                    WHERE m.project_id = CAST(:pid AS UUID) AND m.status = 'APPROVED'
                    ORDER BY u.email
                """), {"pid": project["id"]}).mappings().all()
                sessions = conn.execute(sa.text("""
                    SELECT s.id::text, s.user_id::text, u.email, s.agent_tool, s.status,
                           s.current_context_tokens, s.max_context_tokens, s.last_heartbeat
                    FROM agent_sessions s
                    JOIN harness_users u ON u.id = s.user_id
                    WHERE s.project_id = CAST(:pid AS UUID) AND s.status IN ('ONLINE', 'BUSY')
                    ORDER BY u.email, s.agent_tool
                """), {"pid": project["id"]}).mappings().all()
            else:
                members = conn.execute(sa.text("""
                    SELECT m.user_id::text, u.email, u.full_name, m.status, m.allowed_skills,
                           m.approved_by_admin_id::text
                    FROM project_memberships m
                    JOIN harness_users u ON u.id = m.user_id
                    WHERE m.project_id = CAST(:pid AS UUID) AND m.user_id = CAST(:uid AS UUID)
                """), {"pid": project["id"], "uid": uid}).mappings().all()
                sessions = conn.execute(sa.text("""
                    SELECT s.id::text, s.user_id::text, u.email, s.agent_tool, s.status,
                           s.current_context_tokens, s.max_context_tokens, s.last_heartbeat
                    FROM agent_sessions s
                    JOIN harness_users u ON u.id = s.user_id
                    WHERE s.project_id = CAST(:pid AS UUID) AND s.user_id = CAST(:uid AS UUID)
                      AND s.status IN ('ONLINE', 'BUSY')
                    ORDER BY s.agent_tool
                """), {"pid": project["id"], "uid": uid}).mappings().all()
            item["members"] = [dict(x) for x in members]
            item["active_sessions"] = [dict(x) for x in sessions]
            result.append(item)
    return {"projects": result}


class ProjectSlotRequest(BaseModel):
    active: bool


class LicenseUpdateRequest(BaseModel):
    plan_code: str = Field(..., min_length=3, max_length=40)
    max_active_projects: Optional[int] = Field(default=None, ge=0)
    max_hermes_sessions: Optional[int] = Field(default=None, ge=0)
    max_engineers_per_project: Optional[int] = Field(default=None, ge=0)
    max_total_engineer_sessions: Optional[int] = Field(default=None, ge=0)
    max_registered_users: Optional[int] = Field(default=None, ge=0)
    max_parallel_rag_jobs: Optional[int] = Field(default=None, ge=1)


def _license_payload(conn, policy: Dict[str, Any]) -> Dict[str, Any]:
    data = license_response(conn, policy)
    signed = LICENSE_MANAGER.get_state(conn)
    data["signed_license"] = {
        "status": signed.get("status"),
        "license_id": signed.get("license_id"),
        "customer": signed.get("customer"),
        "plan_code": signed.get("plan_code"),
        "installation_id": signed.get("installation_id"),
        "allowed_plans": signed.get("allowed_plans") or [],
        "signed_limits": signed.get("signed_limits") or {},
        "features": signed.get("features") or [],
        "expires_at": signed.get("expires_at"),
        "grace_until": signed.get("grace_until"),
        "last_verified_at": signed.get("last_verified_at"),
        "error_message": signed.get("error_message"),
    }
    data["license_operational"] = LICENSE_MANAGER.is_operational()
    data["activation"] = ACTIVATION_MANAGER.get_state(conn)
    data["activation_operational"] = ACTIVATION_MANAGER.is_operational()
    data["audit_chain"] = LICENSE_MANAGER.verify_audit_chain(conn, limit=500)
    data["plans"] = LICENSE_TIERS
    data["recent_audit"] = [
        dict(row) for row in conn.execute(
            sa.text("""
                SELECT id, event_type, actor, payload, prev_hash, event_hash, created_at
                FROM license_audit_events
                ORDER BY id DESC
                LIMIT 15
            """)
        ).mappings().all()
    ]
    data["recent_jobs"] = [
        dict(row) for row in conn.execute(
            sa.text("""
                SELECT j.id::text, j.project_id::text, p.project_slug, j.job_type,
                       j.status, j.attempts, j.error_message, j.created_at,
                       j.started_at, j.completed_at
                FROM hermes_worker_jobs j
                JOIN harness_projects p ON p.id = j.project_id
                ORDER BY j.created_at DESC
                LIMIT 15
            """)
        ).mappings().all()
    ]
    return data


@app.post("/api/v1/projects/{project_id}/toggle-slot")
def toggle_project_slot(
    project_id: str,
    payload: ProjectSlotRequest,
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    assert_license_operational()
    worker = None
    job = None
    with engine.begin() as conn:
        policy = get_policy(conn, for_update=True)
        project = conn.execute(
            sa.text("""
                SELECT id::text, project_slug, display_name, is_active_slot
                FROM harness_projects
                WHERE id = CAST(:pid AS UUID)
                FOR UPDATE
            """),
            {"pid": project_id},
        ).mappings().fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if payload.active and not project["is_active_slot"]:
            assert_project_slot_available(conn, policy)
            existing = conn.execute(
                sa.text("""
                    SELECT id::text FROM agent_sessions
                    WHERE project_id = CAST(:pid AS UUID)
                      AND session_kind = 'SYSTEM_WORKER'
                      AND agent_tool = 'hermes'
                      AND status <> 'REVOKED'
                    LIMIT 1
                """),
                {"pid": project_id},
            ).fetchone()
            assert_hermes_slot_available(
                conn, policy,
                exclude_session_id=existing[0] if existing else None,
            )
            conn.execute(
                sa.text("UPDATE harness_projects SET is_active_slot = true WHERE id = CAST(:pid AS UUID)"),
                {"pid": project_id},
            )
            worker = ensure_hermes_worker_session(
                conn, project_id, set_online=False, initial_status="OFFLINE"
            )
            job = enqueue_job(
                conn, project_id, "RAG_REFRESH_PROJECT",
                {"reason": "slot_activated", "project_slug": project["project_slug"]},
                idempotency_key=None,
                priority=40,
            )
        elif not payload.active and project["is_active_slot"]:
            conn.execute(
                sa.text("UPDATE harness_projects SET is_active_slot = false WHERE id = CAST(:pid AS UUID)"),
                {"pid": project_id},
            )
            conn.execute(
                sa.text("""
                    UPDATE hermes_worker_jobs
                    SET status = 'CANCELLED',
                        completed_at = NOW(),
                        updated_at = NOW(),
                        error_message = COALESCE(error_message, 'project slot deactivated')
                    WHERE project_id = CAST(:pid AS UUID) AND status = 'QUEUED'
                """),
                {"pid": project_id},
            )
            deactivate_hermes_session(conn, project_id, "OFFLINE")
        data = _license_payload(conn, policy)
    record_event(
        engine,
        "PROJECT_SLOT_CHANGED",
        {
            "project_id": project_id,
            "project_slug": project["project_slug"],
            "active": bool(payload.active),
            "job_id": (job or {}).get("id"),
        },
    )
    return {
        "project": {
            "id": project_id,
            "project_slug": project["project_slug"],
            "display_name": project["display_name"],
            "is_active_slot": bool(payload.active),
        },
        "hermes_worker": {k: v for k, v in (worker or {}).items() if k != "session_key"},
        "job": job,
        "license": data,
    }


@app.get("/api/v1/admin/license")
def get_admin_license(auth: Dict[str, Any] = Depends(verify_portal_admin_or_license)):
    with engine.connect() as conn:
        policy = get_policy(conn)
        return _license_payload(conn, policy)


@app.put("/api/v1/admin/license")
def put_admin_license(
    payload: LicenseUpdateRequest,
    request: Request,
    auth: Dict[str, Any] = Depends(verify_license_admin),
):
    assert_license_operational()
    overrides = {
        field: getattr(payload, field)
        for field in QUOTA_FIELDS
        if getattr(payload, field, None) is not None
    }
    with engine.begin() as conn:
        get_policy(conn, for_update=True)
        policy = update_policy(conn, payload.plan_code, overrides)
        LICENSE_MANAGER.validate_plan_limits(
            policy["plan_code"],
            {field: policy[field] for field in QUOTA_FIELDS},
        )
        assert_downgrade_allowed(conn, policy)
        data = _license_payload(conn, policy)
    LICENSE_MANAGER.record_audit(
        "LICENSE_PLAN_CHANGED",
        auth.get("email") or "license-operator",
        {
            "plan_code": policy["plan_code"],
            "limits": {field: policy[field] for field in QUOTA_FIELDS},
        },
        context={"actor_ip": _client_ip(request)},
    )
    return data


@app.post("/api/v1/admin/license/reload")
async def reload_admin_license(
    request: Request,
    auth: Dict[str, Any] = Depends(verify_license_admin),
):
    actor = auth.get("email") or "license-operator"
    context = {"actor_ip": _client_ip(request)}
    await asyncio.to_thread(LICENSE_MANAGER.reload, actor, True, context)
    try:
        await asyncio.to_thread(ACTIVATION_MANAGER.activate, actor, True, context)
    except Exception as exc:
        print(f"[license] activation after reload failed: {exc}")
    with engine.connect() as conn:
        policy = get_policy(conn)
        data = _license_payload(conn, policy)
    return data


@app.post("/api/v1/admin/license/activate")
async def activate_admin_license(
    request: Request,
    auth: Dict[str, Any] = Depends(verify_license_admin),
):
    actor = auth.get("email") or "license-operator"
    context = {"actor_ip": _client_ip(request)}
    state = await asyncio.to_thread(ACTIVATION_MANAGER.activate, actor, True, context)
    with engine.connect() as conn:
        policy = get_policy(conn)
        data = _license_payload(conn, policy)
    return {"activation": state, "license": data}


@app.get("/api/v1/admin/license/audit")
def get_license_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    auth: Dict[str, Any] = Depends(verify_portal_admin_or_license),
):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT id, event_type, actor, payload, prev_hash, event_hash, created_at
                FROM license_audit_events
                ORDER BY id DESC
                LIMIT :limit
            """),
            {"limit": limit},
        ).mappings().all()
        chain = LICENSE_MANAGER.verify_audit_chain(conn, limit=500)
    return {
        "events": [dict(row) for row in rows],
        "chain": chain,
    }


@app.post("/api/v1/projects/{project_id}/members")
def add_project_member(project_id: str, payload: ProjectMemberRequest, background: BackgroundTasks, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    if bool(payload.user_id) == bool(payload.email):
        raise HTTPException(status_code=400, detail="Provide exactly one of user_id or email")
    initial_password = None
    invite_token = None
    with engine.begin() as conn:
        project = conn.execute(
            sa.text("SELECT id::text, project_slug FROM harness_projects WHERE id = CAST(:pid AS UUID) FOR UPDATE"),
            {"pid": project_id}
        ).mappings().fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if payload.user_id:
            user = conn.execute(
                sa.text("""
                    SELECT id::text, email, full_name, role, status, password_hash, invite_token, default_skills
                    FROM harness_users WHERE id = CAST(:uid AS UUID) FOR UPDATE
                """),
                {"uid": payload.user_id}
            ).mappings().fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
        else:
            email = payload.email.strip().lower()
            if not _valid_email(email):
                raise HTTPException(status_code=400, detail="Invalid email")
            user = conn.execute(
                sa.text("""
                    SELECT id::text, email, full_name, role, status, password_hash, invite_token, default_skills
                    FROM harness_users WHERE email = :email FOR UPDATE
                """),
                {"email": email}
            ).mappings().fetchone()
            if not user:
                full_name = payload.full_name or email.split("@")[0]
                if payload.password:
                    ph = hash_password(payload.password)
                    status = "ACTIVE"
                elif payload.invite_token:
                    invite_token = payload.invite_token
                    ph = hash_password(secrets.token_urlsafe(32))
                    status = "INVITED"
                else:
                    initial_password = secrets.token_urlsafe(12)
                    ph = hash_password(initial_password)
                    status = "ACTIVE"
                conn.execute(
                    sa.text("""
                        INSERT INTO harness_users
                            (id, email, full_name, password_hash, role, status, invite_token, default_skills)
                        VALUES
                            (CAST(:id AS UUID), :email, :name, :ph, 'ENGINEER', :status, :invite,
                             CAST('[]' AS JSONB))
                    """),
                    {
                        "id": str(uuid.uuid4()), "email": email, "name": full_name,
                        "ph": ph, "status": status, "invite": invite_token,
                    }
                )
                user = conn.execute(
                    sa.text("""
                        SELECT id::text, email, full_name, role, status, password_hash, invite_token, default_skills
                        FROM harness_users WHERE email = :email
                    """),
                    {"email": email}
                ).mappings().fetchone()

        if user["status"] == "DISABLED":
            raise HTTPException(status_code=403, detail="User disabled")
        if user["role"] == "LICENSE_ADMIN":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "LICENSE_ADMIN_NOT_PROJECT_MEMBER",
                    "message": "The license operator is not a project user",
                },
            )
        if payload.password:
            conn.execute(
                sa.text("UPDATE harness_users SET password_hash = :ph WHERE id = CAST(:uid AS UUID)"),
                {"ph": hash_password(payload.password), "uid": user["id"]}
            )
        if payload.full_name:
            conn.execute(
                sa.text("UPDATE harness_users SET full_name = :name WHERE id = CAST(:uid AS UUID)"),
                {"name": payload.full_name, "uid": user["id"]}
            )
        # PENDING: el admin aprueba y activa. INVITED: solo se activa cuando el
        # ingeniero define contraseña (o recibe una contraseña inicial explicita).
        if user["status"] == "PENDING" or (payload.password and user["status"] == "INVITED"):
            conn.execute(
                sa.text("""
                    UPDATE harness_users
                    SET status = 'ACTIVE', invite_token = NULL
                    WHERE id = CAST(:uid AS UUID)
                """),
                {"uid": user["id"]}
            )
        elif payload.invite_token and user["status"] == "INVITED":
            conn.execute(
                sa.text("UPDATE harness_users SET invite_token = :inv WHERE id = CAST(:uid AS UUID)"),
                {"inv": payload.invite_token, "uid": user["id"]}
            )

        membership = conn.execute(
            sa.text("""
                INSERT INTO project_memberships
                    (id, project_id, user_id, approved_by_admin_id, allowed_skills, status)
                VALUES
                    (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:uid AS UUID),
                     CAST(:approved_by AS UUID), CAST(:skills AS JSONB), 'APPROVED')
                ON CONFLICT (project_id, user_id) DO UPDATE SET
                    status = 'APPROVED',
                    allowed_skills = EXCLUDED.allowed_skills,
                    approved_by_admin_id = EXCLUDED.approved_by_admin_id
                RETURNING id::text, project_id::text, user_id::text, allowed_skills, status, created_at
            """),
            {
                "id": str(uuid.uuid4()), "pid": project_id, "uid": user["id"],
                "approved_by": auth.get("user_id"), "skills": json.dumps(payload.allowed_skills),
            }
        ).mappings().fetchone()
        _sync_membership_skills(
            conn, project_id, user["id"], payload.allowed_skills,
            auth.get("user_id"), replace=True,
        )
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, full_name, role, status, default_skills
                FROM harness_users WHERE id = CAST(:uid AS UUID)
            """),
            {"uid": user["id"]}
        ).mappings().fetchone()

    with engine.connect() as conn:
        approved_projects = conn.execute(
            sa.text("""
                SELECT p.project_slug, p.display_name, m.allowed_skills
                FROM project_memberships m
                JOIN harness_projects p ON p.id = m.project_id
                WHERE m.user_id = CAST(:uid AS UUID) AND m.status = 'APPROVED'
                ORDER BY p.project_slug
            """),
            {"uid": user["id"]}
        ).mappings().all()

    recipient = user["email"]
    subject = f"Acceso aprobado: {project['project_slug']} - Harness Control Plane"
    html_body = render_project_invite(
        f"{HARNESS_PUBLIC_URL}/portal",
        recipient,
        [dict(p) for p in approved_projects],
        initial_password=initial_password or payload.password,
        invite_token=invite_token,
    )
    background.add_task(
        send_transactional_email,
        engine,
        recipient,
        subject,
        html_body,
        "project_invite",
    )

    return {
        "user": _public_user(user),
        "membership": dict(membership),
        "initial_password": initial_password,
        "invite_token": invite_token,
        "email_queued": True,
        "email_recipient": recipient,
    }


@app.delete("/api/v1/projects/{project_id}/members/{user_id}")
async def remove_project_member(project_id: str, user_id: str, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    revoked_sessions = []
    with engine.begin() as conn:
        membership = conn.execute(
            sa.text("""
                SELECT id::text FROM project_memberships
                WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
                FOR UPDATE
            """),
            {"pid": project_id, "uid": user_id}
        ).fetchone()
        if not membership:
            raise HTTPException(status_code=404, detail="Membership not found")
        conn.execute(
            sa.text("""
                UPDATE project_memberships SET status = 'REVOKED'
                WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
            """),
            {"pid": project_id, "uid": user_id}
        )
        rows = conn.execute(
            sa.text("""
                SELECT id::text FROM agent_sessions
                WHERE user_id = CAST(:uid AS UUID) AND project_id = CAST(:pid AS UUID)
                  AND status IN ('ONLINE', 'BUSY')
            """),
            {"pid": project_id, "uid": user_id}
        ).fetchall()
        revoked_sessions = [r[0] for r in rows]
        for sid in revoked_sessions:
            cleanup_disconnected_sessions(conn, [sid], target_status="REVOKED")

    for sid in revoked_sessions:
        ws = active_connections.pop(sid, None)
        if ws:
            try:
                await ws.close(code=4003, reason="Project access revoked")
            except Exception:
                pass
        await emit_war("SESSION_REVOKED", {"session_id": sid, "reason": "membership_revoked"}, source_session_id=sid)

    return {"membership": "REVOKED", "revoked_sessions": revoked_sessions}


@app.post("/api/v1/portal/sessions")
async def create_portal_session(payload: PortalSessionRequest, auth: Dict[str, Any] = Depends(verify_portal)):
    assert_license_operational()
    uid = auth["user_id"]
    try:
        with engine.connect() as conn:
            profile = resolve_profile(conn, payload.model_profile, payload.max_context_tokens)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    max_tokens = int(profile["context_window_tokens"]) if payload.model_profile else int(payload.max_context_tokens)
    with engine.connect() as conn:
        membership = conn.execute(
            sa.text("""
                SELECT m.allowed_skills, m.status, p.id::text AS project_id, p.project_slug
                FROM project_memberships m
                JOIN harness_projects p ON p.id = m.project_id
                WHERE m.user_id = CAST(:uid AS UUID) AND m.project_id = CAST(:pid AS UUID)
            """),
            {"uid": uid, "pid": payload.project_id}
        ).mappings().fetchone()
    if not membership or membership["status"] != "APPROVED":
        raise HTTPException(status_code=403, detail="Project Access Not Approved by Administrator")

    with engine.connect() as conn:
        skill_rows = conn.execute(
            sa.text("""
                SELECT s.slug
                FROM membership_skills ms
                JOIN harness_skills s ON s.id = ms.skill_id
                WHERE ms.user_id = CAST(:uid AS UUID)
                  AND ms.project_id = CAST(:pid AS UUID)
                  AND s.status = 'PUBLISHED'
                ORDER BY s.slug
            """),
            {"uid": uid, "pid": payload.project_id}
        ).fetchall()
    skills = [row[0] for row in skill_rows] or membership["allowed_skills"] or auth["user"].get("default_skills") or []
    old_sessions = []
    session_id = str(uuid.uuid4())
    now = int(datetime.now(timezone.utc).timestamp())
    jwt_payload = {
        "sub": session_id,
        "user_id": uid,
        "project_id": payload.project_id,
        "project_slug": membership["project_slug"],
        "email": auth["email"],
        "tool": payload.agent_tool,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + SESSION_JWT_TTL_HOURS * 3600,
    }
    session_key = jwt.encode(jwt_payload, JWT_SECRET, algorithm="HS256")
    key_hash = hash_token(session_key)
    vault = seal_secret(session_key)

    with engine.begin() as conn:
        policy = get_policy(conn, for_update=True)
        rows = conn.execute(
            sa.text("""
                SELECT id::text FROM agent_sessions
                WHERE user_id = CAST(:uid AS UUID) AND project_id = CAST(:pid AS UUID)
                  AND agent_tool = :tool AND status IN ('ONLINE', 'BUSY')
            """),
            {"uid": uid, "pid": payload.project_id, "tool": payload.agent_tool}
        ).fetchall()
        old_sessions = [r[0] for r in rows]
        assert_engineer_quota(
            conn, policy, payload.project_id, uid,
            exclude_session_ids=old_sessions,
        )
        for sid in old_sessions:
            cleanup_disconnected_sessions(conn, [sid], target_status="REVOKED")
        conn.execute(
            sa.text("""
                INSERT INTO agent_sessions (
                    id, engineer_username, agent_tool, llm_provider, session_key_hash,
                    skills, status, max_context_tokens, current_context_tokens,
                    user_id, project_id, session_key_vault, session_kind, model_profile
                ) VALUES (
                    CAST(:id AS UUID), :user, :tool, :provider, :khash,
                    CAST(:skills AS JSONB), 'ONLINE', :max_tok, 0,
                    CAST(:uid AS UUID), CAST(:pid AS UUID), :vault, 'INTERACTIVE', :model_profile
                )
            """),
            {
                "id": session_id,
                "user": auth["email"],
                "tool": payload.agent_tool,
                "provider": payload.llm_provider,
                "khash": key_hash,
                "skills": json.dumps(skills),
                "max_tok": max_tokens,
                "uid": uid,
                "pid": payload.project_id,
                "vault": vault,
                "model_profile": profile["name"],
            }
        )

    for sid in old_sessions:
        ws = active_connections.pop(sid, None)
        if ws:
            try:
                await ws.close(code=4003, reason="Session replaced by portal rotation")
            except Exception:
                pass
        await emit_war("SESSION_REVOKED", {"session_id": sid, "reason": "portal_session_rotated"}, source_session_id=sid)

    await emit_war(
        "SESSION_REGISTERED",
        {
            "session_id": session_id,
            "user_id": uid,
            "project_id": payload.project_id,
            "project_slug": membership["project_slug"],
            "agent_tool": payload.agent_tool,
            "skills": skills,
        },
        source_session_id=session_id,
    )
    return {
        "session_id": session_id,
        "session_key": session_key,
        "status": "ONLINE",
        "project_id": payload.project_id,
        "project_slug": membership["project_slug"],
        "agent_tool": payload.agent_tool,
        "skills": skills,
        "max_context_tokens": max_tokens,
        "model_profile": profile["name"],
        "replaced_session_ids": old_sessions,
        "ws_url": f"/ws/agent/{session_id}",
    }


@app.get("/api/v1/portal/sessions/{session_id}/config-bundle")
def portal_session_config_bundle(session_id: str, auth: Dict[str, Any] = Depends(verify_portal_or_admin)):
    with engine.connect() as conn:
        session = conn.execute(
            sa.text("""
                SELECT s.id::text, s.user_id::text, s.agent_tool, s.status, s.session_key_vault,
                       p.project_slug
                FROM agent_sessions s
                JOIN harness_projects p ON p.id = s.project_id
                WHERE s.id = CAST(:sid AS UUID)
            """),
            {"sid": session_id}
        ).mappings().fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if auth["role_name"] != "ADMIN" and session["user_id"] != auth.get("user_id"):
        raise HTTPException(status_code=403, detail="Session belongs to another user")
    if session["status"] == "REVOKED":
        raise HTTPException(status_code=409, detail="Session revoked; create a new session")
    session_key = open_secret(session["session_key_vault"] or "")
    if not session_key:
        raise HTTPException(status_code=409, detail="Session key unavailable; recreate the session")

    env = {
        "HARNESS_URL": HARNESS_PUBLIC_URL,
        "HARNESS_SESSION_ID": session_id,
        "HARNESS_SESSION_KEY": session_key,
        "HARNESS_PROJECT_SLUG": session["project_slug"],
    }
    cursor = {"mcpServers": {"harness": {"command": "python", "args": ["harness_mcp_bridge.py"], "env": env}}}
    claude = {"mcpServers": {"harness": {"command": "python", "args": ["harness_mcp_bridge.py"], "env": env}}}
    opencode = {"mcp": {"servers": {"harness": {
        "type": "local",
        "command": ["python", "harness_mcp_bridge.py"],
        "environment": env,
    }}}}
    hermes = "\n".join(f"export {key}={value}" for key, value in env.items())
    download = f"curl -fsSL {HARNESS_PUBLIC_URL}/api/v1/client/bridge.py -o harness_mcp_bridge.py"

    return {
        "session_id": session_id,
        "project_slug": session["project_slug"],
        "agent_tool": session["agent_tool"],
        "env": env,
        "cursor_mcp_json": json.dumps(cursor, indent=2),
        "claudecode_mcp_json": json.dumps(claude, indent=2),
        "opencode_json": json.dumps(opencode, indent=2),
        "hermes_cli_env": hermes,
        "bridge_download_command": download,
    }


# ---------------------------------------------------------------------------
# Etapa 7 SMTP - configuracion global, test y auditoria de correos
# ---------------------------------------------------------------------------
class SmtpConfigRequest(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=120)
    from_email: str = Field(..., min_length=3, max_length=255)
    use_tls: bool = True
    use_ssl: bool = False
    test_recipient: Optional[str] = Field(default=None, max_length=255)


class SmtpTestRequest(BaseModel):
    recipient: Optional[str] = Field(default=None, max_length=255)


def _smtp_config_response(row) -> Dict[str, Any]:
    return {
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "password_set": bool(row["password"]),
        "from_email": row["from_email"],
        "use_tls": row["use_tls"],
        "use_ssl": row["use_ssl"],
        "updated_at": row["updated_at"],
    }


def _smtp_test_html() -> str:
    return f"""<!doctype html>
<html lang="es"><body style="font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;padding:24px;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:24px;">
    <div style="color:#0284c7;font-size:12px;font-weight:800;letter-spacing:.10em;text-transform:uppercase;">Harness Control Plane</div>
    <h1 style="font-size:21px;margin:8px 0 10px;">Correo de prueba SMTP</h1>
    <p style="margin:0 0 12px;">Si recibes este mensaje, la configuracion SMTP del servidor funciona correctamente.</p>
    <p style="margin:0;"><a href="{HARNESS_PUBLIC_URL}/portal" style="color:#0284c7;">{HARNESS_PUBLIC_URL}/portal</a></p>
  </div>
</body></html>"""


@app.get("/api/v1/admin/smtp-config")
def get_admin_smtp_config(auth: Dict[str, Any] = Depends(verify_portal_admin)):
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("""
                SELECT host, port, username, password, from_email, use_tls, use_ssl, updated_at
                FROM system_smtp_config WHERE id = 1
            """)
        ).mappings().fetchone()
    if not row:
        return {
            "host": "smtp.gmail.com", "port": 587, "username": None, "password_set": False,
            "from_email": "harness-bot@controlplane.local", "use_tls": True, "use_ssl": False,
            "updated_at": None,
        }
    return _smtp_config_response(row)


@app.put("/api/v1/admin/smtp-config")
async def update_admin_smtp_config(payload: SmtpConfigRequest, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    host = payload.host.strip()
    from_email = payload.from_email.strip().lower()
    recipient = (payload.test_recipient or "").strip().lower()
    if payload.use_tls and payload.use_ssl:
        raise HTTPException(status_code=400, detail="use_tls y use_ssl son mutuamente excluyentes")
    if not _valid_email(from_email):
        raise HTTPException(status_code=400, detail="from_email invalido")
    if recipient and not _valid_email(recipient):
        raise HTTPException(status_code=400, detail="test_recipient invalido")
    username = (payload.username or "").strip() or None
    incoming_password = payload.password

    with engine.begin() as conn:
        existing = conn.execute(
            sa.text("SELECT password FROM system_smtp_config WHERE id = 1 FOR UPDATE")
        ).fetchone()
        if incoming_password is None:
            stored_password = existing[0] if existing else None
        elif incoming_password == "":
            stored_password = None
        else:
            stored_password = "vault:" + seal_secret(incoming_password)

        row = conn.execute(
            sa.text("""
                INSERT INTO system_smtp_config
                    (id, host, port, username, password, from_email, use_tls, use_ssl, updated_at)
                VALUES
                    (1, :host, :port, :username, :password, :from_email, :use_tls, :use_ssl, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    host = EXCLUDED.host,
                    port = EXCLUDED.port,
                    username = EXCLUDED.username,
                    password = EXCLUDED.password,
                    from_email = EXCLUDED.from_email,
                    use_tls = EXCLUDED.use_tls,
                    use_ssl = EXCLUDED.use_ssl,
                    updated_at = NOW()
                RETURNING host, port, username, password, from_email, use_tls, use_ssl, updated_at
            """),
            {
                "host": host, "port": payload.port, "username": username,
                "password": stored_password, "from_email": from_email,
                "use_tls": payload.use_tls, "use_ssl": payload.use_ssl,
            }
        ).mappings().fetchone()

    test_sent = None
    if recipient:
        test_sent = await asyncio.to_thread(
            send_transactional_email, engine, recipient,
            "Harness Control Plane - Correo de prueba", _smtp_test_html(), "smtp_test",
        )
    response = _smtp_config_response(row)
    response["test_sent"] = test_sent
    return response


@app.post("/api/v1/admin/smtp-config/test")
async def test_admin_smtp_config(payload: SmtpTestRequest, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    recipient = (payload.recipient or auth.get("email") or "").strip().lower()
    if not recipient or not _valid_email(recipient):
        raise HTTPException(status_code=400, detail="recipient invalido")
    sent = await asyncio.to_thread(
        send_transactional_email, engine, recipient,
        "Harness Control Plane - Correo de prueba", _smtp_test_html(), "smtp_test",
    )
    return {"sent": sent, "recipient": recipient}


@app.get("/api/v1/admin/email-logs")
def list_admin_email_logs(
    limit: int = Query(default=50, ge=1, le=200),
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT id::text, recipient, subject, template_name, status,
                       error_message, sent_at
                FROM email_dispatch_logs
                ORDER BY sent_at DESC
                LIMIT :limit
            """),
            {"limit": limit}
        ).mappings().all()
    return {"logs": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Etapa 7 passwords - autogestion y reset administrativo con notificacion SMTP
# ---------------------------------------------------------------------------
class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class AdminPasswordResetRequest(BaseModel):
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    generate_random: bool = False


@app.put("/api/v1/auth/password")
async def change_own_password(
    payload: PasswordChangeRequest,
    auth: Dict[str, Any] = Depends(verify_portal),
):
    policy_error = _password_policy_error(payload.new_password)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)

    with engine.connect() as conn:
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, password_hash, status, role, password_change_allowed
                FROM harness_users WHERE id = CAST(:uid AS UUID)
            """),
            {"uid": auth["user_id"]}
        ).mappings().fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] == "LICENSE_ADMIN" or not user["password_change_allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PASSWORD_SEALED",
                "message": "This account password is sealed; vendor-side break-glass is required",
            },
        )
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if verify_password(payload.new_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="New password must differ from current password")

    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE harness_users SET password_hash = :ph WHERE id = CAST(:uid AS UUID)"),
            {"ph": hash_password(payload.new_password), "uid": user["id"]}
        )

    email_sent = await asyncio.to_thread(
        send_transactional_email,
        engine,
        user["email"],
        "Tu contrasena fue actualizada - Harness Control Plane",
        render_password_changed(f"{HARNESS_PUBLIC_URL}/portal", user["email"]),
        "password_changed",
    )
    return {
        "status": "PASSWORD_UPDATED",
        "user_id": user["id"],
        "email_sent": email_sent,
    }


@app.put("/api/v1/admin/users/{user_id}/reset-password")
async def admin_reset_user_password(
    user_id: str,
    payload: AdminPasswordResetRequest,
    request: Request,
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    with engine.connect() as conn:
        user = conn.execute(
            sa.text("""
                SELECT id::text, email, full_name, status, role, password_change_allowed
                FROM harness_users WHERE id = CAST(:uid AS UUID)
            """),
            {"uid": user_id}
        ).mappings().fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["status"] == "DISABLED":
        raise HTTPException(status_code=403, detail="User disabled")
    if user["role"] == "LICENSE_ADMIN" or not user["password_change_allowed"]:
        try:
            LICENSE_MANAGER.record_audit(
                "LICENSE_ADMIN_PASSWORD_RESET_BLOCKED",
                auth.get("email") or "admin",
                {"target_user_id": user["id"], "target_email": user["email"]},
                context={"actor_ip": _client_ip(request)},
            )
        except Exception as exc:
            print(f"[license] audit blocked reset failed: {exc}")
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PASSWORD_SEALED",
                "message": "License operator password is sealed; vendor-side break-glass is required",
            },
        )

    generated = False
    if payload.generate_random or not payload.new_password:
        temporary_password = _generate_temporary_password()
        generated = True
    else:
        policy_error = _password_policy_error(payload.new_password)
        if policy_error:
            raise HTTPException(status_code=400, detail=policy_error)
        temporary_password = payload.new_password

    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE harness_users SET password_hash = :ph WHERE id = CAST(:uid AS UUID)"),
            {"ph": hash_password(temporary_password), "uid": user["id"]}
        )

    email_sent = await asyncio.to_thread(
        send_transactional_email,
        engine,
        user["email"],
        "Contrasena restablecida por el Administrador - Harness Control Plane",
        render_admin_password_reset(
            f"{HARNESS_PUBLIC_URL}/portal",
            user["email"],
            temporary_password,
        ),
        "admin_password_reset",
    )
    return {
        "status": "PASSWORD_RESET",
        "user_id": user["id"],
        "email": user["email"],
        "generated": generated,
        "email_sent": email_sent,
    }


# ---------------------------------------------------------------------------
# Etapa 7 UX - alta de usuarios independiente de proyectos y asignacion posterior
# ---------------------------------------------------------------------------
class AdminUserCreateRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    full_name: str = Field(..., min_length=2, max_length=150)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    invite_token: Optional[str] = Field(default=None, max_length=128)


@app.get("/api/v1/admin/users")
def list_admin_users(
    search: Optional[str] = Query(default=None, max_length=200),
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    where = "WHERE u.role <> 'LICENSE_ADMIN'"
    params: Dict[str, Any] = {}
    if search and search.strip():
        where += " AND (u.email ILIKE :like OR u.full_name ILIKE :like)"
        params["like"] = f"%{search.strip()}%"
    with engine.connect() as conn:
        users = conn.execute(
            sa.text(f"""
                SELECT u.id::text, u.email, u.full_name, u.role, u.status, u.created_at,
                       u.password_change_allowed, u.mfa_enabled
                FROM harness_users u
                {where}
                ORDER BY u.email
            """),
            params
        ).mappings().all()
        memberships = conn.execute(
            sa.text("""
                SELECT m.user_id::text, m.project_id::text, p.project_slug,
                       p.display_name, m.status, m.allowed_skills
                FROM project_memberships m
                JOIN harness_projects p ON p.id = m.project_id
                ORDER BY p.project_slug
            """)
        ).mappings().all()
        sessions = conn.execute(
            sa.text("""
                SELECT user_id::text, count(*) AS active_sessions
                FROM agent_sessions
                WHERE user_id IS NOT NULL AND status IN ('ONLINE', 'BUSY')
                GROUP BY user_id
            """)
        ).mappings().all()
        capabilities = conn.execute(
            sa.text("""
                SELECT user_id::text, can_create_skills, can_clone_skills,
                       can_edit_own_skills, can_grant_skills
                FROM user_skill_capabilities
            """)
        ).mappings().all()

    memberships_by_user: Dict[str, List[Dict[str, Any]]] = {}
    for membership in memberships:
        memberships_by_user.setdefault(membership["user_id"], []).append(dict(membership))
    sessions_by_user = {row["user_id"]: row["active_sessions"] for row in sessions}
    capabilities_by_user = {
        row["user_id"]: {
            "can_create_skills": row["can_create_skills"],
            "can_clone_skills": row["can_clone_skills"],
            "can_edit_own_skills": row["can_edit_own_skills"],
            "can_grant_skills": row["can_grant_skills"],
        }
        for row in capabilities
    }

    result = []
    for user in users:
        item = dict(user)
        item["memberships"] = memberships_by_user.get(user["id"], [])
        item["active_sessions"] = sessions_by_user.get(user["id"], 0)
        item["skill_capabilities"] = capabilities_by_user.get(user["id"], {
            "can_create_skills": False,
            "can_clone_skills": False,
            "can_edit_own_skills": True,
            "can_grant_skills": False,
        })
        item["is_admin"] = user["role"] == "ADMIN"
        item["is_sealed"] = not bool(user["password_change_allowed"])
        result.append(item)
    return {"users": result}


@app.post("/api/v1/admin/users")
async def create_admin_user(
    payload: AdminUserCreateRequest,
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    assert_license_operational()
    email = payload.email.strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email")
    if payload.password and payload.invite_token:
        raise HTTPException(status_code=400, detail="Provide password or invite_token, not both")

    with engine.connect() as conn:
        existing = conn.execute(
            sa.text("SELECT id::text FROM harness_users WHERE email = :email"),
            {"email": email}
        ).fetchone()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="User already exists; assign projects from Usuarios y Accesos",
        )

    generated = False
    invite_token = payload.invite_token
    temporary_password = payload.password
    if payload.password:
        policy_error = _password_policy_error(payload.password)
        if policy_error:
            raise HTTPException(status_code=400, detail=policy_error)
        password_hash = hash_password(payload.password)
        status = "ACTIVE"
    elif payload.invite_token:
        password_hash = hash_password(secrets.token_urlsafe(32))
        status = "INVITED"
    else:
        temporary_password = _generate_temporary_password()
        generated = True
        password_hash = hash_password(temporary_password)
        status = "ACTIVE"

    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        policy = get_policy(conn, for_update=True)
        assert_registered_user_quota(conn, policy)
        user = conn.execute(
            sa.text("""
                INSERT INTO harness_users
                    (id, email, full_name, password_hash, role, status, invite_token, default_skills)
                VALUES
                    (CAST(:id AS UUID), :email, :name, :ph, 'ENGINEER', :status, :invite,
                     CAST('[]' AS JSONB))
                RETURNING id::text, email, full_name, role, status, created_at
            """),
            {
                "id": user_id,
                "email": email,
                "name": payload.full_name.strip(),
                "ph": password_hash,
                "status": status,
                "invite": invite_token,
            }
        ).mappings().fetchone()

    email_sent = await asyncio.to_thread(
        send_transactional_email,
        engine,
        email,
        "Tu cuenta Harness fue creada - Harness Control Plane",
        render_user_welcome(
            f"{HARNESS_PUBLIC_URL}/portal",
            email,
            temporary_password=None if invite_token else temporary_password,
            invite_token=invite_token,
        ),
        "user_welcome",
    )
    return {
        "user": dict(user),
        "generated": generated,
        "email_sent": email_sent,
    }


# ---------------------------------------------------------------------------
# Etapa 8 - Catalogo estandar de skills, permisos y revelacion progresiva
# ---------------------------------------------------------------------------
class SkillDraftRequest(BaseModel):
    raw_idea: str = Field(..., min_length=10, max_length=2000)
    category: str = Field(default="general", max_length=50)


class SkillWriteRequest(BaseModel):
    slug: Optional[str] = Field(default=None, max_length=100)
    display_name: str = Field(..., min_length=2, max_length=150)
    category: str = Field(..., max_length=50)
    trigger_description: str = Field(..., min_length=10, max_length=300)
    objective: str = Field(..., min_length=10, max_length=1000)
    workflow_steps: List[str] = Field(default_factory=list)
    guardrails_forbidden: List[str] = Field(default_factory=list)
    output_criteria: str = Field(..., min_length=10, max_length=1000)
    is_official: bool = False
    visibility: Optional[str] = Field(default=None, max_length=20)
    status: str = Field(default="PUBLISHED", max_length=20)


class SkillCapabilitiesRequest(BaseModel):
    can_create_skills: bool = False
    can_clone_skills: bool = False
    can_edit_own_skills: bool = True
    can_grant_skills: bool = False


class SkillPermissionRequest(BaseModel):
    user_id: str
    can_use: bool = True
    can_edit: bool = False
    can_clone: bool = False
    can_grant: bool = False


class SkillAssignRequest(BaseModel):
    skill_slugs: List[str] = Field(default_factory=list)


def _skill_row(conn, slug: str):
    return conn.execute(
        sa.text("""
            SELECT id::text, slug, display_name, category, trigger_description,
                   objective, workflow_steps, guardrails_forbidden, output_criteria,
                   compiled_skill_md, created_by_user_id::text AS created_by_user_id,
                   owner_user_id::text AS owner_user_id, is_official, status,
                   visibility, version, compiled_tokens, created_at, updated_at
            FROM harness_skills
            WHERE slug = :slug
        """),
        {"slug": slug}
    ).mappings().fetchone()


def _skill_full_response(conn, skill, auth: Dict[str, Any]) -> Dict[str, Any]:
    response = _skill_summary(conn, skill, auth)
    response.update({
        "objective": skill["objective"],
        "workflow_steps": skill["workflow_steps"] or [],
        "guardrails_forbidden": skill["guardrails_forbidden"] or [],
        "output_criteria": skill["output_criteria"],
        "compiled_skill_md": skill["compiled_skill_md"],
        "created_by_user_id": skill["created_by_user_id"],
        "created_at": skill["created_at"],
    })
    return response


def _validate_skill_write(payload: SkillWriteRequest) -> Optional[str]:
    if payload.status not in ("DRAFT", "PUBLISHED"):
        return "status debe ser DRAFT o PUBLISHED"
    return validate_skill_fields(
        payload.display_name, payload.category, payload.trigger_description,
        payload.objective, payload.workflow_steps, payload.guardrails_forbidden,
        payload.output_criteria,
    )


@app.get("/api/v1/skills")
def list_skills(
    category: Optional[str] = Query(default=None, max_length=50),
    search: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None, max_length=20),
    auth: Dict[str, Any] = Depends(verify_portal_or_admin),
):
    is_admin = auth.get("role_name") == "ADMIN"
    clauses = ["s.status <> 'ARCHIVED'"]
    params: Dict[str, Any] = {}
    if category and category != "all":
        if category not in SKILL_CATEGORIES:
            raise HTTPException(status_code=400, detail="category invalida")
        clauses.append("s.category = :category")
        params["category"] = category
    if search and search.strip():
        clauses.append("(s.display_name ILIKE :like OR s.slug ILIKE :like OR s.trigger_description ILIKE :like)")
        params["like"] = f"%{search.strip()}%"
    if status and status != "all":
        if status not in ("DRAFT", "PUBLISHED", "ARCHIVED"):
            raise HTTPException(status_code=400, detail="status invalido")
        clauses = [c for c in clauses if c != "s.status <> 'ARCHIVED'"]
        clauses.append("s.status = :status")
        params["status"] = status
    if not is_admin:
        uid = auth.get("user_id")
        if not uid:
            raise HTTPException(status_code=403, detail="Portal user required")
        clauses.append("""
            (
                s.is_official = true
                OR s.visibility = 'SHARED'
                OR s.owner_user_id = CAST(:uid AS UUID)
                OR EXISTS (
                    SELECT 1 FROM skill_permissions p
                    WHERE p.skill_id = s.id AND p.user_id = CAST(:uid AS UUID)
                      AND (p.can_use = true OR p.can_edit = true OR p.can_clone = true OR p.can_grant = true)
                )
                OR EXISTS (
                    SELECT 1 FROM membership_skills ms
                    WHERE ms.skill_id = s.id AND ms.user_id = CAST(:uid AS UUID)
                )
            )
        """)
        params["uid"] = uid
    where = " AND ".join(clauses)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(f"""
                SELECT s.id::text, s.slug, s.display_name, s.category, s.trigger_description,
                       s.objective, s.workflow_steps, s.guardrails_forbidden, s.output_criteria,
                       s.compiled_skill_md, s.created_by_user_id::text AS created_by_user_id,
                       s.owner_user_id::text AS owner_user_id, s.is_official, s.status,
                       s.visibility, s.version, s.compiled_tokens, s.created_at, s.updated_at
                FROM harness_skills s
                WHERE {where}
                ORDER BY s.is_official DESC, s.category ASC, s.display_name ASC
            """),
            params
        ).mappings().all()
        skills = [_skill_summary(conn, row, auth) for row in rows]
        if is_admin:
            viewer_caps = {
                "can_create_skills": True,
                "can_clone_skills": True,
                "can_edit_own_skills": True,
                "can_grant_skills": True,
            }
        else:
            viewer_caps = _skill_capabilities(conn, auth.get("user_id"))
    return {
        "skills": skills,
        "categories": SKILL_CATEGORIES,
        "viewer_capabilities": viewer_caps,
        "can_create": viewer_caps["can_create_skills"],
    }


@app.get("/api/v1/skills/{slug}")
def get_skill(slug: str, auth: Dict[str, Any] = Depends(verify_portal_or_admin)):
    with engine.connect() as conn:
        skill = _skill_row(conn, slug)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not _can_view_skill(conn, skill, auth):
            raise HTTPException(status_code=403, detail="Skill not authorized")
        return _skill_full_response(conn, skill, auth)


@app.get("/api/v1/skills/{slug}/manual")
def get_skill_manual(slug: str, auth: Dict[str, Any] = Depends(verify_auth)):
    with engine.connect() as conn:
        skill = _skill_row(conn, slug)
        if not skill or skill["status"] != "PUBLISHED":
            raise HTTPException(status_code=404, detail="Skill not found")
        if auth.get("role") != "admin":
            allowed = False
            if auth.get("user_id") and auth.get("project_id"):
                allowed = bool(conn.execute(
                    sa.text("""
                        SELECT 1 FROM membership_skills
                        WHERE skill_id = CAST(:sid AS UUID)
                          AND user_id = CAST(:uid AS UUID)
                          AND project_id = CAST(:pid AS UUID)
                        LIMIT 1
                    """),
                    {"sid": skill["id"], "uid": auth["user_id"], "pid": auth["project_id"]}
                ).fetchone())
            if not allowed and auth.get("session_id"):
                session = conn.execute(
                    sa.text("""
                        SELECT skills FROM agent_sessions WHERE id = CAST(:sid AS UUID)
                    """),
                    {"sid": auth["session_id"]}
                ).fetchone()
                if session and slug in (session[0] or []):
                    allowed = True
            if not allowed:
                raise HTTPException(status_code=403, detail="Skill not assigned to this session")
        return {
            "slug": skill["slug"],
            "display_name": skill["display_name"],
            "category": skill["category"],
            "trigger_description": skill["trigger_description"],
            "compiled_skill_md": skill["compiled_skill_md"],
            "compiled_tokens": skill["compiled_tokens"],
        }


@app.post("/api/v1/skills/preview")
def preview_skill(payload: SkillWriteRequest, auth: Dict[str, Any] = Depends(verify_portal)):
    error = _validate_skill_write(payload)
    if error:
        raise HTTPException(status_code=400, detail=error)
    compiled = compile_standard_skill_md(
        slugify(payload.slug or payload.display_name, 100),
        payload.display_name, payload.category, payload.trigger_description,
        payload.objective, payload.workflow_steps, payload.guardrails_forbidden,
        payload.output_criteria,
    )
    return {
        "compiled_skill_md": compiled,
        "compiled_tokens": estimate_tokens(compiled),
        "valid": True,
    }


@app.post("/api/v1/skills/draft-from-idea")
def draft_skill_from_idea(payload: SkillDraftRequest, auth: Dict[str, Any] = Depends(verify_portal)):
    category = payload.category if payload.category in SKILL_CATEGORIES else "general"
    draft = draft_skill(payload.raw_idea, category)
    with engine.connect() as conn:
        unique_slug = _unique_skill_slug(conn, draft["slug"])
    if unique_slug != draft["slug"]:
        draft["slug"] = unique_slug
        draft["compiled_skill_md"] = compile_standard_skill_md(
            unique_slug, draft["display_name"], draft["category"],
            draft["trigger_description"], draft["objective"],
            draft["workflow_steps"], draft["guardrails_forbidden"],
            draft["output_criteria"],
        )
        draft["compiled_tokens"] = estimate_tokens(draft["compiled_skill_md"])
    return draft


@app.post("/api/v1/skills")
def create_skill(payload: SkillWriteRequest, auth: Dict[str, Any] = Depends(verify_portal)):
    is_admin = auth.get("role_name") == "ADMIN"
    uid = auth.get("user_id")
    error = _validate_skill_write(payload)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if payload.is_official and not is_admin:
        raise HTTPException(status_code=403, detail="Only Admin can create official skills")
    with engine.begin() as conn:
        if not is_admin:
            caps = _skill_capabilities(conn, uid)
            if not caps["can_create_skills"]:
                raise HTTPException(status_code=403, detail="Skill creation not authorized")
        if payload.slug:
            slug = slugify(payload.slug, 100)
            if conn.execute(sa.text("SELECT 1 FROM harness_skills WHERE slug = :slug"), {"slug": slug}).fetchone():
                raise HTTPException(status_code=409, detail="Skill slug already exists")
        else:
            slug = _unique_skill_slug(conn, payload.display_name)
        compiled = compile_standard_skill_md(
            slug, payload.display_name, payload.category, payload.trigger_description,
            payload.objective, payload.workflow_steps, payload.guardrails_forbidden,
            payload.output_criteria,
        )
        visibility = "OFFICIAL" if payload.is_official else (
            payload.visibility if is_admin and payload.visibility in ("PRIVATE", "SHARED") else "PRIVATE"
        )
        owner_id = None if payload.is_official else uid
        row = conn.execute(
            sa.text("""
                INSERT INTO harness_skills
                    (id, slug, display_name, category, trigger_description, objective,
                     workflow_steps, guardrails_forbidden, output_criteria, compiled_skill_md,
                     created_by_user_id, owner_user_id, is_official, status, visibility,
                     version, compiled_tokens)
                VALUES
                    (CAST(:id AS UUID), :slug, :display_name, :category, :trigger, :objective,
                     CAST(:steps AS JSONB), CAST(:guardrails AS JSONB), :output, :compiled,
                     CAST(:created_by AS UUID), CAST(:owner AS UUID), :official, :status,
                     :visibility, 1, :tokens)
                RETURNING id::text, slug, display_name, category, trigger_description,
                          objective, workflow_steps, guardrails_forbidden, output_criteria,
                          compiled_skill_md, created_by_user_id::text AS created_by_user_id,
                          owner_user_id::text AS owner_user_id, is_official, status,
                          visibility, version, compiled_tokens, created_at, updated_at
            """),
            {
                "id": str(uuid.uuid4()), "slug": slug, "display_name": payload.display_name,
                "category": payload.category, "trigger": payload.trigger_description,
                "objective": payload.objective, "steps": json.dumps(payload.workflow_steps),
                "guardrails": json.dumps(payload.guardrails_forbidden),
                "output": payload.output_criteria, "compiled": compiled,
                "created_by": uid, "owner": owner_id, "official": payload.is_official,
                "status": payload.status, "visibility": visibility,
                "tokens": estimate_tokens(compiled),
            }
        ).mappings().fetchone()
        if owner_id:
            conn.execute(
                sa.text("""
                    INSERT INTO skill_permissions
                        (id, skill_id, user_id, can_use, can_edit, can_clone, can_grant, granted_by_user_id)
                    VALUES
                        (CAST(:id AS UUID), CAST(:sid AS UUID), CAST(:uid AS UUID),
                         true, true, true, true, CAST(:uid AS UUID))
                    ON CONFLICT (skill_id, user_id) DO UPDATE SET
                        can_use = true, can_edit = true, can_clone = true, can_grant = true
                """),
                {"id": str(uuid.uuid4()), "sid": row["id"], "uid": owner_id}
            )
        return _skill_full_response(conn, row, auth)


@app.put("/api/v1/skills/{slug}")
def update_skill(slug: str, payload: SkillWriteRequest, auth: Dict[str, Any] = Depends(verify_portal)):
    error = _validate_skill_write(payload)
    if error:
        raise HTTPException(status_code=400, detail=error)
    with engine.begin() as conn:
        skill = _skill_row(conn, slug)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not _skill_flags(conn, skill, auth)["can_edit"]:
            raise HTTPException(status_code=403, detail="Skill edit not authorized")
        compiled = compile_standard_skill_md(
            slug, payload.display_name, payload.category, payload.trigger_description,
            payload.objective, payload.workflow_steps, payload.guardrails_forbidden,
            payload.output_criteria,
        )
        row = conn.execute(
            sa.text("""
                UPDATE harness_skills SET
                    display_name = :display_name,
                    category = :category,
                    trigger_description = :trigger,
                    objective = :objective,
                    workflow_steps = CAST(:steps AS JSONB),
                    guardrails_forbidden = CAST(:guardrails AS JSONB),
                    output_criteria = :output,
                    compiled_skill_md = :compiled,
                    compiled_tokens = :tokens,
                    version = version + 1,
                    updated_at = NOW()
                WHERE slug = :slug
                RETURNING id::text, slug, display_name, category, trigger_description,
                          objective, workflow_steps, guardrails_forbidden, output_criteria,
                          compiled_skill_md, created_by_user_id::text AS created_by_user_id,
                          owner_user_id::text AS owner_user_id, is_official, status,
                          visibility, version, compiled_tokens, created_at, updated_at
            """),
            {
                "display_name": payload.display_name, "category": payload.category,
                "trigger": payload.trigger_description, "objective": payload.objective,
                "steps": json.dumps(payload.workflow_steps),
                "guardrails": json.dumps(payload.guardrails_forbidden),
                "output": payload.output_criteria, "compiled": compiled,
                "tokens": estimate_tokens(compiled), "slug": slug,
            }
        ).mappings().fetchone()
        return _skill_full_response(conn, row, auth)


@app.post("/api/v1/skills/{slug}/clone")
def clone_skill(slug: str, auth: Dict[str, Any] = Depends(verify_portal)):
    uid = auth.get("user_id")
    with engine.begin() as conn:
        skill = _skill_row(conn, slug)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not _skill_flags(conn, skill, auth)["can_clone"]:
            raise HTTPException(status_code=403, detail="Skill clone not authorized")
        if not uid:
            raise HTTPException(status_code=401, detail="Portal user required")
        new_slug = _unique_skill_slug(conn, f"{slug}-copy")
        display = f"Copia de {skill['display_name']}"[:150]
        compiled = compile_standard_skill_md(
            new_slug, display, skill["category"], skill["trigger_description"],
            skill["objective"], skill["workflow_steps"] or [],
            skill["guardrails_forbidden"] or [], skill["output_criteria"],
        )
        row = conn.execute(
            sa.text("""
                INSERT INTO harness_skills
                    (id, slug, display_name, category, trigger_description, objective,
                     workflow_steps, guardrails_forbidden, output_criteria, compiled_skill_md,
                     created_by_user_id, owner_user_id, is_official, status, visibility,
                     version, compiled_tokens)
                VALUES
                    (CAST(:id AS UUID), :slug, :display_name, :category, :trigger, :objective,
                     CAST(:steps AS JSONB), CAST(:guardrails AS JSONB), :output, :compiled,
                     CAST(:uid AS UUID), CAST(:uid AS UUID), false, 'PUBLISHED', 'PRIVATE',
                     1, :tokens)
                RETURNING id::text, slug, display_name, category, trigger_description,
                          objective, workflow_steps, guardrails_forbidden, output_criteria,
                          compiled_skill_md, created_by_user_id::text AS created_by_user_id,
                          owner_user_id::text AS owner_user_id, is_official, status,
                          visibility, version, compiled_tokens, created_at, updated_at
            """),
            {
                "id": str(uuid.uuid4()), "slug": new_slug, "display_name": display,
                "category": skill["category"], "trigger": skill["trigger_description"],
                "objective": skill["objective"], "steps": json.dumps(skill["workflow_steps"] or []),
                "guardrails": json.dumps(skill["guardrails_forbidden"] or []),
                "output": skill["output_criteria"], "compiled": compiled,
                "uid": uid, "tokens": estimate_tokens(compiled),
            }
        ).mappings().fetchone()
        conn.execute(
            sa.text("""
                INSERT INTO skill_permissions
                    (id, skill_id, user_id, can_use, can_edit, can_clone, can_grant, granted_by_user_id)
                VALUES
                    (CAST(:id AS UUID), CAST(:sid AS UUID), CAST(:uid AS UUID),
                     true, true, true, true, CAST(:uid AS UUID))
                ON CONFLICT (skill_id, user_id) DO NOTHING
            """),
            {"id": str(uuid.uuid4()), "sid": row["id"], "uid": uid}
        )
        return _skill_full_response(conn, row, auth)


@app.get("/api/v1/skills/{slug}/permissions")
def list_skill_permissions(slug: str, auth: Dict[str, Any] = Depends(verify_portal)):
    with engine.connect() as conn:
        skill = _skill_row(conn, slug)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not _skill_flags(conn, skill, auth)["can_grant"]:
            raise HTTPException(status_code=403, detail="Skill grant not authorized")
        rows = conn.execute(
            sa.text("""
                SELECT p.user_id::text, u.email, u.full_name, p.can_use, p.can_edit,
                       p.can_clone, p.can_grant
                FROM skill_permissions p
                JOIN harness_users u ON u.id = p.user_id
                WHERE p.skill_id = CAST(:sid AS UUID)
                ORDER BY u.email
            """),
            {"sid": skill["id"]}
        ).mappings().all()
    return {"permissions": [dict(row) for row in rows]}


@app.put("/api/v1/skills/{slug}/permissions")
def set_skill_permission(slug: str, payload: SkillPermissionRequest, auth: Dict[str, Any] = Depends(verify_portal)):
    with engine.begin() as conn:
        skill = _skill_row(conn, slug)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not _skill_flags(conn, skill, auth)["can_grant"]:
            raise HTTPException(status_code=403, detail="Skill grant not authorized")
        user = conn.execute(
            sa.text("SELECT id::text, email FROM harness_users WHERE id = CAST(:uid AS UUID)"),
            {"uid": payload.user_id}
        ).mappings().fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            sa.text("""
                INSERT INTO skill_permissions
                    (id, skill_id, user_id, can_use, can_edit, can_clone, can_grant, granted_by_user_id)
                VALUES
                    (CAST(:id AS UUID), CAST(:sid AS UUID), CAST(:uid AS UUID),
                     :can_use, :can_edit, :can_clone, :can_grant, CAST(:by AS UUID))
                ON CONFLICT (skill_id, user_id) DO UPDATE SET
                    can_use = EXCLUDED.can_use,
                    can_edit = EXCLUDED.can_edit,
                    can_clone = EXCLUDED.can_clone,
                    can_grant = EXCLUDED.can_grant,
                    granted_by_user_id = EXCLUDED.granted_by_user_id
            """),
            {
                "id": str(uuid.uuid4()), "sid": skill["id"], "uid": payload.user_id,
                "can_use": payload.can_use, "can_edit": payload.can_edit,
                "can_clone": payload.can_clone, "can_grant": payload.can_grant,
                "by": auth.get("user_id"),
            }
        )
    return {"skill": slug, "user": user["email"], "updated": True}


@app.delete("/api/v1/skills/{slug}/permissions/{user_id}")
def delete_skill_permission(slug: str, user_id: str, auth: Dict[str, Any] = Depends(verify_portal)):
    with engine.begin() as conn:
        skill = _skill_row(conn, slug)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not _skill_flags(conn, skill, auth)["can_grant"]:
            raise HTTPException(status_code=403, detail="Skill grant not authorized")
        conn.execute(
            sa.text("""
                DELETE FROM skill_permissions
                WHERE skill_id = CAST(:sid AS UUID) AND user_id = CAST(:uid AS UUID)
            """),
            {"sid": skill["id"], "uid": user_id}
        )
    return {"skill": slug, "user_id": user_id, "deleted": True}


@app.get("/api/v1/admin/users/{user_id}/skill-capabilities")
def get_user_skill_capabilities(user_id: str, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    with engine.connect() as conn:
        user = conn.execute(
            sa.text("SELECT id::text, email, role FROM harness_users WHERE id = CAST(:uid AS UUID)"),
            {"uid": user_id}
        ).mappings().fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        caps = _skill_capabilities(conn, user_id)
    return {"user": dict(user), "capabilities": caps, "admin_bypass": user["role"] == "ADMIN"}


@app.put("/api/v1/admin/users/{user_id}/skill-capabilities")
def set_user_skill_capabilities(
    user_id: str,
    payload: SkillCapabilitiesRequest,
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    with engine.begin() as conn:
        user = conn.execute(
            sa.text("SELECT id::text, email, role FROM harness_users WHERE id = CAST(:uid AS UUID)"),
            {"uid": user_id}
        ).mappings().fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        row = conn.execute(
            sa.text("""
                INSERT INTO user_skill_capabilities
                    (id, user_id, can_create_skills, can_clone_skills,
                     can_edit_own_skills, can_grant_skills)
                VALUES
                    (CAST(:id AS UUID), CAST(:uid AS UUID), :create, :clone, :edit_own, :grant)
                ON CONFLICT (user_id) DO UPDATE SET
                    can_create_skills = EXCLUDED.can_create_skills,
                    can_clone_skills = EXCLUDED.can_clone_skills,
                    can_edit_own_skills = EXCLUDED.can_edit_own_skills,
                    can_grant_skills = EXCLUDED.can_grant_skills,
                    updated_at = NOW()
                RETURNING can_create_skills, can_clone_skills,
                          can_edit_own_skills, can_grant_skills
            """),
            {
                "id": str(uuid.uuid4()), "uid": user_id,
                "create": payload.can_create_skills,
                "clone": payload.can_clone_skills,
                "edit_own": payload.can_edit_own_skills,
                "grant": payload.can_grant_skills,
            }
        ).mappings().fetchone()
    return {"user": dict(user), "capabilities": dict(row), "admin_bypass": user["role"] == "ADMIN"}


@app.get("/api/v1/projects/{project_id}/skills")
def list_project_skills(project_id: str, auth: Dict[str, Any] = Depends(verify_portal_or_admin)):
    with engine.connect() as conn:
        project = conn.execute(
            sa.text("SELECT id::text, project_slug FROM harness_projects WHERE id = CAST(:pid AS UUID)"),
            {"pid": project_id}
        ).mappings().fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if auth.get("role_name") != "ADMIN":
            member = conn.execute(
                sa.text("""
                    SELECT 1 FROM project_memberships
                    WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
                      AND status = 'APPROVED'
                """),
                {"pid": project_id, "uid": auth.get("user_id")}
            ).fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Project access not approved")
        rows = conn.execute(
            sa.text("""
                SELECT s.id::text, s.slug, s.display_name, s.category,
                       s.trigger_description, s.is_official, s.compiled_tokens
                FROM project_skills ps
                JOIN harness_skills s ON s.id = ps.skill_id
                WHERE ps.project_id = CAST(:pid AS UUID)
                ORDER BY s.category, s.display_name
            """),
            {"pid": project_id}
        ).mappings().all()
    return {"project": dict(project), "skills": [dict(row) for row in rows]}


@app.put("/api/v1/projects/{project_id}/skills")
def set_project_skills(project_id: str, payload: SkillAssignRequest, auth: Dict[str, Any] = Depends(verify_portal_admin)):
    with engine.begin() as conn:
        project = conn.execute(
            sa.text("SELECT id::text, project_slug FROM harness_projects WHERE id = CAST(:pid AS UUID)"),
            {"pid": project_id}
        ).mappings().fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        slugs = list(dict.fromkeys(slugify(s, 100) for s in payload.skill_slugs if str(s).strip()))
        skill_ids = {}
        for slug in slugs:
            row = conn.execute(
                sa.text("SELECT id::text FROM harness_skills WHERE slug = :slug"),
                {"slug": slug}
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail=f"Skill not found: {slug}")
            skill_ids[slug] = row[0]
        conn.execute(
            sa.text("DELETE FROM project_skills WHERE project_id = CAST(:pid AS UUID)"),
            {"pid": project_id}
        )
        for slug, skill_id in skill_ids.items():
            conn.execute(
                sa.text("""
                    INSERT INTO project_skills (id, project_id, skill_id, assigned_by_user_id)
                    VALUES (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:sid AS UUID),
                            CAST(:by AS UUID))
                """),
                {"id": str(uuid.uuid4()), "pid": project_id, "sid": skill_id, "by": auth.get("user_id")}
            )
        conn.execute(
            sa.text("""
                DELETE FROM membership_skills
                WHERE project_id = CAST(:pid AS UUID)
                  AND skill_id NOT IN (
                      SELECT skill_id FROM project_skills WHERE project_id = CAST(:pid AS UUID)
                  )
            """),
            {"pid": project_id}
        )
        conn.execute(
            sa.text("""
                UPDATE project_memberships m
                SET allowed_skills = COALESCE((
                    SELECT jsonb_agg(s.slug ORDER BY s.slug)
                    FROM membership_skills ms
                    JOIN harness_skills s ON s.id = ms.skill_id
                    WHERE ms.project_id = m.project_id AND ms.user_id = m.user_id
                ), '[]'::jsonb)
                WHERE m.project_id = CAST(:pid AS UUID)
            """),
            {"pid": project_id}
        )
    return {"project": dict(project), "skill_slugs": slugs}


@app.get("/api/v1/projects/{project_id}/members/{user_id}/skills")
def list_membership_skills(
    project_id: str,
    user_id: str,
    auth: Dict[str, Any] = Depends(verify_portal_or_admin),
):
    if auth.get("role_name") != "ADMIN" and auth.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Skill assignment not authorized")
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT s.id::text, s.slug, s.display_name, s.category,
                       s.trigger_description, s.is_official, s.compiled_tokens
                FROM membership_skills ms
                JOIN harness_skills s ON s.id = ms.skill_id
                WHERE ms.project_id = CAST(:pid AS UUID) AND ms.user_id = CAST(:uid AS UUID)
                ORDER BY s.category, s.display_name
            """),
            {"pid": project_id, "uid": user_id}
        ).mappings().all()
    return {"skills": [dict(row) for row in rows]}


@app.put("/api/v1/projects/{project_id}/members/{user_id}/skills")
def set_membership_skills(
    project_id: str,
    user_id: str,
    payload: SkillAssignRequest,
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    with engine.begin() as conn:
        membership = conn.execute(
            sa.text("""
                SELECT 1 FROM project_memberships
                WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
            """),
            {"pid": project_id, "uid": user_id}
        ).fetchone()
        if not membership:
            raise HTTPException(status_code=404, detail="Membership not found")
        slugs = list(dict.fromkeys(slugify(s, 100) for s in payload.skill_slugs if str(s).strip()))
        for slug in slugs:
            row = conn.execute(
                sa.text("SELECT 1 FROM harness_skills WHERE slug = :slug"),
                {"slug": slug}
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail=f"Skill not found: {slug}")
        result = _sync_membership_skills(
            conn, project_id, user_id, slugs, auth.get("user_id"), replace=True
        )
    return {"project_id": project_id, "user_id": user_id, "skill_slugs": result}


# ---------------------------------------------------------------------------
# Etapa 9 - RAG documental nativo, bootstrap y aislamiento por proyecto
# ---------------------------------------------------------------------------
class DocumentIngestRequest(BaseModel):
    source_uri: str = Field(..., min_length=3, max_length=500)
    title: str = Field(..., min_length=1, max_length=255)
    doc_type: str = Field(default="OTHER", max_length=50)
    content: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    replace: bool = True


class DocumentSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


def verify_auth_or_portal(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "admin", "role_name": "ADMIN", "session_id": None, "user_id": None}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing credentials")
    token = authorization.split(" ", 1)[1].strip()
    try:
        decoded = jwt.decode(
            token, JWT_SECRET, algorithms=["HS256"],
            options={"require": ["exp", "jti", "sub"]},
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    if decoded.get("typ") == "portal":
        return _portal_auth_from_header(authorization)
    return verify_auth(x_harness_admin_key=x_harness_admin_key, authorization=authorization)


def _assert_doc_project_access(auth: Dict[str, Any], project_id: str, require_write: bool = False) -> None:
    if auth.get("role") == "admin":
        return
    if auth.get("role") == "agent":
        if auth.get("project_id") != project_id:
            raise HTTPException(status_code=403, detail="Cross-Project Isolation Violation")
        if require_write:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT skills FROM agent_sessions WHERE id = CAST(:sid AS UUID)"),
                    {"sid": auth.get("session_id")}
                ).fetchone()
            skills = set(row[0] or []) if row else set()
            if not ({"rag-docs-curator", "tech-writer"} & skills):
                raise HTTPException(status_code=403, detail="Document ingestion requires rag-docs-curator or tech-writer skill")
        return
    if require_write:
        raise HTTPException(status_code=403, detail="Document ingestion requires Admin or Documenter agent")
    with engine.connect() as conn:
        membership = conn.execute(
            sa.text("""
                SELECT 1 FROM project_memberships
                WHERE project_id = CAST(:pid AS UUID) AND user_id = CAST(:uid AS UUID)
                  AND status = 'APPROVED'
            """),
            {"pid": project_id, "uid": auth.get("user_id")}
        ).fetchone()
    if not membership:
        raise HTTPException(status_code=403, detail="Project access not approved")


@app.post("/api/v1/projects/{project_id}/documents/bootstrap")
async def bootstrap_project_documents(
    project_id: str,
    wait: bool = Query(default=True),
    timeout: int = Query(default=180, ge=10, le=600),
    auth: Dict[str, Any] = Depends(verify_portal_admin),
):
    assert_license_operational()
    with engine.connect() as conn:
        project = conn.execute(
            sa.text("""
                SELECT id::text, project_slug, is_active_slot
                FROM harness_projects WHERE id = CAST(:pid AS UUID)
            """),
            {"pid": project_id},
        ).mappings().fetchone()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project["is_active_slot"]:
        raise HTTPException(status_code=409, detail="Project slot is inactive")

    with engine.begin() as conn:
        ensure_hermes_worker_session(conn, project_id, set_online=False, initial_status="OFFLINE")
        ensure_headless_worker_session(conn, project_id, True)
        job = enqueue_job(
            conn, project_id, "RAG_BOOTSTRAP",
            {"reason": "admin_bootstrap", "project_slug": project["project_slug"]},
            idempotency_key=None,
            priority=10,
        )
    job_id = job["id"]
    if not wait:
        return {"queued": True, "job_id": job_id, "status": "QUEUED"}

    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)
    row = None
    while datetime.now(timezone.utc) < deadline:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("""
                    SELECT status, payload, error_message
                    FROM hermes_worker_jobs WHERE id = CAST(:jid AS UUID)
                """),
                {"jid": job_id},
            ).mappings().fetchone()
        if row and row["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        await asyncio.sleep(2)

    with engine.connect() as conn:
        documents_count = len(list_documents(conn, project_id))
    if not row:
        return JSONResponse(status_code=202, content={
            "queued": True, "job_id": job_id, "status": "UNKNOWN",
            "documents_count": documents_count,
        })
    payload = row["payload"] or {}
    result = payload.get("result") or {}
    summary = result.get("bootstrap") if isinstance(result, dict) else None
    if not isinstance(summary, dict):
        summary = result if isinstance(result, dict) else {}
    response = {
        "queued": False,
        "job_id": job_id,
        "status": row["status"],
        "documents_count": documents_count,
        **{k: summary.get(k) for k in ("scanned", "created", "updated", "skipped", "generated", "errors") if k in summary},
    }
    if row["error_message"]:
        response["error_message"] = row["error_message"]
        response.setdefault("errors", []).append(row["error_message"])
    if row["status"] != "COMPLETED":
        return JSONResponse(status_code=202, content=response)
    return response


@app.get("/api/v1/projects/{project_id}/documents")
def list_project_documents(
    project_id: str,
    auth: Dict[str, Any] = Depends(verify_auth_or_portal),
):
    _assert_doc_project_access(auth, project_id)
    with engine.connect() as conn:
        documents = list_documents(conn, project_id)
    return {"project_id": project_id, "documents": documents}


@app.post("/api/v1/projects/{project_id}/documents/search")
def search_project_documents(
    project_id: str,
    payload: DocumentSearchRequest,
    auth: Dict[str, Any] = Depends(verify_auth_or_portal),
):
    _assert_doc_project_access(auth, project_id)
    with engine.connect() as conn:
        chunks = search_documents(conn, project_id, payload.query, limit=payload.limit)
    return {"project_id": project_id, "query": payload.query, "chunks": chunks}


@app.post("/api/v1/projects/{project_id}/documents")
def ingest_project_document(
    project_id: str,
    payload: DocumentIngestRequest,
    auth: Dict[str, Any] = Depends(verify_auth_or_portal),
):
    assert_license_operational()
    _assert_doc_project_access(auth, project_id, require_write=True)
    try:
        with engine.begin() as conn:
            result = ingest_document(
                conn, project_id, payload.source_uri, payload.title,
                payload.doc_type, payload.content, payload.metadata,
                auth.get("session_id") if auth.get("role") == "agent" else None,
                replace=payload.replace,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# ---------------------------------------------------------------------------
# Etapa 10 - API interna del supervisor Hermes
# ---------------------------------------------------------------------------
class HermesWorkerRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=150)


class HermesCompleteRequest(BaseModel):
    worker_id: str = Field(default="hermes-worker", max_length=150)
    result: Dict[str, Any] = Field(default_factory=dict)


class HermesFailRequest(BaseModel):
    worker_id: str = Field(default="hermes-worker", max_length=150)
    error_message: str = Field(..., min_length=1, max_length=4000)


class InternalBootstrapRequest(BaseModel):
    session_id: Optional[str] = None
    trigger: str = Field(default="hermes_worker", max_length=100)


@app.post("/api/v1/internal/hermes/pool/sync")
def internal_hermes_pool_sync(auth: Dict[str, Any] = Depends(require_worker)):
    assert_license_operational()
    with engine.begin() as conn:
        policy = get_policy(conn, for_update=True)
        data = sync_pool(conn, policy)
    return data


@app.post("/api/v1/internal/hermes/jobs/claim")
def internal_hermes_claim(payload: HermesWorkerRequest, auth: Dict[str, Any] = Depends(require_worker)):
    assert_license_operational()
    with engine.begin() as conn:
        claimed = claim_job(conn, payload.worker_id)
    if not claimed:
        return {"job": None}
    record_event(
        engine,
        "HERMES_JOB_CLAIMED",
        {
            "job_id": claimed["job"]["id"],
            "project_id": claimed["job"]["project_id"],
            "job_type": claimed["job"]["job_type"],
            "worker_id": payload.worker_id,
        },
        source_session_id=claimed["session"]["session_id"],
    )
    return claimed


@app.post("/api/v1/internal/hermes/jobs/{job_id}/heartbeat")
def internal_hermes_heartbeat(
    job_id: str,
    payload: HermesWorkerRequest,
    auth: Dict[str, Any] = Depends(require_worker),
):
    with engine.begin() as conn:
        ok = heartbeat_job(conn, job_id, payload.worker_id)
    return {"job_id": job_id, "extended": bool(ok)}


@app.post("/api/v1/internal/hermes/jobs/{job_id}/complete")
def internal_hermes_complete(
    job_id: str,
    payload: HermesCompleteRequest,
    auth: Dict[str, Any] = Depends(require_worker),
):
    with engine.begin() as conn:
        job = complete_job(conn, job_id, payload.result)
    record_event(
        engine,
        "HERMES_JOB_COMPLETED",
        {
            "job_id": job_id,
            "project_id": job["project_id"],
            "job_type": job["job_type"],
            "worker_id": payload.worker_id,
        },
        source_session_id=job["session_id"],
    )
    if job["job_type"] in ("RAG_BOOTSTRAP", "RAG_REFRESH_PROJECT", "RAG_UPDATE_ARCHITECTURE"):
        detail = payload.result.get("bootstrap") if isinstance(payload.result, dict) else {}
        record_event(
            engine,
            "DOCS_BOOTSTRAP_COMPLETED",
            {
                "job_id": job_id,
                "project_id": job["project_id"],
                "job_type": job["job_type"],
                "trigger": job["job_type"].lower(),
                "scanned": (detail or {}).get("scanned", 0),
                "created": (detail or {}).get("created", 0),
                "updated": (detail or {}).get("updated", 0),
                "skipped": (detail or {}).get("skipped", 0),
                "documents_count": (detail or {}).get("documents_count", 0),
                "generated": (detail or {}).get("generated", []),
                "errors": (detail or {}).get("errors", []),
            },
            source_session_id=job["session_id"],
        )
    return {"job_id": job_id, "status": "COMPLETED", "project_id": job["project_id"]}


@app.post("/api/v1/internal/hermes/jobs/{job_id}/fail")
def internal_hermes_fail(
    job_id: str,
    payload: HermesFailRequest,
    auth: Dict[str, Any] = Depends(require_worker),
):
    with engine.begin() as conn:
        job = fail_job(conn, job_id, payload.error_message)
    record_event(
        engine,
        "HERMES_JOB_FAILED" if not job["retry"] else "HERMES_JOB_RETRY",
        {
            "job_id": job_id,
            "project_id": job["project_id"],
            "status": job["status"],
            "error": payload.error_message,
            "worker_id": payload.worker_id,
        },
        source_session_id=job["session_id"],
    )
    return job


@app.post("/api/v1/internal/projects/{project_id}/rag/bootstrap")
def internal_rag_bootstrap(
    project_id: str,
    payload: InternalBootstrapRequest,
    auth: Dict[str, Any] = Depends(require_worker),
):
    with engine.begin() as conn:
        project = conn.execute(
            sa.text("""
                SELECT id::text, project_slug, display_name
                FROM harness_projects
                WHERE id = CAST(:pid AS UUID) AND is_active_slot = true
            """),
            {"pid": project_id},
        ).mappings().fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Active project not found")
        session_id = payload.session_id
        if not session_id:
            row = conn.execute(
                sa.text("""
                    SELECT id::text FROM agent_sessions
                    WHERE project_id = CAST(:pid AS UUID)
                      AND session_kind = 'SYSTEM_WORKER'
                      AND agent_tool = 'hermes'
                      AND status <> 'REVOKED'
                    LIMIT 1
                """),
                {"pid": project_id},
            ).fetchone()
            session_id = row[0] if row else None
        summary = bootstrap_project_docs(
            conn, project_id, project["project_slug"], session_id, payload.trigger,
        )
    return {"project_id": project_id, "project_slug": project["project_slug"], **summary}
