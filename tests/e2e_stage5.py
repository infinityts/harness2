"""E2E Fase 4.5 + Etapa 5: locks, conflicto, War Room y skills en caliente."""
import asyncio
import json
import os
import pathlib
import re
import shutil
import subprocess
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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


async def heartbeat(ws, state, tokens):
    try:
        while True:
            await ws.send(json.dumps({"current_context_tokens": tokens, "status": state["status"]}))
            await asyncio.sleep(2.5)
    except Exception:
        pass


def cleanup():
    task_rows = db_rows("""
        SELECT t.id::text AS id, t.git_branch
        FROM harness_tasks t
        JOIN feature_consensus_plans p ON p.id = t.plan_id
        WHERE p.requested_by LIKE 'e2e-stage5%'
    """)
    for task in task_rows:
        shutil.rmtree(WORKTREES / task["id"], ignore_errors=True)
        if task["git_branch"]:
            git_repo("branch", "-D", task["git_branch"], check=False)
    db_exec("DELETE FROM agent_links WHERE source_session_id IN (SELECT id FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage5-%') OR target_session_id IN (SELECT id FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage5-%')")
    db_exec("DELETE FROM harness_tasks WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage5%')")
    db_exec("DELETE FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage5%'")
    db_exec("DELETE FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage5-%'")
    db_exec("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE repo_name = 'harness-self'")
    git_repo("worktree", "prune", check=False)


def override_plan(files):
    tasks = []
    for idx, file_path in enumerate(files, start=1):
        tasks.append({
            "title": f"[Impl] Tarea {idx} en {file_path}",
            "required_skills": ["python"],
            "allowed_files": [file_path],
            "allow_dependency_changes": False,
        })
    return {
        "strategy": "MODULAR_SCALABLE_VERIFIED",
        "allow_dependency_changes": False,
        "estimated_context_per_task": 1200,
        "tasks": tasks,
    }


async def main():
    print("== FASE 4.5 + ETAPA 5 ==")
    cleanup()

    print("\n--- 1. Alembic 0003 ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    tables = db_rows("SELECT to_regclass('public.agent_links') AS links, to_regclass('public.war_room_events') AS events")[0]
    print("alembic:", version, "tables:", dict(tables))
    assert version in ("0003", "0004", "0005", "0006", "0007", "0008") and tables["links"] and tables["events"]

    print("\n--- 2. Tres agentes + War Room WS ---")
    s1 = register("e2e-stage5-opencode", "opencode", ["python"])
    s2 = register("e2e-stage5-cursor", "cursor", ["typescript"])
    s3 = register("e2e-stage5-hermes", "hermes", ["rust"])

    ws1 = await websockets.connect(f"{WS}/ws/agent/{s1['session_id']}", additional_headers={"Authorization": f"Bearer {s1['session_key']}"})
    ws2 = await websockets.connect(f"{WS}/ws/agent/{s2['session_id']}", additional_headers={"Authorization": f"Bearer {s2['session_key']}"})
    ws3 = await websockets.connect(f"{WS}/ws/agent/{s3['session_id']}", additional_headers={"Authorization": f"Bearer {s3['session_key']}"})
    state1, state2, state3 = {"status": "ONLINE"}, {"status": "ONLINE"}, {"status": "ONLINE"}
    hb1 = asyncio.create_task(heartbeat(ws1, state1, 5))
    hb2 = asyncio.create_task(heartbeat(ws2, state2, 5))
    hb3 = asyncio.create_task(heartbeat(ws3, state3, 50))
    await asyncio.sleep(0.5)

    wwar = await websockets.connect(f"{WS}/ws/war-room", additional_headers={"X-Harness-Admin-Key": ADMIN_KEY})
    hello = json.loads(await asyncio.wait_for(wwar.recv(), 5))
    print("war-room:", hello)
    assert hello["event"] == "WAR_ROOM_CONNECTED"

    print("\n--- 3. Skills en caliente (hermes) ---")
    st, ctrl = http("PATCH", f"/api/v1/sessions/{s3['session_id']}/control", {
        "skills": ["rust", "python"],
        "max_context_tokens": 24000,
        "system_contract_rules": ["no tocar main sin lock"],
    }, admin=True)
    print("control:", st, {k: ctrl.get(k) for k in ("skills", "max_context_tokens", "delivered")})
    assert st == 200 and "python" in ctrl["skills"]
    ev = await recv_until(ws3, "CONTROL_RULES_UPDATED", 5)
    print("WS hermes:", ev)
    assert "python" in ev["skills"]
    war_ev = await recv_until(wwar, "SKILLS_INJECTED", 5)
    print("war-room skills:", war_ev["data"].get("skills"))
    assert "python" in war_ev["data"]["skills"]

    print("\n--- 4. Plan con 2 tareas + dispatch concurrente ---")
    plan_b = override_plan(["src/main.py", "src/indexer.py"])
    plan_a = override_plan(["src/main.py"])
    st, plan = http("POST", "/api/v1/plans/propose", {
        "requested_by": "e2e-stage5",
        "repo_name": "harness-self",
        "requirement_prompt": "Implementar dos mejoras paralelas en main e indexer",
        "required_skills_hint": ["python"],
        "plan_a_override": plan_a,
        "plan_b_override": plan_b,
    }, admin=True)
    print("plan:", st, plan.get("plan_id"), "tasks:", len(plan["created_tasks"]))
    assert st == 200 and len(plan["created_tasks"]) == 2
    await recv_until(wwar, "PLAN_APPROVED", 5)

    st, disp = http("POST", "/api/v1/tasks/dispatch", admin=True)
    print("dispatch:", st, disp)
    assert st == 200 and disp["count"] == 2 and not disp["failed"]
    assigned_sessions = {x["assigned_to"]["session_id"] for x in disp["dispatched"]}
    print("asignadas a:", [(x["assigned_to"]["engineer_username"], x["allowed_files"]) for x in disp["dispatched"]])
    assert assigned_sessions == {s1["session_id"], s3["session_id"]}
    assert len({x["worktree"] for x in disp["dispatched"]}) == 2
    for item in disp["dispatched"]:
        assert pathlib.Path(item["worktree"]).exists()
    task_main = next(x for x in disp["dispatched"] if "src/main.py" in x["allowed_files"])
    task_index = next(x for x in disp["dispatched"] if "src/indexer.py" in x["allowed_files"])
    assert task_main["assigned_to"]["session_id"] == s1["session_id"]
    assert task_index["assigned_to"]["session_id"] == s3["session_id"]
    state1["status"] = "BUSY"
    state3["status"] = "BUSY"
    await recv_until(ws1, "TASK_ASSIGNED", 5)
    await recv_until(ws3, "TASK_ASSIGNED", 5)
    await recv_until(wwar, "TASK_ASSIGNED", 5)

    print("\n--- 5. Relay sin enlace (403) ---")
    st, denied = http("POST", "/api/v1/war-room/relay", {
        "target_session_id": s2["session_id"], "message_type": "note", "content": "hola",
    }, bearer=s1["session_key"])
    print("relay sin link:", st, denied)
    assert st == 403 and "Link Not Authorized" in json.dumps(denied)

    print("--- 6. Crear enlace y relay autorizado ---")
    st, link = http("POST", "/api/v1/war-room/links", {
        "source_session_id": s1["session_id"], "target_session_id": s2["session_id"],
        "link_type": "PEER_REVIEW", "trigger_event": "ON_TASK_MERGED",
    }, admin=True)
    print("link:", st, link.get("id"), link.get("trigger_event"))
    assert st == 200

    st, relay = http("POST", "/api/v1/war-room/relay", {
        "target_session_id": s2["session_id"], "message_type": "note", "content": "revisa mi cambio",
    }, bearer=s1["session_key"])
    print("relay:", st, relay)
    assert st == 200 and relay["delivered"] is True
    msg = await recv_until(ws2, "AGENT_MESSAGE", 5)
    print("cursor recibe:", msg)
    assert msg["from_session_id"] == s1["session_id"] and msg["content"] == "revisa mi cambio"

    print("\n--- 7. Submit de opencode + UPSTREAM_TASK_MERGED ---")
    wt_main = pathlib.Path(task_main["worktree"])
    base_main = (wt_main / "src/main.py").read_text(encoding="utf-8")
    helper = "stage5_helper"
    if f"def {helper}" in base_main:
        helper = f"{helper}_{int(time.time())}"
    st, submitted = http("POST", f"/api/v1/tasks/{task_main['task_id']}/submit", {
        "file_changes": {"src/main.py": base_main + f"\n\ndef {helper}(x):\n    return x + 1\n"},
        "commit_message": f"add {helper}",
    }, bearer=s1["session_key"])
    print("submit opencode:", st, {k: submitted.get(k) for k in ("status", "main_commit", "changed_files")})
    assert st == 200 and submitted["status"] == "MERGED"
    state1["status"] = "ONLINE"
    upstream = await recv_until(ws2, "UPSTREAM_TASK_MERGED", 8)
    print("upstream cursor:", upstream)
    assert upstream["from_session_id"] == s1["session_id"] and upstream["task_id"] == task_main["task_id"]
    await recv_until(wwar, "TASK_MERGED", 5)

    print("\n--- 8. Conflicto real + reopen + merge resuelto ---")
    st, plan_c = http("POST", "/api/v1/plans/propose", {
        "requested_by": "e2e-stage5-conflict",
        "repo_name": "harness-self",
        "requirement_prompt": "Cambiar el TTL de sesion y forzar conflicto controlado",
        "required_skills_hint": ["python"],
        "plan_a_override": override_plan(["src/main.py"]),
        "plan_b_override": override_plan(["src/main.py"]),
    }, admin=True)
    assert st == 200 and len(plan_c["created_tasks"]) == 1
    task_c = plan_c["created_tasks"][0]

    st, disp_c = http("POST", "/api/v1/tasks/dispatch", admin=True)
    print("dispatch C:", st, disp_c)
    assert st == 200 and disp_c["count"] == 1
    item_c = disp_c["dispatched"][0]
    assert item_c["assigned_to"]["session_id"] == s1["session_id"]
    state1["status"] = "BUSY"
    wt_c = pathlib.Path(item_c["worktree"])
    content_c = (wt_c / "src/main.py").read_text(encoding="utf-8")
    match = re.search(r'MAX_CONTEXT_RATIO", "([0-9.]+)"', content_c)
    assert match, "no se encontro MAX_CONTEXT_RATIO"
    v = match.group(1)
    main_val = f"{float(v) + 0.01:.2f}"
    submit_val = f"{float(v) + 0.02:.2f}"

    base_path = REPO / "src/main.py"
    base_text = base_path.read_text(encoding="utf-8")
    git_repo("checkout", "main")
    assert git_repo("status", "--porcelain").stdout.strip() == ""
    base_path.write_text(
        base_text.replace(f'MAX_CONTEXT_RATIO", "{v}"', f'MAX_CONTEXT_RATIO", "{main_val}"'),
        encoding="utf-8",
    )
    git_repo("add", "-A")
    git_repo("commit", "-m", "advance main for stage5 conflict")

    conflicted = content_c.replace(f'MAX_CONTEXT_RATIO", "{v}"', f'MAX_CONTEXT_RATIO", "{submit_val}"')
    st, conflict = http("POST", f"/api/v1/tasks/{task_c['task_id']}/submit", {
        "file_changes": {"src/main.py": conflicted},
        "commit_message": "change ttl (conflict)",
    }, bearer=s1["session_key"])
    print("submit conflicto:", st, conflict)
    assert st == 409 and conflict["detail"]["error"] == "MERGE_CONFLICT"
    assert git_repo("status", "--porcelain").stdout.strip() == ""

    task_row = db_rows("SELECT status, failed_attempts_memory FROM harness_tasks WHERE id = CAST(:tid AS UUID)", tid=task_c["task_id"])[0]
    print("estado C:", task_row["status"], "memoria:", [m.get("barrier") for m in task_row["failed_attempts_memory"]])
    assert task_row["status"] == "CONFLICT"
    assert any(m.get("barrier") == "MERGE_CONFLICT" for m in task_row["failed_attempts_memory"])
    lock = db_rows("SELECT locked_by_task_id::text AS tid FROM repo_file_map WHERE repo_name='harness-self' AND file_path='src/main.py'")[0]
    print("lock tras conflicto:", lock["tid"])
    assert lock["tid"] is None
    state1["status"] = "ONLINE"

    st, reopened = http("POST", f"/api/v1/tasks/{task_c['task_id']}/reopen-from-main", bearer=s1["session_key"])
    print("reopen:", st, reopened)
    assert st == 200 and reopened["status"] == "IN_PROGRESS"
    state1["status"] = "BUSY"
    lock = db_rows("SELECT locked_by_task_id::text AS tid FROM repo_file_map WHERE repo_name='harness-self' AND file_path='src/main.py'")[0]
    assert lock["tid"] == task_c["task_id"]

    wt_c2 = pathlib.Path(reopened["worktree"])
    content_c2 = (wt_c2 / "src/main.py").read_text(encoding="utf-8")
    assert f'MAX_CONTEXT_RATIO", "{main_val}"' in content_c2
    resolved = content_c2.replace(f'MAX_CONTEXT_RATIO", "{main_val}"', f'MAX_CONTEXT_RATIO", "{submit_val}"')
    st, resolved_submit = http("POST", f"/api/v1/tasks/{task_c['task_id']}/submit", {
        "file_changes": {"src/main.py": resolved},
        "commit_message": "resolve ttl after reopen",
    }, bearer=s1["session_key"])
    print("submit resuelto:", st, {k: resolved_submit.get(k) for k in ("status", "main_commit")})
    assert st == 200 and resolved_submit["status"] == "MERGED"
    state1["status"] = "ONLINE"
    await recv_until(wwar, "TASK_CONFLICT", 5)
    assert not wt_c2.exists()

    print("\n--- 9. War Room UI + topologia ---")
    page = urllib.request.urlopen(BASE + "/war-room", timeout=10)
    body = page.read().decode("utf-8", errors="ignore")
    print("war-room:", page.status, page.headers.get("content-type"), "bytes:", len(body))
    assert page.status == 200 and "text/html" in (page.headers.get("content-type") or "")
    st, topo = http("GET", "/api/v1/war-room/topology", admin=True)
    print("topology:", st, "nodes:", len(topo["nodes"]), "links:", len(topo["links"]), "tasks:", len(topo["tasks"]), "locks:", len(topo["locks"]), "events:", len(topo["events"]))
    assert st == 200 and len(topo["nodes"]) == 3 and len(topo["links"]) >= 1 and len(topo["events"]) >= 1

    print("\n--- 10. Cleanup ---")
    st, failed = http("POST", f"/api/v1/tasks/{task_index['task_id']}/fail-checkpoint", {
        "error_reason": "fin de prueba stage5", "release": True,
    }, bearer=s3["session_key"])
    print("fail-checkpoint hermes:", st, failed.get("status"), failed.get("released"))
    state3["status"] = "ONLINE"

    for t in (hb1, hb2, hb3):
        t.cancel()
    await ws1.close()
    await ws2.close()
    await ws3.close()
    await wwar.close()
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
    print("\nE2E STAGE 4.5 + 5: OK")


asyncio.run(main())
