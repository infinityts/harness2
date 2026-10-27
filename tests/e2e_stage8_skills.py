"""E2E Etapa 8: catalogo estandar de skills, permisos y revelacion progresiva."""
import asyncio
import json
import os
import pathlib
import shutil
import socketserver
import threading
import time
import urllib.error
import urllib.request
from email import message_from_string

import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS_BASE = BASE.replace("http", "ws", 1)
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = "admin@local"
ADMIN_PASSWORD = "admin"
REPOS = pathlib.Path("/var/lib/harness/repos")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
PROJECT_SLUG = "skills-e2e"
USER_EMAIL = "skills-dev@empresa.com"
USER_PASSWORD = "SkillsDev2026!"
FROM_EMAIL = "harness-bot@controlplane.local"
ORIGINAL_SMTP = None

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 localhost ESMTP\r\n")
        data_mode = False
        data_lines = []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if data_mode:
                if text == ".":
                    self.server.messages.append("\n".join(data_lines))
                    data_lines = []
                    data_mode = False
                    self.wfile.write(b"250 OK\r\n")
                else:
                    data_lines.append(text)
                continue
            upper = text.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                self.wfile.write(b"250-localhost\r\n250 SIZE 10485760\r\n")
            elif upper.startswith("MAIL FROM") or upper.startswith("RCPT TO"):
                self.wfile.write(b"250 OK\r\n")
            elif upper.startswith("DATA"):
                data_mode = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif upper.startswith("QUIT"):
                self.wfile.write(b"221 Bye\r\n")
                break
            elif upper.startswith("RSET") or upper.startswith("NOOP"):
                self.wfile.write(b"250 OK\r\n")
            else:
                self.wfile.write(b"250 OK\r\n")


class MockSmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []


def http(method, path, body=None, bearer=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
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


async def api(method, path, body=None, bearer=None, timeout=60):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def reset_smtp_config():
    db_exec("""
        INSERT INTO system_smtp_config
            (id, host, port, username, password, from_email, use_tls, use_ssl, updated_at)
        VALUES
            (1, 'smtp.gmail.com', 587, NULL, NULL, 'harness-bot@controlplane.local', true, false, NOW())
        ON CONFLICT (id) DO UPDATE SET
            host = 'smtp.gmail.com', port = 587, username = NULL, password = NULL,
            from_email = 'harness-bot@controlplane.local', use_tls = true, use_ssl = false,
            updated_at = NOW()
    """)


def restore_smtp(config):
    if not config:
        reset_smtp_config()
        return
    db_exec("""
        INSERT INTO system_smtp_config
            (id, host, port, username, password, from_email, use_tls, use_ssl, updated_at)
        VALUES
            (1, :host, :port, :username, :password, :from_email, :use_tls, :use_ssl, NOW())
        ON CONFLICT (id) DO UPDATE SET
            host = EXCLUDED.host, port = EXCLUDED.port, username = EXCLUDED.username,
            password = EXCLUDED.password, from_email = EXCLUDED.from_email,
            use_tls = EXCLUDED.use_tls, use_ssl = EXCLUDED.use_ssl, updated_at = NOW()
    """, **config)


def cleanup():
    task_rows = db_rows("""
        SELECT t.id::text AS id
        FROM harness_tasks t
        JOIN feature_consensus_plans p ON p.id = t.plan_id
        WHERE p.requested_by LIKE 'e2e-stage8%'
    """)
    for task in task_rows:
        shutil.rmtree(WORKTREES / task["id"], ignore_errors=True)
    db_exec("""
        DELETE FROM harness_tasks
        WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage8%')
    """)
    db_exec("DELETE FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage8%'")
    db_exec("""
        DELETE FROM harness_skills
        WHERE slug IN ('skills-e2e-helper', 'python-copy')
           OR slug LIKE 'python-copy-%'
    """)
    db_exec("DELETE FROM harness_projects WHERE project_slug = :slug", slug=PROJECT_SLUG)
    db_exec("DELETE FROM harness_users WHERE email = :email", email=USER_EMAIL)
    db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=PROJECT_SLUG)
    db_exec("DELETE FROM email_dispatch_logs WHERE recipient = :email", email=USER_EMAIL)
    shutil.rmtree(REPOS / PROJECT_SLUG, ignore_errors=True)


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


def skill_payload(slug, name, category, trigger, objective, steps, guardrails, output):
    return {
        "slug": slug,
        "display_name": name,
        "category": category,
        "trigger_description": trigger,
        "objective": objective,
        "workflow_steps": steps,
        "guardrails_forbidden": guardrails,
        "output_criteria": output,
        "is_official": False,
        "visibility": "PRIVATE",
        "status": "PUBLISHED",
    }


async def main():
    global ORIGINAL_SMTP
    print("== ETAPA 8 - SKILLS ==")
    current = db_rows("""
        SELECT host, port, username, password, from_email, use_tls, use_ssl
        FROM system_smtp_config WHERE id = 1
    """)
    ORIGINAL_SMTP = dict(current[0]) if current else None
    if ORIGINAL_SMTP and ORIGINAL_SMTP["host"] == "127.0.0.1":
        reset_smtp_config()
        ORIGINAL_SMTP = dict(db_rows("""
            SELECT host, port, username, password, from_email, use_tls, use_ssl
            FROM system_smtp_config WHERE id = 1
        """)[0])
    cleanup()

    print("\n--- 1. Migracion 0009, seeds y login ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    seeds = db_rows("SELECT slug, category, is_official, compiled_tokens FROM harness_skills WHERE is_official = true ORDER BY slug")
    print("alembic:", version, "| seeds:", [(r["slug"], r["compiled_tokens"]) for r in seeds])
    assert version == "0009" or version > "0009"
    assert {"python", "fastapi", "ui-responsive", "db-alembic", "doc-analyzer", "tech-writer"}.issubset({r["slug"] for r in seeds})
    assert all(r["compiled_tokens"] <= 500 for r in seeds)

    st, admin_login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert st == 200
    admin_token = admin_login["access_token"]

    server = MockSmtpServer(("127.0.0.1", 0), SmtpHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    st, cfg = await api("PUT", "/api/v1/admin/smtp-config", {
        "host": "127.0.0.1", "port": port, "username": None, "password": "",
        "from_email": FROM_EMAIL, "use_tls": False, "use_ssl": False,
    }, bearer=admin_token)
    assert st == 200

    try:
        print("\n--- 2. Proyecto y usuario sin skills ---")
        st, created = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT_SLUG,
            "display_name": "Skills E2E",
            "description": "Catalogo de skills y revelacion progresiva",
            "default_branch": "main",
        }, bearer=admin_token)
        assert st == 200
        project = created["project"]

        st, new_user = await api("POST", "/api/v1/admin/users", {
            "email": USER_EMAIL, "full_name": "Skills Dev", "password": USER_PASSWORD,
        }, bearer=admin_token)
        assert st == 200
        user_id = new_user["user"]["id"]

        st, user_login = await api("POST", "/api/v1/auth/login", {"email": USER_EMAIL, "password": USER_PASSWORD})
        assert st == 200
        user_token = user_login["access_token"]

        print("\n--- 3. Catalogo oficial y permisos iniciales ---")
        st, catalog = await api("GET", "/api/v1/skills", bearer=user_token)
        assert st == 200
        python_skill = next(s for s in catalog["skills"] if s["slug"] == "python")
        print("python summary:", {k: python_skill[k] for k in ("is_official", "can_edit", "can_clone", "is_read_only")})
        assert python_skill["is_official"] is True and python_skill["can_edit"] is False
        assert python_skill["can_clone"] is False and python_skill["is_read_only"] is True
        assert catalog["viewer_capabilities"]["can_create_skills"] is False

        st, detail = await api("GET", "/api/v1/skills/python", bearer=user_token)
        assert st == 200 and "Mandatory Steps" in detail["compiled_skill_md"]
        assert detail["compiled_tokens"] <= 500

        st, denied_create = await api("POST", "/api/v1/skills", skill_payload(
            "skills-e2e-helper", "Helper E2E", "backend", "Usa esta skill para pruebas del catalogo.",
            "Validar el catalogo de skills end to end.",
            ["Paso uno de prueba.", "Paso dos de prueba.", "Paso tres de prueba."],
            ["No inventar resultados.", "No omitir evidencia."],
            "Entregar evidencia verificable.",
        ), bearer=user_token)
        print("create sin permiso:", st, denied_create)
        assert st == 403

        st, denied_clone = await api("POST", "/api/v1/skills/python/clone", bearer=user_token)
        print("clone sin permiso:", st)
        assert st == 403

        print("\n--- 4. Permisos y creacion privada ---")
        st, caps = await api("PUT", f"/api/v1/admin/users/{user_id}/skill-capabilities", {
            "can_create_skills": True, "can_clone_skills": True,
            "can_edit_own_skills": True, "can_grant_skills": False,
        }, bearer=admin_token)
        assert st == 200 and caps["capabilities"]["can_create_skills"] is True
        st, users = await api("GET", "/api/v1/admin/users?search=skills-dev", bearer=admin_token)
        found = next(u for u in users["users"] if u["email"] == USER_EMAIL)
        assert found["skill_capabilities"]["can_clone_skills"] is True

        st, draft = await api("POST", "/api/v1/skills/draft-from-idea", {
            "raw_idea": "Revisar facturas y extraer el total y la fecha sin inventar datos",
            "category": "documents",
        }, bearer=user_token)
        print("draft:", st, draft.get("provider"), draft.get("slug"), len(draft.get("workflow_steps", [])))
        assert st == 200 and draft["provider"] == "deterministic"
        assert 3 <= len(draft["workflow_steps"]) <= 5
        assert "NEVER:" in draft["compiled_skill_md"]

        st, preview = await api("POST", "/api/v1/skills/preview", {
            k: draft[k] for k in (
                "display_name", "category", "trigger_description", "objective",
                "workflow_steps", "guardrails_forbidden", "output_criteria",
            )
        }, bearer=user_token)
        assert st == 200 and preview["compiled_tokens"] <= 500

        helper_payload = skill_payload(
            "skills-e2e-helper", "Helper E2E", "backend",
            "Usa esta skill cuando la tarea requiera validar el catalogo end to end.",
            "Validar el catalogo de skills, permisos y revelacion progresiva.",
            ["Leer el contrato de la tarea.", "Ejecutar el paso verificable.", "Registrar la evidencia."],
            ["No inventar resultados.", "No omitir la evidencia."],
            "Entregar evidencia y resultado verificable.",
        )
        st, helper = await api("POST", "/api/v1/skills", helper_payload, bearer=user_token)
        print("create private:", st, helper.get("slug"), helper.get("version"))
        assert st == 200 and helper["slug"] == "skills-e2e-helper" and helper["is_official"] is False
        assert helper["compiled_tokens"] <= 500

        helper_payload["objective"] = "Validar el catalogo y sus permisos con evidencia reproducible."
        st, edited = await api("PUT", "/api/v1/skills/skills-e2e-helper", helper_payload, bearer=user_token)
        assert st == 200 and edited["version"] == 2

        st, denied_official = await api("PUT", "/api/v1/skills/python", {
            "display_name": "Python hackeado", "category": "backend",
            "trigger_description": "Intento no autorizado de editar una skill oficial.",
            "objective": "No deberia permitirse a un ingeniero.",
            "workflow_steps": ["Uno.", "Dos.", "Tres."],
            "guardrails_forbidden": ["No hacerlo."],
            "output_criteria": "No aplica.",
        }, bearer=user_token)
        print("edit official:", st, denied_official)
        assert st == 403

        st, clone = await api("POST", "/api/v1/skills/python/clone", bearer=user_token)
        print("clone official:", st, clone.get("slug"), clone.get("is_official"))
        assert st == 200 and clone["is_official"] is False
        assert clone["owner_user_id"] == user_id
        assert f"skill: {clone['slug']}" in clone["compiled_skill_md"]

        print("\n--- 5. Asignacion a proyecto/usuario y sesion ---")
        st, _ = await api("PUT", f"/api/v1/projects/{project['id']}/skills", {
            "skill_slugs": ["python", "tech-writer", "skills-e2e-helper"],
        }, bearer=admin_token)
        assert st == 200
        st, member = await api("POST", f"/api/v1/projects/{project['id']}/members", {
            "user_id": user_id,
            "allowed_skills": ["python", "tech-writer", "skills-e2e-helper"],
        }, bearer=admin_token)
        print("membership skills:", st, member.get("membership", {}).get("status"))
        assert st == 200 and member["membership"]["status"] == "APPROVED"

        st, membership_skills = await api("GET", f"/api/v1/projects/{project['id']}/members/{user_id}/skills", bearer=user_token)
        slugs = {s["slug"] for s in membership_skills["skills"]}
        print("membership skills:", sorted(slugs))
        assert {"python", "tech-writer", "skills-e2e-helper"}.issubset(slugs)

        st, session = await api("POST", "/api/v1/portal/sessions", {
            "project_id": project["id"], "agent_tool": "opencode",
            "llm_provider": "local-llm", "max_context_tokens": 32000,
        }, bearer=user_token)
        print("session skills:", session.get("skills"))
        assert st == 200 and {"python", "tech-writer", "skills-e2e-helper"}.issubset(set(session["skills"]))
        ws, hb, ws_state = await open_agent_ws(session["session_id"], session["session_key"])

        try:
            print("\n--- 6. Tarea, manuales activos y MCP ---")
            override = {
                "strategy": "CONSERVATIVE_MINIMAL_FOOTPRINT",
                "allow_dependency_changes": False,
                "estimated_context_per_task": 900,
                "tasks": [{
                    "title": "[Impl] Tarea con skill python",
                    "required_skills": ["python"],
                    "allowed_files": ["README.md"],
                    "allow_dependency_changes": False,
                }],
            }
            st, plan = await api("POST", "/api/v1/plans/propose", {
                "requested_by": "e2e-stage8",
                "repo_name": PROJECT_SLUG,
                "project_id": project["id"],
                "requirement_prompt": "Tarea E2E que requiere la skill python",
                "required_skills_hint": ["python"],
                "plan_a_override": override,
                "plan_b_override": override,
            }, bearer=admin_token)
            assert st == 200
            st, dispatch = await api("POST", "/api/v1/tasks/dispatch", bearer=admin_token)
            print("dispatch:", st, dispatch.get("count"))
            assert st == 200 and dispatch["count"] == 1
            assert dispatch["dispatched"][0]["assigned_to"]["session_id"] == session["session_id"]
            assert "python" in dispatch["dispatched"][0]["active_task_skill_manuals"]

            st, tasks = await api("GET", "/api/v1/tasks", bearer=session["session_key"])
            assert st == 200 and tasks["tasks"]
            active_task = tasks["tasks"][0]
            print("task manuals:", list(active_task.get("skill_manuals", {}).keys()))
            assert "python" in active_task["skill_manuals"]
            assert "Mandatory Steps" in active_task["skill_manuals"]["python"]["compiled_skill_md"]

            st, manual = await api("GET", "/api/v1/skills/python/manual", bearer=session["session_key"])
            print("manual python:", st, manual.get("compiled_tokens"))
            assert st == 200 and "NEVER:" in manual["compiled_skill_md"]

            st, denied_manual = await api("GET", "/api/v1/skills/ui-responsive/manual", bearer=session["session_key"])
            print("manual no asignado:", st)
            assert st == 403
        finally:
            hb.cancel()
            await ws.close()

        print("\n--- 7. UI Skill Studio y filtros ---")
        st, ui = await api("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in (
            "viewSkills", "newSkillBtn", "skillsGrid", "openSkillEditor",
            "assignProjectSearch", "assignSkillSearch", "projectSearch",
            "harness_get_skill_manual", "Skill Studio",
        ):
            assert marker in ui, f"UI sin marcador: {marker}"
        print("portal skills UI:", st, len(ui), "bytes")

        print("\n--- 8. Cleanup ---")
        cleanup()
        counts = db_rows("""
            SELECT 'skills' AS k, count(*) AS n FROM harness_skills
            UNION ALL SELECT 'projects', count(*) FROM harness_projects
            UNION ALL SELECT 'sessions', count(*) FROM agent_sessions
            UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
            UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
            UNION ALL SELECT 'email_logs', count(*) FROM email_dispatch_logs
        """)
        print("counts finales:", {r["k"]: r["n"] for r in counts})
        print("\nE2E STAGE 8 SKILLS: OK")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


try:
    asyncio.run(main())
finally:
    try:
        cleanup()
    except Exception:
        pass
    try:
        restore_smtp(ORIGINAL_SMTP)
    except Exception:
        pass
