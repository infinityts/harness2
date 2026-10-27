"""E2E Etapa 7B: portal auth, proyectos, sesiones 1:1, aislamiento y config bundle."""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
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
ADMIN_EMAIL = os.environ.get("HARNESS_INITIAL_ADMIN_EMAIL", "admin@harness.local")
ADMIN_PASSWORD = os.environ.get("HARNESS_INITIAL_ADMIN_PASSWORD", "AdminHarness2026!")
PUBLIC_URL = os.environ.get("HARNESS_PUBLIC_URL", "http://11.0.0.3:8444").rstrip("/")
REPO = pathlib.Path("/var/lib/harness/repos/harness-self")
REPOS = pathlib.Path("/var/lib/harness/repos")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
TEST_SLUG = "stage7b-iso"
TEST_EMAIL = "e2e-stage7b@example.com"
TEST_PASSWORD = "Engineer2026!"

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


async def api(method, path, body=None, admin=False, bearer=None, timeout=120):
    return await asyncio.to_thread(http, method, path, body, admin, bearer, timeout)


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


def cleanup():
    tasks = db_rows("""
        SELECT t.id::text AS id, t.git_branch
        FROM harness_tasks t JOIN feature_consensus_plans p ON p.id = t.plan_id
        WHERE p.requested_by LIKE 'e2e-stage7b%'
    """)
    for task in tasks:
        shutil.rmtree(WORKTREES / task["id"], ignore_errors=True)
        if task["git_branch"]:
            git_repo("branch", "-D", task["git_branch"], check=False)
    db_exec("DELETE FROM harness_tasks WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage7b%')")
    db_exec("DELETE FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage7b%'")
    db_exec("DELETE FROM agent_links WHERE source_session_id IN (SELECT s.id FROM agent_sessions s JOIN harness_users u ON u.id=s.user_id WHERE u.email LIKE 'e2e-stage7b%') OR target_session_id IN (SELECT s.id FROM agent_sessions s JOIN harness_users u ON u.id=s.user_id WHERE u.email LIKE 'e2e-stage7b%')")
    db_exec("DELETE FROM agent_sessions WHERE user_id IN (SELECT id FROM harness_users WHERE email LIKE 'e2e-stage7b%')")
    db_exec("DELETE FROM project_memberships WHERE user_id IN (SELECT id FROM harness_users WHERE email LIKE 'e2e-stage7b%')")
    db_exec("DELETE FROM harness_users WHERE email LIKE 'e2e-stage7b%'")
    project = db_rows("SELECT id::text FROM harness_projects WHERE project_slug = :slug", slug=TEST_SLUG)
    if project:
        db_exec("DELETE FROM project_memberships WHERE project_id = CAST(:pid AS UUID)", pid=project[0]["id"])
        db_exec("DELETE FROM agent_sessions WHERE project_id = CAST(:pid AS UUID)", pid=project[0]["id"])
        db_exec("DELETE FROM harness_projects WHERE id = CAST(:pid AS UUID)", pid=project[0]["id"])
    db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=TEST_SLUG)
    shutil.rmtree(REPOS / TEST_SLUG, ignore_errors=True)
    git_repo("worktree", "prune", check=False)


async def heartbeat(ws, state, tokens=10):
    try:
        while True:
            await ws.send(json.dumps({"current_context_tokens": tokens, "status": state["status"]}))
            await asyncio.sleep(2.5)
    except Exception:
        pass


async def start_session_ws(session_id, key):
    ws = await websockets.connect(
        f"{WS}/ws/agent/{session_id}",
        additional_headers={"Authorization": f"Bearer {key}"},
    )
    state = {"status": "ONLINE"}
    task = asyncio.create_task(heartbeat(ws, state))
    await asyncio.sleep(0.4)
    return ws, task, state


async def main():
    print("== ETAPA 7 - PARTE B ==")
    cleanup()

    print("\n--- 1. Admin login y proyectos ---")
    st, admin_login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    print("admin login:", st, admin_login.get("user", {}).get("role") if isinstance(admin_login, dict) else admin_login)
    assert st == 200 and admin_login["user"]["role"] == "ADMIN"
    admin_token = admin_login["access_token"]
    st, me_admin = await api("GET", "/api/v1/auth/me", bearer=admin_token)
    print("admin me:", st, [p["project_slug"] for p in me_admin.get("projects", [])])
    assert st == 200
    st, projects = await api("GET", "/api/v1/projects", bearer=admin_token)
    print("projects admin:", st, [p["project_slug"] for p in projects.get("projects", [])])
    assert st == 200
    harness_project = next(p for p in projects["projects"] if p["project_slug"] == "harness-self")

    print("\n--- 2. Registro PENDING y aprobacion ---")
    st, reg = await api("POST", "/api/v1/auth/register-invite", {
        "email": TEST_EMAIL, "password": TEST_PASSWORD, "full_name": "E2E Stage7B",
    })
    print("register-invite:", st, reg.get("status"))
    assert st == 200 and reg["status"] == "PENDING"
    st, _ = await api("POST", "/api/v1/auth/login", {"email": TEST_EMAIL, "password": TEST_PASSWORD})
    print("login PENDING:", st)
    assert st == 403

    st, member = await api("POST", f"/api/v1/projects/{harness_project['id']}/members", {
        "email": TEST_EMAIL, "allowed_skills": ["python"],
    }, bearer=admin_token)
    print("member harness-self:", st, member["membership"]["status"], member["user"]["status"])
    assert st == 200 and member["membership"]["status"] == "APPROVED"
    engineer_id = member["user"]["id"]

    st, login = await api("POST", "/api/v1/auth/login", {"email": TEST_EMAIL, "password": TEST_PASSWORD})
    print("engineer login:", st, login.get("user", {}).get("role"))
    assert st == 200
    eng_token = login["access_token"]
    st, me = await api("GET", "/api/v1/auth/me", bearer=eng_token)
    print("engineer me:", st, [p["project_slug"] for p in me["projects"]])
    assert st == 200 and [p["project_slug"] for p in me["projects"]] == ["harness-self"]

    print("\n--- 3. Proyecto nuevo y memberships ---")
    st, created = await api("POST", "/api/v1/projects", {
        "project_slug": TEST_SLUG,
        "display_name": "Stage7B Isolation",
        "description": "Proyecto de prueba de aislamiento",
        "default_branch": "main",
    }, bearer=admin_token)
    print("create project:", st, created)
    assert st == 200
    iso_project = created["project"]
    assert iso_project["project_slug"] == TEST_SLUG
    assert (REPOS / TEST_SLUG).exists()

    st, member2 = await api("POST", f"/api/v1/projects/{iso_project['id']}/members", {
        "email": TEST_EMAIL, "allowed_skills": ["python"],
    }, bearer=admin_token)
    print("member stage7b-iso:", st, member2["membership"]["status"])
    assert st == 200

    st, eng_projects = await api("GET", "/api/v1/projects", bearer=eng_token)
    slugs = [p["project_slug"] for p in eng_projects["projects"]]
    print("engineer projects:", st, slugs)
    assert st == 200 and set(slugs) == {"harness-self", TEST_SLUG}
    st, me2 = await api("GET", "/api/v1/auth/me", bearer=eng_token)
    assert {p["project_slug"] for p in me2["projects"]} == {"harness-self", TEST_SLUG}

    print("\n--- 4. Sesiones 1:1 proyecto y rotacion ---")
    st, s_a = await api("POST", "/api/v1/portal/sessions", {
        "project_id": harness_project["id"], "agent_tool": "opencode",
        "llm_provider": "local-llm", "max_context_tokens": 32000,
    }, bearer=eng_token)
    print("session A:", st, s_a.get("project_slug"))
    assert st == 200 and s_a["project_slug"] == "harness-self"

    st, s_b = await api("POST", "/api/v1/portal/sessions", {
        "project_id": harness_project["id"], "agent_tool": "opencode",
        "llm_provider": "local-llm", "max_context_tokens": 32000,
    }, bearer=eng_token)
    print("session B (rota):", st, "replaced:", s_b.get("replaced_session_ids"))
    assert st == 200 and s_a["session_id"] in s_b["replaced_session_ids"]

    st, s_c = await api("POST", "/api/v1/portal/sessions", {
        "project_id": iso_project["id"], "agent_tool": "cursor",
        "llm_provider": "local-llm", "max_context_tokens": 32000,
    }, bearer=eng_token)
    print("session C:", st, s_c.get("project_slug"))
    assert st == 200 and s_c["project_slug"] == TEST_SLUG

    decode = __import__("jwt").decode(s_b["session_key"], os.environ["HARNESS_JWT_SECRET"], algorithms=["HS256"])
    print("session JWT:", {k: decode.get(k) for k in ("sub", "user_id", "project_id", "project_slug")})
    assert decode["user_id"] == engineer_id and decode["project_id"] == harness_project["id"]
    assert decode["project_slug"] == "harness-self"

    ws_b, hb_b, state_b = await start_session_ws(s_b["session_id"], s_b["session_key"])
    ws_c, hb_c, state_c = await start_session_ws(s_c["session_id"], s_c["session_key"])

    print("\n--- 5. Config bundle ---")
    st, bundle = await api("GET", f"/api/v1/portal/sessions/{s_b['session_id']}/config-bundle", bearer=eng_token)
    print("config bundle:", st, sorted(bundle.keys()) if isinstance(bundle, dict) else bundle)
    assert st == 200
    assert bundle["env"]["HARNESS_PROJECT_SLUG"] == "harness-self"
    assert bundle["env"]["HARNESS_URL"] == PUBLIC_URL
    assert bundle["env"]["HARNESS_SESSION_KEY"] == s_b["session_key"]
    assert "HARNESS_SESSION_KEY=" in bundle["hermes_cli_env"]
    assert "/api/v1/client/bridge.py" in bundle["bridge_download_command"]
    cursor = json.loads(bundle["cursor_mcp_json"])
    opencode = json.loads(bundle["opencode_json"])
    assert cursor["mcpServers"]["harness"]["env"]["HARNESS_SESSION_ID"] == s_b["session_id"]
    assert opencode["mcp"]["servers"]["harness"]["environment"]["HARNESS_PROJECT_SLUG"] == "harness-self"

    print("\n--- 6. Aislamiento cross-project ---")
    st, cross1 = await api("POST", "/api/v1/context/pack", {
        "repo_name": TEST_SLUG, "query": "anything", "token_budget": 500,
    }, bearer=s_b["session_key"])
    print("context pack cross:", st, cross1)
    assert st == 403 and "Cross-Project" in json.dumps(cross1)
    st, cross2 = await api("GET", f"/api/v1/repos/{TEST_SLUG}/map", bearer=s_b["session_key"])
    print("map cross:", st, cross2)
    assert st == 403
    st, cross3 = await api("POST", "/api/v1/guardrails/validate-symbols", {
        "repo_name": TEST_SLUG, "referenced_symbols": ["x"],
    }, bearer=s_b["session_key"])
    print("guardrails cross:", st, cross3)
    assert st == 403
    st, cross4 = await api("POST", "/api/v1/context/pack", {
        "repo_name": "harness-self", "query": "anything", "token_budget": 500,
    }, bearer=s_c["session_key"])
    print("context pack cross C:", st)
    assert st == 403

    st, link = await api("POST", "/api/v1/war-room/links", {
        "source_session_id": s_b["session_id"], "target_session_id": s_c["session_id"],
        "link_type": "PEER_REVIEW", "trigger_event": "MANUAL_RELAY",
    }, admin=True)
    print("link cross:", st)
    assert st == 200
    st, relay = await api("POST", "/api/v1/war-room/relay", {
        "target_session_id": s_c["session_id"], "message_type": "note", "content": "cross",
    }, bearer=s_b["session_key"])
    print("relay cross:", st, relay)
    assert st == 403 and "Cross-Project" in json.dumps(relay)

    print("\n--- 7. Dispatch por proyecto y submit ---")
    plan_override = {
        "strategy": "CONSERVATIVE_MINIMAL_FOOTPRINT",
        "allow_dependency_changes": False,
        "estimated_context_per_task": 1200,
        "tasks": [{
            "title": "[Impl] Cambio portal en src/main.py",
            "required_skills": ["python"],
            "allowed_files": ["src/main.py"],
            "allow_dependency_changes": False,
        }],
    }
    st, plan = await api("POST", "/api/v1/plans/propose", {
        "requested_by": "e2e-stage7b",
        "repo_name": "harness-self",
        "project_id": harness_project["id"],
        "requirement_prompt": "Agregar comentario validado por el portal",
        "required_skills_hint": ["python"],
        "plan_a_override": plan_override,
        "plan_b_override": plan_override,
    }, admin=True)
    print("plan:", st, plan.get("plan_id"), "project:", (plan.get("master_plan_c") or {}).get("project_id"))
    assert st == 200 and len(plan["created_tasks"]) == 1
    task = plan["created_tasks"][0]

    st, disp = await api("POST", "/api/v1/tasks/dispatch", admin=True)
    print("dispatch:", st, disp)
    assert st == 200 and disp["count"] == 1
    item = disp["dispatched"][0]
    assert item["assigned_to"]["session_id"] == s_b["session_id"]
    assert item["assigned_to"]["session_id"] != s_c["session_id"]

    st, cross_submit = await api("POST", f"/api/v1/tasks/{task['task_id']}/submit", {
        "file_changes": {"src/main.py": "# cross\n"},
        "commit_message": "cross",
    }, bearer=s_c["session_key"])
    print("submit cross C:", st, cross_submit)
    assert st == 403 and "Cross-Project" in json.dumps(cross_submit)

    wt = pathlib.Path(item["worktree"])
    base = (wt / "src/main.py").read_text(encoding="utf-8")
    st, submitted = await api("POST", f"/api/v1/tasks/{task['task_id']}/submit", {
        "file_changes": {"src/main.py": base + "\n# stage7b portal submit\n"},
        "commit_message": "stage7b portal submit",
    }, bearer=s_b["session_key"])
    print("submit B:", st, {k: submitted.get(k) for k in ("status", "main_commit")})
    assert st == 200 and submitted["status"] == "MERGED"

    print("\n--- 8. Revocacion de membership ---")
    st, revoked = await api("DELETE", f"/api/v1/projects/{harness_project['id']}/members/{engineer_id}", bearer=admin_token)
    print("revoke membership:", st, revoked)
    assert st == 200 and s_b["session_id"] in revoked["revoked_sessions"]

    st, _ = await api("POST", "/api/v1/context/pack", {
        "repo_name": "harness-self", "query": "x", "token_budget": 500,
    }, bearer=s_b["session_key"])
    print("revoked session key:", st)
    assert st == 401

    st, me3 = await api("GET", "/api/v1/auth/me", bearer=eng_token)
    print("engineer me tras revocar:", st, [p["project_slug"] for p in me3["projects"]])
    assert {p["project_slug"] for p in me3["projects"]} == {TEST_SLUG}
    st, projects3 = await api("GET", "/api/v1/projects", bearer=eng_token)
    assert {p["project_slug"] for p in projects3["projects"]} == {TEST_SLUG}

    print("\n--- 9. Cleanup ---")
    hb_b.cancel(); hb_c.cancel()
    await ws_b.close(); await ws_c.close()
    cleanup()
    counts = db_rows("""
        SELECT 'users' AS k, count(*) AS n FROM harness_users
        UNION ALL SELECT 'projects', count(*) FROM harness_projects
        UNION ALL SELECT 'memberships', count(*) FROM project_memberships
        UNION ALL SELECT 'sessions', count(*) FROM agent_sessions
        UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
        UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
        UNION ALL SELECT 'links', count(*) FROM agent_links
        UNION ALL SELECT 'events', count(*) FROM war_room_events
        UNION ALL SELECT 'files', count(*) FROM repo_file_map
    """)
    print("counts finales:", {r["k"]: r["n"] for r in counts})
    print("\nE2E STAGE 7B: OK")


asyncio.run(main())
