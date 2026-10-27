import os
import json
import uuid
import hashlib
import hmac
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, List, Dict, Optional

import jwt
import sqlalchemy as sa
from fastapi import Depends, FastAPI, Header, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel, Field

from src.indexer import index_repository, compute_embedding_768
from src.git_guardian import (
    GitBarrierError,
    GitTaskPermissionError,
    cleanup_task_worktree,
    prepare_worktree_for_task,
    submit_task_changes,
)
from src.orchestrator import (
    ContractViolation,
    TaskPermissionError,
    complete_task,
    dispatch_pending_tasks,
    fail_task_checkpoint,
    generate_dual_plans_and_synthesize,
)

ADMIN_API_KEY = os.getenv("HARNESS_ADMIN_API_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("HARNESS_JWT_SECRET", "change-me-secret")
HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "10"))
MAX_CONTEXT_RATIO = float(os.getenv("MAX_CONTEXT_RATIO", "0.65"))
SESSION_JWT_TTL_HOURS = int(os.getenv("SESSION_JWT_TTL_HOURS", "12"))

engine = sa.create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

# Registro en memoria de conexiones WebSocket activas en este nodo
active_connections: Dict[str, WebSocket] = {}


class SessionRegisterRequest(BaseModel):
    engineer_username: str = Field(..., min_length=2, max_length=100)
    agent_tool: str = Field(..., pattern="^(opencode|claudecode|cursor|hermes|pi|custom)$")
    llm_provider: str = Field(..., min_length=2, max_length=100)
    skills: List[str] = Field(default_factory=list)
    max_context_tokens: int = Field(default=32000, gt=1024)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_auth(
    x_harness_admin_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Admin por X-Harness-Admin-Key o agente por Authorization: Bearer <session_key>."""
    if ADMIN_API_KEY and x_harness_admin_key and hmac.compare_digest(x_harness_admin_key, ADMIN_API_KEY):
        return {"role": "admin", "session_id": None}

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        session_id = decoded.get("sub")
        if not session_id:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("""
                    SELECT id::text, status FROM agent_sessions
                    WHERE id = CAST(:sid AS UUID)
                      AND session_key_hash = :khash
                      AND status IN ('ONLINE', 'BUSY')
                """),
                {"sid": session_id, "khash": hash_token(token)}
            ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Session not active")
        return {"role": "agent", "session_id": row[0], "status": row[1]}

    raise HTTPException(
        status_code=401,
        detail="Missing credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )


def require_admin(auth: Dict[str, Any] = Depends(verify_auth)) -> Dict[str, Any]:
    if auth.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin key required")
    return auth


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
    while True:
        try:
            with engine.begin() as conn:
                rows = conn.execute(
                    sa.text("""
                        SELECT id::text FROM agent_sessions
                        WHERE status IN ('ONLINE', 'BUSY')
                          AND last_heartbeat < NOW() - make_interval(secs => :timeout)
                    """),
                    {"timeout": HEARTBEAT_TIMEOUT}
                ).fetchall()
                expired_ids = [r[0] for r in rows]
                if expired_ids:
                    cleanup_disconnected_sessions(conn, expired_ids, target_status="OFFLINE")
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
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog_task = asyncio.create_task(session_watchdog_loop())
    yield
    watchdog_task.cancel()


app = FastAPI(title="Harness Control Plane", version="0.2.0", lifespan=lifespan)


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
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/api/v1/sessions/register", dependencies=[Depends(require_admin)])
def register_agent_session(payload: SessionRegisterRequest):
    session_id = str(uuid.uuid4())
    now_ts = int(datetime.now(timezone.utc).timestamp())
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
                    session_key_hash, skills, status, max_context_tokens, current_context_tokens
                ) VALUES (
                    CAST(:id AS UUID), :user, :tool, :provider,
                    :khash, CAST(:skills AS JSONB), 'ONLINE', :max_tok, 0
                )
            """),
            {
                "id": session_id,
                "user": payload.engineer_username,
                "tool": payload.agent_tool,
                "provider": payload.llm_provider,
                "khash": key_hash,
                "skills": json.dumps(payload.skills),
                "max_tok": payload.max_context_tokens
            }
        )

    return {
        "session_id": session_id,
        "session_key": session_key,
        "status": "ONLINE",
        "ws_url": f"/ws/agent/{session_id}"
    }


@app.get("/api/v1/sessions", dependencies=[Depends(verify_auth)])
def list_sessions():
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                SELECT id::text, engineer_username, agent_tool, llm_provider,
                       skills, status, current_context_tokens, max_context_tokens,
                       last_heartbeat, connected_at
                FROM agent_sessions
                ORDER BY connected_at DESC
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
                SET current_context_tokens = 0, last_heartbeat = NOW()
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
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if decoded.get("sub") != session_id:
            await websocket.close(code=4003, reason="Token subject mismatch")
            return
    except Exception:
        await websocket.close(code=4003, reason="Invalid session_key token")
        return

    token_hash = hash_token(token)
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("""
                SELECT status, max_context_tokens
                FROM agent_sessions
                WHERE id = CAST(:sid AS UUID) AND session_key_hash = :khash
            """),
            {"sid": session_id, "khash": token_hash}
        ).fetchone()

        if not row or row[0] == "REVOKED":
            await websocket.close(code=4003, reason="Session unauthorized or revoked")
            return

        # Al reconectar con key valida (y no revocada), asegurar estado ONLINE y heartbeat fresco
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'ONLINE', last_heartbeat = NOW()
                WHERE id = CAST(:sid AS UUID)
            """),
            {"sid": session_id}
        )

    if not pre_accepted:
        await websocket.accept()
    active_connections[session_id] = websocket

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
    except WebSocketDisconnect:
        pass
    finally:
        active_connections.pop(session_id, None)
        with engine.begin() as conn:
            cleanup_disconnected_sessions(conn, [session_id], target_status="OFFLINE")


class RepoIndexRequest(BaseModel):
    repo_name: str = Field(..., min_length=2, max_length=150, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    repo_url: Optional[str] = None
    branch: str = Field(default="main", max_length=150)


class ContextPackRequest(BaseModel):
    repo_name: str = Field(..., min_length=2, max_length=150)
    query: str = Field(..., min_length=1, max_length=4000)
    allowed_files: Optional[List[str]] = None
    token_budget: int = Field(default=2000, ge=300, le=16000)
    top_k: int = Field(default=5, ge=1, le=20)


class SymbolValidationRequest(BaseModel):
    repo_name: str = Field(..., min_length=2, max_length=150)
    referenced_symbols: List[str]


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
def get_repository_quick_map(repo_name: str):
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
def build_surgical_context_pack(payload: ContextPackRequest):
    """
    Construye un Context Pack quirurgico (< token_budget) combinando:
    1. El Mapa Rapido de archivos relevantes.
    2. Los fragmentos exactos AST mas cercanos en pgvector (HNSW cosine distance).
    """
    q_vec = compute_embedding_768(payload.query)
    q_vec_str = "[" + ",".join(str(x) for x in q_vec) + "]"

    with engine.connect() as conn:
        # 1. Mapa rapido de archivos del repo
        map_rows = conn.execute(
            sa.text("""
                SELECT file_path, quick_summary, locked_by_task_id::text AS locked_by_task_id
                FROM repo_file_map
                WHERE repo_name = :rname
                ORDER BY file_path ASC
            """),
            {"rname": payload.repo_name}
        ).mappings().all()

        # 2. Busqueda vectorial HNSW en code_ast_vectors
        if payload.allowed_files:
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
                {"qvec": q_vec_str, "rname": payload.repo_name, "afiles": payload.allowed_files, "topk": payload.top_k}
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
                {"qvec": q_vec_str, "rname": payload.repo_name, "topk": payload.top_k}
            ).mappings().all()

    # Empaquetado respetando estrictamente el presupuesto de tokens (~4 caracteres por token)
    char_budget = payload.token_budget * 4
    used_chars = 0

    repo_overview = []
    for r in map_rows:
        line = f"{r['quick_summary']} [LOCK={r['locked_by_task_id'] or 'FREE'}]"
        if used_chars + len(line) > int(char_budget * 0.35):
            break
        repo_overview.append(line)
        used_chars += len(line)

    selected_snippets = []
    for vr in vec_rows:
        snippet_text = vr["code_snippet"]
        remaining = char_budget - used_chars
        if remaining <= 120:
            break
        if len(snippet_text) > remaining:
            suffix = "\n# ... [truncated by token_budget]"
            snippet_text = snippet_text[: max(0, remaining - len(suffix))] + suffix
        used_chars += len(snippet_text)
        selected_snippets.append({
            "file_path": vr["file_path"],
            "symbol_name": vr["symbol_name"],
            "symbol_signature": vr["symbol_signature"],
            "lines": [vr["start_line"], vr["end_line"]],
            "similarity": float(vr["similarity"]),
            "code_snippet": snippet_text
        })

    estimated_tokens = min(payload.token_budget, max(1, used_chars // 4))
    return {
        "repo_name": payload.repo_name,
        "token_budget": payload.token_budget,
        "estimated_tokens_used": estimated_tokens,
        "allowed_files_contract": payload.allowed_files or "READ_ONLY_UNRESTRICTED",
        "repo_quick_map": repo_overview,
        "surgical_ast_chunks": selected_snippets
    }


@app.post("/api/v1/guardrails/validate-symbols", dependencies=[Depends(verify_auth)])
def validate_symbols_against_hallucination(payload: SymbolValidationRequest):
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


class TaskCompleteRequest(BaseModel):
    commit_sha: str = Field(default="", max_length=64)
    modified_files: List[str] = Field(default_factory=list)
    summary: str = Field(default="", max_length=2000)


class TaskFailRequest(BaseModel):
    error_reason: str = Field(..., min_length=1, max_length=2000)
    git_diff_summary: Optional[str] = None
    release: bool = True


class TaskSubmitRequest(BaseModel):
    file_changes: Optional[Dict[str, str]] = None
    unified_diff: Optional[str] = None
    commit_message: str = Field(default="", max_length=500)


@app.post("/api/v1/plans/propose", dependencies=[Depends(require_admin)])
def propose_dual_plan(payload: PlanProposeRequest):
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
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@app.post("/api/v1/tasks/dispatch", dependencies=[Depends(require_admin)])
async def dispatch_tasks():
    with engine.begin() as conn:
        dispatched = dispatch_pending_tasks(conn, HEARTBEAT_TIMEOUT, MAX_CONTEXT_RATIO)

    for item in dispatched:
        ws = active_connections.get(item["assigned_to"]["session_id"])
        if ws:
            try:
                await ws.send_json({"event": "TASK_ASSIGNED", "task": item})
            except Exception:
                pass

    return {"dispatched": dispatched, "count": len(dispatched)}


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
                       required_skills, git_branch, allowed_files, allow_dependency_changes,
                       failed_attempts_memory, status, created_at
                FROM harness_tasks{where}
                ORDER BY created_at ASC
            """),
            params
        ).mappings().all()

    return {"tasks": [dict(r) for r in rows]}


@app.post("/api/v1/tasks/{task_id}/complete", dependencies=[Depends(require_admin)], deprecated=True)
async def complete_task_endpoint(
    task_id: str,
    payload: TaskCompleteRequest,
):
    session_id = None
    try:
        with engine.begin() as conn:
            result = complete_task(
                conn=conn,
                task_id=task_id,
                session_id=session_id,
                commit_sha=payload.commit_sha,
                modified_files=payload.modified_files,
                summary=payload.summary,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TaskPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ContractViolation as exc:
        raise HTTPException(status_code=403, detail={"error": "Contract Violation", "files": exc.files})

    ws = active_connections.get(result["assigned_session_id"]) if result["assigned_session_id"] else None
    if ws:
        try:
            await ws.send_json({"event": "CLEAR_LLM_CONTEXT", "task_id": task_id})
        except Exception:
            pass

    # /complete es legado (solo admin): limpia el worktree efimero si existiera.
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

    return result


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

    return result
