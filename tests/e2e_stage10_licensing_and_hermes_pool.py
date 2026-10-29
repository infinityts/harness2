"""E2E Etapa 10: licencias por plan, pool Hermes y RAG.

Requiere el contenedor `harness-hermes-worker` conectado a Harness2.
"""
import asyncio
import json
import os
import pathlib
import select
import shutil
import socketserver
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid

import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS_BASE = BASE.replace("http", "ws", 1)
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
LICENSE_EMAIL = os.environ.get("HARNESS_E2E_LICENSE_OPERATOR_EMAIL", "license-operator@harness.local")
LICENSE_PASSWORD = os.environ.get("HARNESS_E2E_LICENSE_OPERATOR_PASSWORD", "")
LICENSE_TOTP_SECRET = os.environ.get("HARNESS_E2E_LICENSE_OPERATOR_TOTP_SECRET", "")
REPOS = pathlib.Path("/var/lib/harness/repos")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
BRIDGE = "/app/client/harness_mcp_bridge.py"
LICENSE_DIR = pathlib.Path("/var/lib/harness/license")
TEST_LICENSE_DIR = pathlib.Path("/var/lib/harness/license-test")

for _candidate in ("/app", str(pathlib.Path(__file__).resolve().parents[1] / "app")):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)
from src import totp as totp_lib

PROJECT_A = "stage10-lic-a"
PROJECT_B = "stage10-lic-b"
PROJECT_C = "stage10-lic-c"
USER_PREFIX = "stage10-eng"
PASSWORD = "Stage10Eng2026!"
FROM_EMAIL = "harness-bot@controlplane.local"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
ORIGINAL_SMTP = None
ORIGINAL_LICENSE = None
ORIGINAL_SLOTS = {}
WS_TASKS = []


def http(method, path, body=None, bearer=None, timeout=300):
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


async def api(method, path, body=None, bearer=None, timeout=300):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


async def login_license_operator():
    if not LICENSE_PASSWORD or not LICENSE_TOTP_SECRET:
        raise AssertionError(
            "HARNESS_E2E_LICENSE_OPERATOR_PASSWORD and HARNESS_E2E_LICENSE_OPERATOR_TOTP_SECRET are required"
        )
    # Los E2E pueden correr en el mismo paso TOTP; se limpia el anti-replay solo en test.
    db_exec(
        """UPDATE harness_users
           SET mfa_last_used_step = NULL, failed_login_attempts = 0, locked_until = NULL
           WHERE email = :email""",
        email=LICENSE_EMAIL,
    )
    st, body = await api("POST", "/api/v1/auth/login", {
        "email": LICENSE_EMAIL,
        "password": LICENSE_PASSWORD,
        "totp_code": totp_lib.code(LICENSE_TOTP_SECRET),
    })
    assert st == 200, body
    assert body["user"]["role"] == "LICENSE_ADMIN", body
    return body["access_token"]


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
                self.wfile.write(b"354 End data\r\n")
            elif upper.startswith("QUIT"):
                self.wfile.write(b"221 Bye\r\n")
                break
            else:
                self.wfile.write(b"250 OK\r\n")


class MockSmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []


def save_state():
    global ORIGINAL_SMTP, ORIGINAL_LICENSE, ORIGINAL_SLOTS
    rows = db_rows("""
        SELECT host, port, username, password, from_email, use_tls, use_ssl
        FROM system_smtp_config WHERE id = 1
    """)
    ORIGINAL_SMTP = dict(rows[0]) if rows else None
    ORIGINAL_SLOTS = {
        row["id"]: row["is_active_slot"]
        for row in db_rows("SELECT id::text, project_slug, is_active_slot FROM harness_projects")
    }


def restore_smtp():
    if not ORIGINAL_SMTP:
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
    """, **ORIGINAL_SMTP)


def cleanup():
    for slug in (PROJECT_A, PROJECT_B, PROJECT_C):
        db_exec("DELETE FROM harness_projects WHERE project_slug = :slug", slug=slug)
        db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=slug)
        shutil.rmtree(REPOS / slug, ignore_errors=True)
        shutil.rmtree(WORKTREES / slug, ignore_errors=True)
    db_exec("DELETE FROM harness_users WHERE email LIKE :pattern", pattern=USER_PREFIX + "%@example.com")
    db_exec("DELETE FROM email_dispatch_logs WHERE recipient LIKE :pattern", pattern=USER_PREFIX + "%@example.com")


def close_ws():
    async def _close():
        for ws, task in WS_TASKS:
            task.cancel()
            try:
                await ws.close()
            except Exception:
                pass
        WS_TASKS.clear()
    return _close()


def activate_test_license(token):
    test_license = TEST_LICENSE_DIR / "test-all-plans.json"
    if not test_license.exists():
        return False
    shutil.copyfile(test_license, LICENSE_DIR / "license.json")
    st, data = http("POST", "/api/v1/admin/license/reload", None, bearer=token)
    assert st == 200, data
    assert data["signed_license"]["status"] == "VALID", data["signed_license"]
    assert len(data["signed_license"]["allowed_plans"]) >= 5
    return True


def restore_production_license(token):
    production = TEST_LICENSE_DIR / "license.production.json"
    if not production.exists():
        return
    shutil.copyfile(production, LICENSE_DIR / "license.json")
    http("POST", "/api/v1/admin/license/reload", None, bearer=token)


async def open_agent_ws(session_id, session_key):
    ws = await websockets.connect(
        f"{WS_BASE}/ws/agent/{session_id}",
        additional_headers={"Authorization": f"Bearer {session_key}"},
    )

    async def heartbeat():
        try:
            while True:
                await ws.send(json.dumps({"current_context_tokens": 12, "status": "ONLINE"}))
                await asyncio.sleep(3)
        except Exception:
            pass

    task = asyncio.create_task(heartbeat())
    WS_TASKS.append((ws, task))
    await asyncio.sleep(0.3)
    return ws


def write_project_files(slug):
    repo = REPOS / slug
    docs = repo / "docs"
    src = repo / "src"
    docs.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text(
        "# Stage 10 Licensing\n\n## Objetivo\n\nProyecto de prueba de licencias y pool Hermes.\n",
        encoding="utf-8",
    )
    (docs / "architecture.md").write_text(
        "# Arquitectura del modulo de facturacion\n\n"
        "## Componentes\n\nEl servicio billing calcula totales e impuestos.\n\n"
        "## Flujo\n\n1. Recibe la factura.\n2. Calcula el total.\n3. Persiste el resultado.\n",
        encoding="utf-8",
    )
    (src / "billing.py").write_text(
        '"""Servicio de facturacion de prueba."""\n\n\n'
        "def calculate_invoice_total(subtotal: float, tax_rate: float) -> float:\n"
        '    """Calcula el total de una factura aplicando impuestos."""\n'
        "    return round(subtotal * (1 + tax_rate), 2)\n",
        encoding="utf-8",
    )


def mcp_send(proc, obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def mcp_read(proc, timeout=60):
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise TimeoutError("MCP bridge no respondio")
    line = proc.stdout.readline()
    if not line:
        err = proc.stderr.read() if proc.poll() is not None else ""
        raise RuntimeError(f"bridge cerro stdout: {err[-1200:]}")
    return json.loads(line)


def mcp_call(proc, req_id, name, arguments):
    mcp_send(proc, {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
    resp = mcp_read(proc)
    if "error" in resp:
        raise RuntimeError(f"MCP error: {resp['error']}")
    content = resp["result"]["content"][0]["text"]
    return json.loads(content), bool(resp["result"].get("isError"))


def mcp_session(session_id, session_key):
    env = dict(os.environ)
    env.update({
        "HARNESS_URL": BASE,
        "HARNESS_SESSION_ID": session_id,
        "HARNESS_SESSION_KEY": session_key,
        "HARNESS_HEARTBEAT_SECONDS": "5",
    })
    proc = subprocess.Popen(
        [sys.executable, BRIDGE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )
    mcp_send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    init = mcp_read(proc)
    assert init["result"]["serverInfo"]["name"] == "harness-mcp-bridge"
    mcp_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    mcp_send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = mcp_read(proc)["result"]["tools"]
    return proc, {t["name"] for t in tools}


def wait_for_hermes_online(project_id, timeout=60):
    with engine.connect() as conn:
        for _ in range(timeout):
            row = conn.execute(
                sa.text("""
                    SELECT status FROM agent_sessions
                    WHERE project_id = CAST(:pid AS UUID)
                      AND session_kind = 'SYSTEM_WORKER'
                      AND agent_tool = 'hermes'
                      AND status <> 'REVOKED'
                    LIMIT 1
                """),
                {"pid": project_id},
            ).fetchone()
            if row and row[0] in ("ONLINE", "BUSY"):
                return row[0]
            import time
            time.sleep(1)
    return None


async def main():
    global ORIGINAL_LICENSE
    print("== ETAPA 10 - LICENCIAS + POOL HERMES ==")
    save_state()
    cleanup()

    st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert st == 200, login
    admin = login["access_token"]
    license_token = await login_license_operator()

    st, original_license = await api("GET", "/api/v1/admin/license", bearer=admin)
    assert st == 200
    ORIGINAL_LICENSE = original_license
    st, projects = await api("GET", "/api/v1/projects", bearer=admin)
    assert st == 200
    harness_self = next(p for p in projects["projects"] if p["project_slug"] == "harness-self")

    server = MockSmtpServer(("127.0.0.1", 0), SmtpHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    st, _ = await api("PUT", "/api/v1/admin/smtp-config", {
        "host": "127.0.0.1", "port": port, "username": None, "password": "",
        "from_email": FROM_EMAIL, "use_tls": False, "use_ssl": False,
    }, bearer=admin)
    assert st == 200

    try:
        print("\n--- 1. Plan ENTERPRISE_10 y usuarios de prueba ---")
        assert activate_test_license(license_token), "test-all-plans license not available"
        st, lic = await api("PUT", "/api/v1/admin/license", {"plan_code": "ENTERPRISE_10"}, bearer=license_token)
        assert st == 200 and lic["plan_code"] == "ENTERPRISE_10"

        users = []
        for index in range(1, 5):
            email = f"{USER_PREFIX}{index}@example.com"
            st, created = await api("POST", "/api/v1/admin/users", {
                "email": email, "full_name": f"Stage10 Engineer {index}", "password": PASSWORD,
            }, bearer=admin)
            assert st == 200, created
            users.append(created["user"])
            st, member = await api("POST", f"/api/v1/projects/{harness_self['id']}/members", {
                "user_id": created["user"]["id"], "allowed_skills": [],
            }, bearer=admin)
            assert st == 200, member
        print("usuarios creados:", [u["email"] for u in users])

        st, license_now = await api("GET", "/api/v1/admin/license", bearer=admin)
        assert st == 200
        assert license_now["limits"]["max_registered_users"] == 30

        print("\n--- 2. PERSONAL_1: 1 proyecto, 1 ingeniero ---")
        st, lic = await api("PUT", "/api/v1/admin/license", {"plan_code": "PERSONAL_1"}, bearer=license_token)
        print("plan:", st, lic.get("plan_code"), lic.get("limits"))
        assert st == 200 and lic["plan_code"] == "PERSONAL_1"

        st, session1 = await api("POST", "/api/v1/portal/sessions", {
            "project_id": harness_self["id"], "agent_tool": "opencode",
            "llm_provider": "local-llm", "max_context_tokens": 32000,
        }, bearer=(await api("POST", "/api/v1/auth/login", {"email": users[0]["email"], "password": PASSWORD}))[1]["access_token"])
        print("session engineer 1:", st)
        assert st == 200, session1
        await open_agent_ws(session1["session_id"], session1["session_key"])

        login2 = await api("POST", "/api/v1/auth/login", {"email": users[1]["email"], "password": PASSWORD})
        st, denied_eng = await api("POST", "/api/v1/portal/sessions", {
            "project_id": harness_self["id"], "agent_tool": "opencode",
            "llm_provider": "local-llm", "max_context_tokens": 32000,
        }, bearer=login2[1]["access_token"])
        print("segundo ingeniero:", st, denied_eng)
        assert st == 403 and "engineers in project (1/1)" in json.dumps(denied_eng)

        st, denied_project = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT_A, "display_name": "Stage10 A",
            "description": "Proyecto A de licencias", "default_branch": "main",
        }, bearer=admin)
        print("segundo proyecto PERSONAL_1:", st, denied_project)
        assert st == 403 and "active projects (1/1)" in json.dumps(denied_project)

        print("\n--- 3. TEAM_3: 3 proyectos y 3 workers Hermes ---")
        st, lic = await api("PUT", "/api/v1/admin/license", {"plan_code": "TEAM_3"}, bearer=license_token)
        assert st == 200 and lic["limits"]["max_engineers_per_project"] == 3

        st, created_a = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT_A, "display_name": "Stage10 A",
            "description": "Proyecto A de licencias", "default_branch": "main",
        }, bearer=admin)
        assert st == 200, created_a
        project_a = created_a["project"]
        write_project_files(PROJECT_A)
        st, indexed = await api("POST", "/api/v1/repos/index", {"repo_name": PROJECT_A, "branch": "main"}, bearer=admin)
        assert st == 200, indexed

        st, created_b = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT_B, "display_name": "Stage10 B",
            "description": "Proyecto B de licencias", "default_branch": "main",
        }, bearer=admin)
        assert st == 200, created_b
        project_b = created_b["project"]

        for project in (harness_self, project_a, project_b):
            status = await asyncio.to_thread(wait_for_hermes_online, project["id"], 80)
            print("hermes online", project["project_slug"], status)
            assert status in ("ONLINE", "BUSY")

        st, license_now = await api("GET", "/api/v1/admin/license", bearer=admin)
        print("usage TEAM_3:", license_now["usage"])
        assert license_now["usage"]["hermes_workers_online"] == 3

        st, denied_project = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT_C, "display_name": "Stage10 C",
            "description": "Proyecto C de licencias", "default_branch": "main",
        }, bearer=admin)
        print("cuarto proyecto TEAM_3:", st, denied_project)
        assert st == 403 and "active projects (3/3)" in json.dumps(denied_project)

        print("\n--- 4. Toggle slot y cuotas de ingenieros ---")
        st, toggled_off = await api("POST", f"/api/v1/projects/{project_b['id']}/toggle-slot",
                                    {"active": False}, bearer=admin)
        print("toggle off:", st, toggled_off.get("project"))
        assert st == 200 and toggled_off["project"]["is_active_slot"] is False
        st, toggled_on = await api("POST", f"/api/v1/projects/{project_b['id']}/toggle-slot",
                                   {"active": True}, bearer=admin)
        print("toggle on:", st, toggled_on.get("project"))
        assert st == 200 and toggled_on["project"]["is_active_slot"] is True
        await asyncio.to_thread(wait_for_hermes_online, project_b["id"], 80)

        st, _ = await api("PUT", f"/api/v1/projects/{project_a['id']}/skills", {
            "skill_slugs": ["python", "tech-writer", "rag-docs-curator", "doc-analyzer"],
        }, bearer=admin)
        assert st == 200
        for user in users:
            st, member = await api("POST", f"/api/v1/projects/{project_a['id']}/members", {
                "user_id": user["id"],
                "allowed_skills": ["python", "tech-writer", "rag-docs-curator"],
            }, bearer=admin)
            assert st == 200, member

        engineer_sessions = []
        for user in users[:3]:
            login_user = await api("POST", "/api/v1/auth/login", {"email": user["email"], "password": PASSWORD})
            st, session = await api("POST", "/api/v1/portal/sessions", {
                "project_id": project_a["id"], "agent_tool": "opencode",
                "llm_provider": "local-llm", "max_context_tokens": 32000,
            }, bearer=login_user[1]["access_token"])
            print("engineer session", user["email"], st)
            assert st == 200, session
            engineer_sessions.append(session)
            await open_agent_ws(session["session_id"], session["session_key"])

        login4 = await api("POST", "/api/v1/auth/login", {"email": users[3]["email"], "password": PASSWORD})
        st, denied_eng = await api("POST", "/api/v1/portal/sessions", {
            "project_id": project_a["id"], "agent_tool": "opencode",
            "llm_provider": "local-llm", "max_context_tokens": 32000,
        }, bearer=login4[1]["access_token"])
        print("cuarto ingeniero:", st, denied_eng)
        assert st == 403 and "engineers in project (3/3)" in json.dumps(denied_eng)

        print("\n--- 5. RAG bootstrap con worker Hermes ---")
        st, boot = await api("POST", f"/api/v1/projects/{project_a['id']}/documents/bootstrap?wait=true&timeout=300",
                             bearer=admin, timeout=330)
        print("bootstrap:", st, {k: boot.get(k) for k in ("status", "documents_count", "scanned", "created", "updated", "skipped")})
        assert st == 200 and boot["documents_count"] >= 5

        st, docs = await api("GET", f"/api/v1/projects/{project_a['id']}/documents", bearer=admin)
        sources = {d["source_uri"] for d in docs["documents"]}
        print("fuentes:", sorted(sources))
        assert "auto://hermes-architecture.md" in sources
        assert "repo://docs/architecture.md" in sources

        st, sessions = await api("GET", "/api/v1/sessions", bearer=admin)
        worker_a = next(s for s in sessions["sessions"] if s.get("project_id") == project_a["id"]
                        and s.get("worker_role") == "RAG_CURATOR")
        print("worker A:", worker_a["status"], worker_a["current_context_tokens"])
        assert worker_a["status"] == "ONLINE" and worker_a["current_context_tokens"] == 0

        print("\n--- 6. Secret scrubber, idempotencia y aislamiento ---")
        session_a = engineer_sessions[0]
        source = "manual://stage10-secret.md"
        secret_content = (
            "# Credenciales de prueba\n\n"
            "postgresql://harness:SuperSecret123@db.local:5432/harness_core\n\n"
            'api_key = "sk-live-ABC123456789"\n\n'
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.firma\n"
        )
        st, ingested = await api("POST", f"/api/v1/projects/{project_a['id']}/documents", {
            "source_uri": source, "title": "Credenciales Stage10",
            "doc_type": "OTHER", "content": secret_content,
        }, bearer=session_a["session_key"])
        assert st == 200, ingested
        st, again = await api("POST", f"/api/v1/projects/{project_a['id']}/documents", {
            "source_uri": source, "title": "Credenciales Stage10",
            "doc_type": "OTHER", "content": secret_content,
        }, bearer=session_a["session_key"])
        assert st == 200 and again["status"] == "SKIPPED"

        raw = db_rows("""
            SELECT raw_content FROM harness_documents
            WHERE project_id = CAST(:pid AS UUID) AND source_uri = :source
        """, pid=project_a["id"], source=source)
        assert raw and "[REDACTED_SECRET]" in raw[0]["raw_content"]
        for leaked in ("SuperSecret123", "sk-live-ABC123456789", "eyJhbGciOiJIUzI1NiJ9"):
            assert leaked not in raw[0]["raw_content"]

        st, denied = await api("POST", f"/api/v1/projects/{harness_self['id']}/documents", {
            "source_uri": "manual://cross.md", "title": "Cross",
            "doc_type": "OTHER", "content": "# cross",
        }, bearer=session_a["session_key"])
        print("cross project:", st)
        assert st == 403 and "Cross-Project Isolation Violation" in json.dumps(denied)

        print("\n--- 7. Context Pack hibrido ---")
        st, pack = await api("POST", "/api/v1/context/pack", {
            "repo_name": PROJECT_A,
            "query": "calculate_invoice_total facturacion impuestos",
            "token_budget": 2000,
            "top_k": 6,
            "include_docs": True,
        }, bearer=session_a["session_key"])
        print("context pack:", st, {
            "estimated": pack.get("estimated_tokens_used"),
            "docs_tokens": pack.get("documentation_tokens_used"),
            "ast": len(pack.get("surgical_ast_chunks") or []),
            "docs": len(pack.get("relevant_doc_chunks") or []),
        })
        assert st == 200
        assert pack["estimated_tokens_used"] <= 2000
        assert pack["surgical_ast_chunks"] and pack["relevant_doc_chunks"]
        assert any("billing.py" in c["file_path"] for c in pack["surgical_ast_chunks"])

        print("\n--- 8. MCP stdio: 10 tools y RAG ---")
        proc, names = mcp_session(session_a["session_id"], session_a["session_key"])
        try:
            print("tools:", sorted(names))
            assert len(names) == 14
            assert {
                "harness_rag_ingest", "harness_rag_search", "harness_rag_list",
                "harness_get_task_contract", "harness_checkpoint_task",
                "harness_resume_task", "harness_report_context_usage",
            }.issubset(names)
            listed, err = mcp_call(proc, 3, "harness_rag_list", {})
            assert not err and any(d["source_uri"] == source for d in listed["documents"])
            found, err = mcp_call(proc, 4, "harness_rag_search", {"query": "facturacion impuestos", "limit": 5})
            assert not err and found["chunks"]
            ingested_mcp, err = mcp_call(proc, 5, "harness_rag_ingest", {
                "source_uri": "manual://stage10-mcp.md",
                "title": "Ingesta MCP Stage10",
                "doc_type": "OTHER",
                "content": "# MCP\n\nharness_rag_ingest funciona en Stage10.",
            })
            assert not err and ingested_mcp["status"] in ("CREATED", "UPDATED")
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        print("\n--- 9. MAX_20 ---")
        st, lic = await api("PUT", "/api/v1/admin/license", {"plan_code": "MAX_20"}, bearer=license_token)
        print("MAX_20:", st, lic.get("limits"))
        assert st == 200 and lic["limits"]["max_engineers_per_project"] == 20
        assert lic["limits"]["max_total_engineer_sessions"] == 60

        print("\n--- 10. UI de licencia y RAG ---")
        st, ui = await api("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("viewLicense", "licensePlan", "licencia y pool", "Hermes", "viewKnowledge"):
            assert marker.lower() in ui.lower(), f"UI sin marcador: {marker}"
        print("portal:", len(ui), "bytes")

        print("\nE2E STAGE 10: OK")
    finally:
        await close_ws()
        cleanup()
        try:
            restore_production_license(license_token)
            st, _ = await api("PUT", "/api/v1/admin/license", {
                "plan_code": ORIGINAL_LICENSE["plan_code"],
                **ORIGINAL_LICENSE["limits"],
            }, bearer=license_token)
            print("licencia restaurada:", st)
        except Exception as exc:
            print("no se pudo restaurar licencia:", exc)
        for project_id, active in ORIGINAL_SLOTS.items():
            db_exec("UPDATE harness_projects SET is_active_slot = :active WHERE id = CAST(:pid AS UUID)",
                    active=active, pid=project_id)
        restore_smtp()
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
        restore_smtp()
    except Exception:
        pass
