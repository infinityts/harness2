"""E2E Fase 5.5 + Etapa 6: 0004, SESSION_SYNC, /repos, sin /complete y bridge MCP."""
import asyncio
import json
import os
import pathlib
import select
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS = BASE.replace("http", "ws", 1)
ADMIN_KEY = os.environ.get("HARNESS_ADMIN_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL")
REPO = pathlib.Path("/var/lib/harness/repos/harness-self")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
BRIDGE = "/app/client/harness_mcp_bridge.py"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, admin=False, bearer=None, timeout=120):
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["X-Harness-Admin-Key"] = ADMIN_KEY
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def git_repo(*args, check=True):
    return subprocess.run(
        ["git", "-C", str(REPO), "-c", "user.name=harness", "-c", "user.email=harness@local", *args],
        capture_output=True, text=True, check=check,
    )


def register(username, tool, skills):
    status, payload = http("POST", "/api/v1/sessions/register", {
        "engineer_username": username, "agent_tool": tool, "llm_provider": "local-qwen",
        "skills": skills, "max_context_tokens": 32000,
    }, admin=True)
    assert status == 200, (status, payload)
    return payload


async def recv_until(ws, event_type, timeout=10):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"no llego {event_type}")
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if msg.get("event") == event_type:
            return msg


def cleanup():
    task_rows = db_rows("""
        SELECT t.id::text AS id, t.git_branch
        FROM harness_tasks t
        JOIN feature_consensus_plans p ON p.id = t.plan_id
        WHERE p.requested_by LIKE 'e2e-stage6%'
    """)
    for task in task_rows:
        shutil.rmtree(WORKTREES / task["id"], ignore_errors=True)
        if task["git_branch"]:
            git_repo("branch", "-D", task["git_branch"], check=False)
    db_exec("DELETE FROM agent_links WHERE source_session_id IN (SELECT id FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage6-%') OR target_session_id IN (SELECT id FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage6-%')")
    db_exec("DELETE FROM harness_tasks WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage6%')")
    db_exec("DELETE FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage6%'")
    db_exec("DELETE FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage6-%'")
    db_exec("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE repo_name = 'harness-self'")
    git_repo("worktree", "prune", check=False)


def mcp_send(proc, obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def mcp_read(proc, timeout=20):
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise TimeoutError("MCP bridge no respondio")
    line = proc.stdout.readline()
    if not line:
        err = proc.stderr.read() if proc.poll() is not None else ""
        raise RuntimeError(f"bridge cerro stdout: {err[-1500:]}")
    return json.loads(line)


def mcp_call(proc, req_id, name, arguments):
    mcp_send(proc, {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
    resp = mcp_read(proc)
    if "error" in resp:
        raise RuntimeError(f"MCP error: {resp['error']}")
    content = resp["result"]["content"][0]["text"]
    return json.loads(content), resp["result"].get("isError", False)


def override_plan(files):
    return {
        "strategy": "CONSERVATIVE_MINIMAL_FOOTPRINT",
        "allow_dependency_changes": False,
        "estimated_context_per_task": 1200,
        "tasks": [{
            "title": f"[Impl] Tarea bridge en {files[0]}",
            "required_skills": ["python"],
            "allowed_files": [files[0]],
            "allow_dependency_changes": False,
        }],
    }


async def main():
    print("== FASE 5.5 + ETAPA 6 ==")
    cleanup()

    print("\n--- 1. Alembic 0004 + SESSION_SYNC ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    col = db_rows("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='agent_sessions' AND column_name='system_contract_rules'
    """)
    print("alembic:", version, "column:", [x["column_name"] for x in col])
    assert version in ("0004", "0005") and col

    s = register("e2e-stage6-bridge", "opencode", ["python"])
    ws = await websockets.connect(f"{WS}/ws/agent/{s['session_id']}", additional_headers={"Authorization": f"Bearer {s['session_key']}"})
    first = await recv_until(ws, "SESSION_SYNC", 5)
    print("SESSION_SYNC inicial:", first)
    assert first["system_contract_rules"] == []

    st, ctrl = http("PATCH", f"/api/v1/sessions/{s['session_id']}/control", {
        "skills": ["python", "fastapi"],
        "max_context_tokens": 24000,
        "system_contract_rules": ["NEVER_TOUCH_ENV", "STRICT_TYPING"],
    }, admin=True)
    print("control:", st, {k: ctrl.get(k) for k in ("system_contract_rules", "max_context_tokens")})
    assert st == 200 and ctrl["system_contract_rules"] == ["NEVER_TOUCH_ENV", "STRICT_TYPING"]
    await recv_until(ws, "CONTROL_RULES_UPDATED", 5)
    await ws.close()

    ws2 = await websockets.connect(f"{WS}/ws/agent/{s['session_id']}", additional_headers={"Authorization": f"Bearer {s['session_key']}"})
    sync = await recv_until(ws2, "SESSION_SYNC", 5)
    print("SESSION_SYNC tras reconexion:", sync)
    assert sync["system_contract_rules"] == ["NEVER_TOUCH_ENV", "STRICT_TYPING"]
    assert "python" in sync["skills"] and sync["max_context_tokens"] == 24000
    await ws2.close()

    print("\n--- 2. /complete eliminado + /repos ---")
    st, _ = http("POST", f"/api/v1/tasks/{uuid.uuid4()}/complete", {"commit_sha": "x", "modified_files": [], "summary": "x"}, admin=True)
    print("POST /complete:", st)
    assert st in (404, 405)

    st, repos = http("GET", "/api/v1/repos", admin=True)
    print("repos:", st, repos)
    assert st == 200 and "repos" in repos
    harness = next(x for x in repos["repos"] if x["repo_name"] == "harness-self")
    assert harness["files"] >= 7 and harness["commit_sha"]

    print("\n--- 3. Bridge MCP stdio ---")
    env = {
        **os.environ,
        "HARNESS_URL": BASE,
        "HARNESS_SESSION_ID": s["session_id"],
        "HARNESS_SESSION_KEY": s["session_key"],
        "HARNESS_HEARTBEAT_SECONDS": "1",
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, BRIDGE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
    )
    try:
        time.sleep(1.5)
        mcp_send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "e2e-stage6", "version": "1.0"}}})
        init = mcp_read(proc)
        print("initialize:", init)
        assert init["result"]["serverInfo"]["name"] == "harness-mcp-bridge"

        mcp_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        mcp_send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = mcp_read(proc)
        names = [t["name"] for t in tools["result"]["tools"]]
        print("tools:", names)
        assert len(names) == 6 and "harness_submit_task_changes" in names

        st, plan = http("POST", "/api/v1/plans/propose", {
            "requested_by": "e2e-stage6",
            "repo_name": "harness-self",
            "requirement_prompt": "Agregar comentario validado por el bridge MCP",
            "required_skills_hint": ["python"],
            "plan_a_override": override_plan(["src/main.py"]),
            "plan_b_override": override_plan(["src/main.py"]),
        }, admin=True)
        assert st == 200 and len(plan["created_tasks"]) == 1
        task = plan["created_tasks"][0]

        st, disp = http("POST", "/api/v1/tasks/dispatch", admin=True)
        print("dispatch:", st, disp)
        assert st == 200 and disp["count"] == 1
        item = disp["dispatched"][0]
        assert item["assigned_to"]["session_id"] == s["session_id"]
        time.sleep(0.5)

        active, is_err = mcp_call(proc, 3, "harness_get_active_contract_and_task", {})
        print("get_active:", is_err, {k: active.get(k) for k in ("session_id", "status", "system_contract_rules")})
        print("task:", {k: (active.get("task") or {}).get(k) for k in ("id", "status", "allowed_files", "git_branch")})
        assert not is_err
        assert active["system_contract_rules"] == ["NEVER_TOUCH_ENV", "STRICT_TYPING"]
        assert active["task"] and active["task"]["id"] == task["task_id"]
        assert active["task"]["allowed_files"] == ["src/main.py"]

        pack, is_err = mcp_call(proc, 4, "harness_request_context_pack", {
            "repo_name": "harness-self",
            "query": "watchdog heartbeat timeout offline sessions",
            "token_budget": 800,
        })
        print("context pack:", is_err, "used:", pack.get("estimated_tokens_used"), "chunks:", len(pack.get("surgical_ast_chunks", [])))
        assert not is_err and pack["estimated_tokens_used"] <= 800 and pack["surgical_ast_chunks"]

        val, is_err = mcp_call(proc, 5, "harness_validate_symbols", {
            "repo_name": "harness-self",
            "referenced_symbols": ["register_agent_session", "invented_bridge_symbol"],
        })
        print("validate:", is_err, val)
        assert not is_err and val["is_clean"] is False
        assert "invented_bridge_symbol" in val["hallucinated_symbols"]

        wt = pathlib.Path(item["worktree"])
        base = (wt / "src/main.py").read_text(encoding="utf-8")
        changed = base + "\n# merge validado por harness_mcp_bridge\n"
        submitted, is_err = mcp_call(proc, 6, "harness_submit_task_changes", {
            "task_id": task["task_id"],
            "file_changes": {"src/main.py": changed},
            "commit_message": "bridge e2e merge",
        })
        print("submit bridge:", is_err, {k: submitted.get(k) for k in ("http_status", "context_purged")}, submitted.get("result", {}).get("status"))
        assert not is_err and submitted["http_status"] == 200
        assert submitted["result"]["status"] == "MERGED" and submitted["context_purged"] is True

        task_row = db_rows("SELECT status FROM harness_tasks WHERE id = CAST(:tid AS UUID)", tid=task["task_id"])[0]
        await asyncio.sleep(1.3)
        sess_row = db_rows("SELECT status, current_context_tokens FROM agent_sessions WHERE id = CAST(:sid AS UUID)", sid=s["session_id"])[0]
        print("post-submit:", task_row["status"], dict(sess_row))
        assert task_row["status"] == "MERGED" and sess_row["current_context_tokens"] == 0 and sess_row["status"] == "ONLINE"

        print("\n--- 4. Relay por bridge ---")
        peer = register("e2e-stage6-peer", "cursor", ["typescript"])
        st, link = http("POST", "/api/v1/war-room/links", {
            "source_session_id": s["session_id"], "target_session_id": peer["session_id"],
            "link_type": "PEER_REVIEW", "trigger_event": "MANUAL_RELAY",
        }, admin=True)
        assert st == 200
        relayed, is_err = mcp_call(proc, 7, "harness_send_peer_message", {
            "target_session_id": peer["session_id"],
            "message_type": "note",
            "content": "revision del bridge",
        })
        print("relay bridge:", is_err, relayed)
        assert not is_err and "delivered" in relayed
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print("\n--- 5. Descarga del bridge + UI multi-repo ---")
    page = urllib.request.urlopen(BASE + "/api/v1/client/bridge.py", timeout=10)
    body = page.read().decode("utf-8")
    print("bridge.py:", page.status, page.headers.get("content-type"), "bytes:", len(body))
    assert page.status == 200 and "harness_submit_task_changes" in body

    ui = urllib.request.urlopen(BASE + "/war-room", timeout=10).read().decode("utf-8")
    assert 'id="repo"' in ui and "/api/v1/repos" in ui

    print("\n--- 6. Cleanup ---")
    cleanup()
    counts = db_rows("""
        SELECT 'sessions' AS k, count(*) AS n FROM agent_sessions
        UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
        UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
        UNION ALL SELECT 'links', count(*) FROM agent_links
        UNION ALL SELECT 'events', count(*) FROM war_room_events
        UNION ALL SELECT 'files', count(*) FROM repo_file_map WHERE repo_name='harness-self'
        UNION ALL SELECT 'vectors', count(*) FROM code_ast_vectors v JOIN repo_file_map m ON m.id=v.file_id WHERE m.repo_name='harness-self'
    """)
    print("counts finales:", {r["k"]: r["n"] for r in counts})
    print("\nE2E STAGE 5.5 + 6: OK")


asyncio.run(main())
