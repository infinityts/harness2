"""E2E Fase 2.5 + Etapa 3: auth, reconciliacion, doble plan, dispatch y lifecycle."""
import asyncio
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS = BASE.replace("http", "ws", 1)
ADMIN_KEY = os.environ.get("HARNESS_ADMIN_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL")
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT_SECONDS", "10"))
REPO = pathlib.Path("/var/lib/harness/repos/harness-self")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, admin=False, bearer=None):
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["X-Harness-Admin-Key"] = ADMIN_KEY
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def git(*args):
    subprocess.run(
        ["git", "-C", str(REPO), "-c", "user.name=harness", "-c", "user.email=harness@local", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def register(username, skills):
    status, payload = http(
        "POST",
        "/api/v1/sessions/register",
        {
            "engineer_username": username,
            "agent_tool": "opencode",
            "llm_provider": "local-qwen",
            "skills": skills,
            "max_context_tokens": 32000,
        },
        admin=True,
    )
    assert status == 200, (status, payload)
    return payload


def cleanup():
    with engine.begin() as conn:
        conn.execute(sa.text("""
            DELETE FROM harness_tasks
            WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by = 'e2e-test')
        """))
        conn.execute(sa.text("DELETE FROM feature_consensus_plans WHERE requested_by = 'e2e-test'"))
        conn.execute(sa.text("DELETE FROM agent_sessions WHERE engineer_username LIKE 'e2e-%'"))
        conn.execute(sa.text("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE repo_name = 'harness-self'"))
    ghost_file = REPO / "src" / "temp_ghost.py"
    if ghost_file.exists():
        ghost_file.unlink()


async def recv_event(ws, timeout=5.0):
    try:
        return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    except Exception:
        return None


async def main():
    print("== FASE 2.5 + ETAPA 3 ==")
    cleanup()

    print("\n--- 1. AUTH ---")
    st_anon, body_anon = http("GET", "/api/v1/sessions")
    print("GET /sessions sin credenciales:", st_anon, body_anon)
    st_admin, body_admin = http("GET", "/api/v1/sessions", admin=True)
    print("GET /sessions con X-Harness-Admin-Key:", st_admin, "sessions:", len(body_admin.get("sessions", [])))
    assert st_anon in (401, 403) and st_admin == 200

    print("\n--- 2. RECONCILIACION DE INDICE ---")
    ghost = REPO / "src" / "temp_ghost.py"
    ghost.write_text("def ghost_func():\n    return 'ghost'\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "add temp ghost")
    st, idx1 = http("POST", "/api/v1/repos/index", {"repo_name": "harness-self", "branch": "main"}, admin=True)
    print("index con ghost:", st, {k: idx1.get(k) for k in ("files_indexed", "ast_vectors_created")})
    st, before = http("POST", "/api/v1/guardrails/validate-symbols", {"repo_name": "harness-self", "referenced_symbols": ["ghost_func"]}, admin=True)
    print("validate ghost_func antes de borrar:", st, before)
    assert before["is_clean"] is True

    ghost.unlink()
    git("add", "-A")
    git("commit", "-m", "remove temp ghost")
    st, idx2 = http("POST", "/api/v1/repos/index", {"repo_name": "harness-self", "branch": "main"}, admin=True)
    print("index sin ghost:", st, {k: idx2.get(k) for k in ("files_indexed", "ast_vectors_created")})
    st, after = http("POST", "/api/v1/guardrails/validate-symbols", {"repo_name": "harness-self", "referenced_symbols": ["ghost_func"]}, admin=True)
    print("validate ghost_func despues de borrar:", st, after)
    assert after["is_clean"] is False and "ghost_func" in after["hallucinated_symbols"]

    print("\n--- 3. SESION OFFLINE + PLANES A/B/C ---")
    s1 = register("e2e-offline", ["python", "fastapi"])
    print("S1 registrada, esperando watchdog para dejarla OFFLINE...")
    time.sleep(HEARTBEAT_TIMEOUT + 3)
    st, sessions = http("GET", "/api/v1/sessions", admin=True)
    s1_state = next(x for x in sessions["sessions"] if x["id"] == s1["session_id"])
    print("S1 estado:", s1_state["status"])
    assert s1_state["status"] == "OFFLINE"

    st, plan = http(
        "POST",
        "/api/v1/plans/propose",
        {
            "requested_by": "e2e-test",
            "repo_name": "harness-self",
            "requirement_prompt": "Implementar un endpoint FastAPI para consultar el estado de las tareas y validar contratos de archivos",
            "required_skills_hint": ["python", "fastapi"],
        },
        admin=True,
    )
    print("plan_id:", plan.get("plan_id"), "status:", plan.get("status"))
    print("justification:", plan.get("arbiter_justification"))
    print("plan_a tasks:", [(t["title"], t["allowed_files"]) for t in plan["plan_a"]["tasks"]])
    print("plan_b tasks:", [(t["title"], t["allowed_files"]) for t in plan["plan_b"]["tasks"]])
    print("master_c tasks:", [(t["step"], t["title"], t["allowed_files"], t["required_skills"]) for t in plan["master_plan_c"]["tasks"]])
    print("created_tasks:", [(t["task_id"][:8], t["git_branch"], t["allowed_files"]) for t in plan["created_tasks"]])
    assert st == 200 and len(plan["created_tasks"]) == 2

    tasks = plan["created_tasks"]
    task1 = tasks[0]

    st, listed = http("GET", f"/api/v1/tasks?plan_id={plan['plan_id']}", admin=True)
    print("GET /tasks por plan:", st, "count:", len(listed.get("tasks", [])))
    assert st == 200 and len(listed["tasks"]) == 2

    print("\n--- 4. DISPATCH POR SKILLS ---")
    s2 = register("e2e-rust", ["rust"])
    s3 = register("e2e-python", ["python", "fastapi"])
    ws2 = await websockets.connect(f"{WS}/ws/agent/{s2['session_id']}", additional_headers={"Authorization": f"Bearer {s2['session_key']}"})
    ws3 = await websockets.connect(f"{WS}/ws/agent/{s3['session_id']}", additional_headers={"Authorization": f"Bearer {s3['session_key']}"})
    await ws2.send(json.dumps({"current_context_tokens": 10}))
    await ws3.send(json.dumps({"current_context_tokens": 10}))
    await recv_event(ws2)
    await recv_event(ws3)

    st, dispatch = http("POST", "/api/v1/tasks/dispatch", admin=True)
    print("dispatch:", st, dispatch)
    assert st == 200 and dispatch["count"] == 1
    assert dispatch["dispatched"][0]["assigned_to"]["session_id"] == s3["session_id"]

    assigned_evt = await recv_event(ws3)
    print("evento WS S3:", assigned_evt)
    assert assigned_evt and assigned_evt.get("event") == "TASK_ASSIGNED"

    rows = db_rows(
        "SELECT status, assigned_session_id::text AS sid FROM harness_tasks WHERE id = CAST(:tid AS UUID)",
        tid=task1["task_id"],
    )
    print("tarea en BD:", dict(rows[0]))
    assert rows[0]["status"] == "IN_PROGRESS" and rows[0]["sid"] == s3["session_id"]

    s3_row = db_rows("SELECT status FROM agent_sessions WHERE id = CAST(:sid AS UUID)", sid=s3["session_id"])[0]
    print("S3 estado:", s3_row["status"])
    assert s3_row["status"] == "BUSY"

    locks = db_rows(
        "SELECT file_path, locked_by_task_id::text AS tid FROM repo_file_map WHERE repo_name = 'harness-self' AND file_path = ANY(:files)",
        files=task1["allowed_files"],
    )
    print("locks:", [dict(x) for x in locks])
    assert locks and all(x["tid"] == task1["task_id"] for x in locks)

    print("\n--- 5. CONTRACT VIOLATION ---")
    st, violation = http(
        "POST",
        f"/api/v1/tasks/{task1['task_id']}/complete",
        {"commit_sha": "deadbeef", "modified_files": ["archivo_prohibido.py"], "summary": "probe"},
        bearer=s3["session_key"],
    )
    print("complete con archivo prohibido:", st, violation)
    assert st == 403 and "Contract" in json.dumps(violation)

    print("\n--- 6. COMPLETE LIMPIO ---")
    st, completed = http(
        "POST",
        f"/api/v1/tasks/{task1['task_id']}/complete",
        {"commit_sha": "cafebabe", "modified_files": task1["allowed_files"], "summary": "e2e ok"},
        bearer=s3["session_key"],
    )
    print("complete valido:", st, completed)
    assert st == 200 and completed["status"] == "MERGED"

    events = []
    while True:
        evt = await recv_event(ws3, timeout=3)
        if not evt:
            break
        events.append(evt)
        if evt.get("event") == "CLEAR_LLM_CONTEXT":
            break
    print("eventos WS tras complete:", events)

    task_row = db_rows("SELECT status FROM harness_tasks WHERE id = CAST(:tid AS UUID)", tid=task1["task_id"])[0]
    s3_row = db_rows("SELECT status, current_context_tokens FROM agent_sessions WHERE id = CAST(:sid AS UUID)", sid=s3["session_id"])[0]
    locks = db_rows("SELECT count(*) AS n FROM repo_file_map WHERE locked_by_task_id = CAST(:tid AS UUID)", tid=task1["task_id"])[0]
    print("post-complete:", task_row["status"], dict(s3_row), "locks:", locks["n"])
    assert task_row["status"] == "MERGED" and s3_row["status"] == "ONLINE" and s3_row["current_context_tokens"] == 0 and locks["n"] == 0

    print("\n--- 7. FAIL-CHECKPOINT (segunda tarea) ---")
    await ws3.send(json.dumps({"current_context_tokens": 500}))
    await recv_event(ws3)
    st, dispatch2 = http("POST", "/api/v1/tasks/dispatch", admin=True)
    print("segundo dispatch:", st, dispatch2)
    assert st == 200 and dispatch2["count"] == 1
    task2 = dispatch2["dispatched"][0]
    await recv_event(ws3)

    st, failed = http(
        "POST",
        f"/api/v1/tasks/{task2['task_id']}/fail-checkpoint",
        {"error_reason": "contexto purgado al 65%", "git_diff_summary": "WIP", "release": True},
        bearer=s3["session_key"],
    )
    print("fail-checkpoint:", st, failed)
    assert st == 200 and failed["status"] == "PENDING" and failed["released"] is True
    mem = db_rows("SELECT failed_attempts_memory, status FROM harness_tasks WHERE id = CAST(:tid AS UUID)", tid=task2["task_id"])[0]
    print("memoria persistente:", dict(mem))
    assert mem["status"] == "PENDING" and len(mem["failed_attempts_memory"]) == 1

    print("\n--- 8. CLEANUP ---")
    await ws2.close()
    await ws3.close()
    cleanup()
    counts = db_rows("""
        SELECT 'sessions' AS k, count(*) AS n FROM agent_sessions
        UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
        UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
        UNION ALL SELECT 'files', count(*) FROM repo_file_map WHERE repo_name='harness-self'
        UNION ALL SELECT 'vectors', count(*) FROM code_ast_vectors v JOIN repo_file_map m ON m.id=v.file_id WHERE m.repo_name='harness-self'
    """)
    print("counts finales:", {r["k"]: r["n"] for r in counts})
    print("\nE2E STAGE 2.5 + 3: OK")


asyncio.run(main())
