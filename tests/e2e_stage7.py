"""E2E Etapa 7 (Partes A/B/C): IAM, proyectos, sesiones 1:1, aislamiento y portal responsive."""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS_BASE = BASE.replace("http", "ws", 1)
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_INITIAL_ADMIN_EMAIL", "admin@harness.local")
ADMIN_PASSWORD = os.environ.get("HARNESS_INITIAL_ADMIN_PASSWORD", "AdminHarness2026!")
PUBLIC_URL = os.environ.get("HARNESS_PUBLIC_URL", "http://11.0.0.3:8444").rstrip("/")
REPOS = pathlib.Path("/var/lib/harness/repos")
ALPHA = "proj-alpha"
BETA = "proj-beta"
DEV_EMAIL = "dev1@empresa.com"
DEV_PASSWORD = "Dev1Empresa2026!"
REQUESTED_BY = "e2e-stage7"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, admin=False, timeout=120):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if admin:
        headers["X-Harness-Admin-Key"] = os.environ.get("HARNESS_ADMIN_API_KEY", "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            if not raw:
                return resp.status, None
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw


async def api(method, path, body=None, bearer=None, admin=False, timeout=120):
    return await asyncio.to_thread(http, method, path, body, bearer, admin, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def git_repo(slug, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(REPOS / slug), "-c", "user.name=harness", "-c", "user.email=harness@local", *args],
        capture_output=True, text=True, check=check,
    )


def cleanup():
    db_exec("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE repo_name IN (:a, :b)", a=ALPHA, b=BETA)
    db_exec("""
        DELETE FROM harness_tasks
        WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage7%')
    """)
    db_exec("DELETE FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage7%'")
    db_exec("DELETE FROM harness_projects WHERE project_slug IN (:a, :b)", a=ALPHA, b=BETA)
    db_exec("DELETE FROM harness_users WHERE email = :email", email=DEV_EMAIL)
    db_exec("DELETE FROM repo_file_map WHERE repo_name IN (:a, :b)", a=ALPHA, b=BETA)
    shutil.rmtree(REPOS / ALPHA, ignore_errors=True)
    shutil.rmtree(REPOS / BETA, ignore_errors=True)


async def heartbeat(ws, state, tokens=15):
    try:
        while True:
            await ws.send(json.dumps({"current_context_tokens": tokens, "status": state["status"]}))
            await asyncio.sleep(2.5)
    except Exception:
        pass


async def open_agent_ws(session_id, session_key):
    ws = await websockets.connect(
        f"{WS_BASE}/ws/agent/{session_id}",
        additional_headers={"Authorization": f"Bearer {session_key}"},
    )
    state = {"status": "ONLINE"}
    task = asyncio.create_task(heartbeat(ws, state))
    await asyncio.sleep(0.4)
    return ws, task, state


async def main():
    print("== ETAPA 7 - PARTES A/B/C ==")
    cleanup()

    print("\n--- 1. Migracion 0005/0006 y login de Admin por correo ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    tables = {r["tablename"] for r in db_rows("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    required = {"harness_users", "harness_projects", "project_memberships", "agent_sessions", "feature_consensus_plans"}
    print("alembic_version:", version, "| tablas IAM:", sorted(required & tables))
    assert int(version) >= 5, f"head inesperado: {version}"
    assert required.issubset(tables)

    st, admin_login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    print("admin login:", st, admin_login.get("user", {}).get("role") if isinstance(admin_login, dict) else admin_login)
    assert st == 200 and admin_login["user"]["role"] == "ADMIN"
    admin_token = admin_login["access_token"]

    st, me_admin = await api("GET", "/api/v1/auth/me", bearer=admin_token)
    assert st == 200 and me_admin["user"]["email"] == ADMIN_EMAIL
    print("admin /auth/me:", st, "proyectos:", [p["project_slug"] for p in me_admin["projects"]])

    print("\n--- 2. Creacion automatica de proj-alpha y proj-beta ---")
    projects = {}
    for slug, name in ((ALPHA, "Proyecto Alpha"), (BETA, "Proyecto Beta")):
        st, created = await api("POST", "/api/v1/projects", {
            "project_slug": slug,
            "display_name": name,
            "description": f"Proyecto E2E {slug}",
            "default_branch": "main",
        }, bearer=admin_token)
        print(f"create {slug}:", st, created.get("index") if isinstance(created, dict) else created)
        assert st == 200
        projects[slug] = created["project"]
        repo = REPOS / slug
        assert repo.exists() and (repo / ".git").exists()
        assert git_repo(slug, "rev-parse", "--verify", "HEAD").returncode == 0
        assert git_repo(slug, "branch", "--show-current").stdout.strip() == "main"
        indexed = db_rows("SELECT file_path FROM repo_file_map WHERE repo_name = :slug", slug=slug)
        paths = {r["file_path"] for r in indexed}
        print(f"repo_file_map {slug}:", sorted(paths))
        assert "README.md" in paths, f"{slug} no indexo README.md"

    print("\n--- 3. Alta de dev1@empresa.com con acceso solo a proj-alpha ---")
    st, member = await api(
        "POST",
        f"/api/v1/projects/{projects[ALPHA]['id']}/members",
        {"email": DEV_EMAIL, "full_name": "Dev Uno", "password": DEV_PASSWORD, "allowed_skills": ["python"]},
        bearer=admin_token,
    )
    print("membership alpha:", st, member.get("user", {}).get("email"), member.get("membership", {}).get("status"))
    assert st == 200 and member["membership"]["status"] == "APPROVED"
    assert member["user"]["role"] == "ENGINEER"
    dev_id = member["user"]["id"]

    st, dev_login = await api("POST", "/api/v1/auth/login", {"email": DEV_EMAIL, "password": DEV_PASSWORD})
    print("dev1 login:", st, dev_login.get("user", {}).get("role"))
    assert st == 200 and dev_login["user"]["role"] == "ENGINEER"
    dev_token = dev_login["access_token"]

    st, dev_projects = await api("GET", "/api/v1/projects", bearer=dev_token)
    slugs = [p["project_slug"] for p in dev_projects["projects"]]
    print("dev1 projects:", st, slugs)
    assert st == 200 and slugs == [ALPHA]

    print("\n--- 4. Bloqueo de proyecto no autorizado ---")
    st, denied = await api("POST", "/api/v1/portal/sessions", {
        "project_id": projects[BETA]["id"],
        "agent_tool": "opencode",
        "llm_provider": "local-llm",
        "max_context_tokens": 32000,
    }, bearer=dev_token)
    print("session beta no autorizada:", st, denied)
    assert st == 403 and "Project Access Not Approved" in json.dumps(denied)

    print("\n--- 5. Sesion alpha, config bundle y aislamiento cross-project ---")
    st, alpha_session = await api("POST", "/api/v1/portal/sessions", {
        "project_id": projects[ALPHA]["id"],
        "agent_tool": "opencode",
        "llm_provider": "local-llm",
        "max_context_tokens": 32000,
    }, bearer=dev_token)
    print("session alpha:", st, alpha_session.get("project_slug"))
    assert st == 200 and alpha_session["project_slug"] == ALPHA

    decoded = __import__("jwt").decode(alpha_session["session_key"], os.environ["HARNESS_JWT_SECRET"], algorithms=["HS256"])
    print("JWT alpha:", {k: decoded.get(k) for k in ("sub", "user_id", "project_id", "project_slug")})
    assert decoded["user_id"] == dev_id
    assert decoded["project_id"] == projects[ALPHA]["id"]
    assert decoded["project_slug"] == ALPHA

    st, bundle = await api("GET", f"/api/v1/portal/sessions/{alpha_session['session_id']}/config-bundle", bearer=dev_token)
    print("config bundle alpha:", st, sorted(bundle.keys()) if isinstance(bundle, dict) else bundle)
    assert st == 200
    assert bundle["env"] == {
        "HARNESS_URL": PUBLIC_URL,
        "HARNESS_SESSION_ID": alpha_session["session_id"],
        "HARNESS_SESSION_KEY": alpha_session["session_key"],
        "HARNESS_PROJECT_SLUG": ALPHA,
    }
    for block in ("cursor_mcp_json", "claudecode_mcp_json", "opencode_json", "hermes_cli_env", "bridge_download_command"):
        assert bundle.get(block), f"bloque vacio: {block}"
    assert json.loads(bundle["cursor_mcp_json"])["mcpServers"]["harness"]["env"]["HARNESS_PROJECT_SLUG"] == ALPHA
    assert "/api/v1/client/bridge.py" in bundle["bridge_download_command"]

    st, cross = await api("POST", "/api/v1/context/pack", {
        "repo_name": BETA, "query": "cualquier cosa", "token_budget": 500,
    }, bearer=alpha_session["session_key"])
    print("context pack cross:", st, cross)
    assert st == 403 and "Cross-Project Isolation Violation" in json.dumps(cross)

    print("\n--- 6. Aprobacion de proj-beta y segunda sesion independiente ---")
    st, member_beta = await api(
        "POST",
        f"/api/v1/projects/{projects[BETA]['id']}/members",
        {"email": DEV_EMAIL, "allowed_skills": ["python"]},
        bearer=admin_token,
    )
    print("membership beta:", st, member_beta.get("membership", {}).get("status"))
    assert st == 200 and member_beta["membership"]["status"] == "APPROVED"

    st, beta_session = await api("POST", "/api/v1/portal/sessions", {
        "project_id": projects[BETA]["id"],
        "agent_tool": "cursor",
        "llm_provider": "cloud-llm",
        "max_context_tokens": 32000,
    }, bearer=dev_token)
    print("session beta:", st, beta_session.get("project_slug"))
    assert st == 200 and beta_session["project_slug"] == BETA

    st, me_dev = await api("GET", "/api/v1/auth/me", bearer=dev_token)
    print("dev1 me:", [p["project_slug"] for p in me_dev["projects"]], "sesiones:", len(me_dev["sessions"]))
    assert {p["project_slug"] for p in me_dev["projects"]} == {ALPHA, BETA}
    assert {s["project_slug"] for s in me_dev["sessions"]} == {ALPHA, BETA}

    ws_a, hb_a, state_a = await open_agent_ws(alpha_session["session_id"], alpha_session["session_key"])
    ws_b, hb_b, state_b = await open_agent_ws(beta_session["session_id"], beta_session["session_key"])

    print("\n--- 7. Dispatch 1:1 por proyecto ---")
    override = {
        "strategy": "CONSERVATIVE_MINIMAL_FOOTPRINT",
        "allow_dependency_changes": False,
        "estimated_context_per_task": 900,
        "tasks": [{
            "title": "[Impl] Tarea E2E de proyecto",
            "required_skills": ["python"],
            "allowed_files": ["README.md"],
            "allow_dependency_changes": False,
        }],
    }
    plan_tasks = {}
    for slug in (ALPHA, BETA):
        st, plan = await api("POST", "/api/v1/plans/propose", {
            "requested_by": REQUESTED_BY,
            "repo_name": slug,
            "project_id": projects[slug]["id"],
            "requirement_prompt": f"Tarea E2E para {slug}",
            "required_skills_hint": ["python"],
            "plan_a_override": override,
            "plan_b_override": override,
        }, bearer=admin_token)
        print(f"plan {slug}:", st, plan.get("plan_id") if isinstance(plan, dict) else plan)
        assert st == 200 and len(plan["created_tasks"]) == 1
        plan_tasks[slug] = plan["created_tasks"][0]["task_id"]

    st, dispatch = await api("POST", "/api/v1/tasks/dispatch", bearer=admin_token)
    ours = [d for d in (dispatch.get("dispatched") or []) if d["task_id"] in set(plan_tasks.values())]
    print("dispatch:", st, "count:", dispatch.get("count"), "nuestras:", len(ours))
    assert st == 200 and len(ours) == 2
    by_project = {d["project_id"]: d for d in ours}
    assert by_project[projects[ALPHA]["id"]]["assigned_to"]["session_id"] == alpha_session["session_id"]
    assert by_project[projects[BETA]["id"]]["assigned_to"]["session_id"] == beta_session["session_id"]
    print("asignaciones:", {
        ALPHA: by_project[projects[ALPHA]["id"]]["assigned_to"]["session_id"][:8],
        BETA: by_project[projects[BETA]["id"]]["assigned_to"]["session_id"][:8],
    })

    print("\n--- 8. UI responsive /portal y /war-room ---")
    for path in ("/portal", "/war-room"):
        st, html = await api("GET", path)
        assert st == 200 and isinstance(html, str)
        checks = {
            "16in": "@media (min-width: 1200px)",
            "tablet": "@media (max-width: 1024px)",
            "mobile": "@media (max-width: 600px)",
            "login": 'id="loginEmail"',
            "password": 'id="loginPassword"',
            "auth_route": "/api/v1/auth/login",
            "config": "config-bundle",
            "cursor": "cursor_mcp_json",
            "claude": "claudecode_mcp_json",
            "opencode": "opencode_json",
            "hermes": "hermes_cli_env",
            "bridge": "bridge_download_command",
            "graph": 'viewBox="0 0 900 560"',
            "touch": "min-height:44px",
            "drawer": "drawerBackdrop",
        }
        missing = [k for k, needle in checks.items() if needle not in html]
        print(f"{path}: 200 | {len(html)} bytes | faltantes: {missing or 'ninguno'}")
        assert not missing

    hb_a.cancel(); hb_b.cancel()
    await ws_a.close(); await ws_b.close()
    print("\n--- 9. Cleanup ---")
    cleanup()
    counts = db_rows("""
        SELECT 'users' AS k, count(*) AS n FROM harness_users
        UNION ALL SELECT 'projects', count(*) FROM harness_projects
        UNION ALL SELECT 'memberships', count(*) FROM project_memberships
        UNION ALL SELECT 'sessions', count(*) FROM agent_sessions
        UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
        UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
        UNION ALL SELECT 'files', count(*) FROM repo_file_map
    """)
    print("counts finales:", {r["k"]: r["n"] for r in counts})
    print("\nE2E STAGE 7 (A/B/C): OK")


try:
    asyncio.run(main())
finally:
    try:
        cleanup()
    except Exception:
        pass
