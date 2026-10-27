"""E2E Etapa 7 UX: alta de usuario sin proyecto y asignacion explicita."""
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

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = "admin@local"
ADMIN_PASSWORD = "admin"
REPOS = pathlib.Path("/var/lib/harness/repos")
PROJECT_SLUG = "users-e2e"
USER_EMAIL = "ux-user@empresa.com"
USER_PASSWORD = "UxUser2026!"
GEN_EMAIL = "ux-generated@empresa.com"
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


def decoded_smtp_bodies(server) -> list:
    bodies = []
    for raw in server.messages:
        msg = message_from_string(raw)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                bodies.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return bodies


def http(method, path, body=None, bearer=None, timeout=30):
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
    db_exec("DELETE FROM harness_projects WHERE project_slug = :slug", slug=PROJECT_SLUG)
    db_exec("DELETE FROM harness_users WHERE email IN (:a, :b)", a=USER_EMAIL, b=GEN_EMAIL)
    db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=PROJECT_SLUG)
    db_exec("DELETE FROM email_dispatch_logs WHERE recipient IN (:a, :b)", a=USER_EMAIL, b=GEN_EMAIL)
    shutil.rmtree(REPOS / PROJECT_SLUG, ignore_errors=True)


def main():
    global ORIGINAL_SMTP
    print("== ETAPA 7 - UX USUARIOS ==")

    current_rows = db_rows("""
        SELECT host, port, username, password, from_email, use_tls, use_ssl
        FROM system_smtp_config WHERE id = 1
    """)
    ORIGINAL_SMTP = dict(current_rows[0]) if current_rows else None
    if ORIGINAL_SMTP and ORIGINAL_SMTP["host"] == "127.0.0.1":
        reset_smtp_config()
        ORIGINAL_SMTP = dict(db_rows("""
            SELECT host, port, username, password, from_email, use_tls, use_ssl
            FROM system_smtp_config WHERE id = 1
        """)[0])
    cleanup()

    st, login = http("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    print("admin login:", st)
    assert st == 200
    admin_token = login["access_token"]

    server = MockSmtpServer(("127.0.0.1", 0), SmtpHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    st, cfg = http("PUT", "/api/v1/admin/smtp-config", {
        "host": "127.0.0.1", "port": port, "username": None, "password": "",
        "from_email": FROM_EMAIL, "use_tls": False, "use_ssl": False,
    }, bearer=admin_token)
    assert st == 200

    try:
        print("\n--- 1. UI didactica ---")
        st, ui = http("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("newUserBtn", "usersList", "openCreateUserModal", "Asignar proyecto", "Sin proyectos asignados"):
            assert marker in ui, f"portal sin marcador UX: {marker}"
        print("portal UX UI:", st, len(ui), "bytes")

        print("\n--- 2. Crear usuario SIN proyecto ---")
        st, created = http("POST", "/api/v1/admin/users", {
            "email": USER_EMAIL, "full_name": "UX User", "password": USER_PASSWORD,
        }, bearer=admin_token)
        print("create user:", st, {k: created.get(k) for k in ("generated", "email_sent")})
        assert st == 200 and created["generated"] is False and created["email_sent"] is True
        user_id = created["user"]["id"]
        assert "new_password" not in created and "password" not in created

        st, duplicate = http("POST", "/api/v1/admin/users", {
            "email": USER_EMAIL, "full_name": "UX User Dup", "password": USER_PASSWORD,
        }, bearer=admin_token)
        print("duplicate:", st, duplicate)
        assert st == 409

        listed = db_rows("""
            SELECT u.id::text, u.email, u.status,
                   count(m.id) FILTER (WHERE m.status = 'APPROVED') AS approved_memberships
            FROM harness_users u
            LEFT JOIN project_memberships m ON m.user_id = u.id
            WHERE u.email = :email
            GROUP BY u.id
        """, email=USER_EMAIL)[0]
        print("user DB:", dict(listed))
        assert listed["status"] == "ACTIVE" and listed["approved_memberships"] == 0

        st, user_login = http("POST", "/api/v1/auth/login", {"email": USER_EMAIL, "password": USER_PASSWORD})
        assert st == 200
        user_token = user_login["access_token"]
        st, user_projects = http("GET", "/api/v1/projects", bearer=user_token)
        print("user projects before assignment:", st, [p["project_slug"] for p in user_projects["projects"]])
        assert st == 200 and user_projects["projects"] == []

        welcome_log = db_rows("""
            SELECT status, template_name FROM email_dispatch_logs
            WHERE recipient = :email AND template_name = 'user_welcome'
            ORDER BY sent_at DESC LIMIT 1
        """, email=USER_EMAIL)
        print("welcome log:", dict(welcome_log[0]) if welcome_log else None)
        assert welcome_log and welcome_log[0]["status"] == "SENT"
        assert any("aun no tiene proyectos" in body for body in decoded_smtp_bodies(server))

        print("\n--- 3. Asignar proyecto explicitamente ---")
        st, project_created = http("POST", "/api/v1/projects", {
            "project_slug": PROJECT_SLUG,
            "display_name": "Users UX E2E",
            "description": "Proyecto para flujo de asignacion",
            "default_branch": "main",
        }, bearer=admin_token)
        assert st == 200
        project = project_created["project"]

        st, assigned = http("POST", f"/api/v1/projects/{project['id']}/members", {
            "user_id": user_id, "allowed_skills": ["python", "fastapi"],
        }, bearer=admin_token)
        print("assign project:", st, assigned.get("membership", {}).get("status"), assigned.get("email_queued"))
        assert st == 200 and assigned["membership"]["status"] == "APPROVED"
        assert assigned["email_queued"] is True

        st, users = http("GET", "/api/v1/admin/users?search=ux-user", bearer=admin_token)
        found = next(u for u in users["users"] if u["email"] == USER_EMAIL)
        print("admin users membership:", [(m["project_slug"], m["status"], m["allowed_skills"]) for m in found["memberships"]])
        assert any(m["project_slug"] == PROJECT_SLUG and m["status"] == "APPROVED" for m in found["memberships"])

        st, user_projects = http("GET", "/api/v1/projects", bearer=user_token)
        assert st == 200 and [p["project_slug"] for p in user_projects["projects"]] == [PROJECT_SLUG]

        print("\n--- 4. Revocar membresia ---")
        st, revoked = http("DELETE", f"/api/v1/projects/{project['id']}/members/{user_id}", bearer=admin_token)
        print("revoke:", st, revoked)
        assert st == 200 and revoked["membership"] == "REVOKED"
        st, user_projects = http("GET", "/api/v1/projects", bearer=user_token)
        assert st == 200 and user_projects["projects"] == []
        st, users = http("GET", "/api/v1/admin/users?search=ux-user", bearer=admin_token)
        found = next(u for u in users["users"] if u["email"] == USER_EMAIL)
        assert any(m["project_slug"] == PROJECT_SLUG and m["status"] == "REVOKED" for m in found["memberships"])

        print("\n--- 5. Generacion de credencial sin proyecto ---")
        st, generated = http("POST", "/api/v1/admin/users", {
            "email": GEN_EMAIL, "full_name": "UX Generated",
        }, bearer=admin_token)
        print("generated user:", st, {k: generated.get(k) for k in ("generated", "email_sent")})
        assert st == 200 and generated["generated"] is True and generated["email_sent"] is True
        assert "new_password" not in generated and "password" not in generated
        gen_welcome = db_rows("""
            SELECT status FROM email_dispatch_logs
            WHERE recipient = :email AND template_name = 'user_welcome'
            ORDER BY sent_at DESC LIMIT 1
        """, email=GEN_EMAIL)
        assert gen_welcome and gen_welcome[0]["status"] == "SENT"

        print("\n--- 6. Cleanup ---")
        cleanup()
        counts = db_rows("""
            SELECT 'users' AS k, count(*) AS n FROM harness_users
            UNION ALL SELECT 'projects', count(*) FROM harness_projects
            UNION ALL SELECT 'sessions', count(*) FROM agent_sessions
            UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
            UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
            UNION ALL SELECT 'email_logs', count(*) FROM email_dispatch_logs
        """)
        print("counts finales:", {r["k"]: r["n"] for r in counts})
        print("\nE2E STAGE 7 USERS UX: OK")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


try:
    main()
finally:
    try:
        cleanup()
    except Exception:
        pass
    try:
        restore_smtp(ORIGINAL_SMTP)
    except Exception:
        pass
