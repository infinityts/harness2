"""E2E Etapa 7 passwords: seed admin@local, cambio propio y reset admin con SMTP."""
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
PUBLIC_URL = os.environ.get("HARNESS_PUBLIC_URL", "http://11.0.0.3:8444").rstrip("/")
ADMIN_EMAIL = "admin@local"
ADMIN_PASSWORD = "admin"
REPOS = pathlib.Path("/var/lib/harness/repos")
PROJECT_SLUG = "pwd-e2e"
DEV_EMAIL = "pwd-dev@empresa.com"
DEV_INITIAL = "PwdDev2026!"
DEV_CHANGED = "PwdNew2026!"
DEV_RESET = "PwdReset2026!"
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
    db_exec("DELETE FROM harness_users WHERE email = :email", email=DEV_EMAIL)
    db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=PROJECT_SLUG)
    db_exec("DELETE FROM email_dispatch_logs WHERE recipient = :email", email=DEV_EMAIL)
    shutil.rmtree(REPOS / PROJECT_SLUG, ignore_errors=True)


def login(email, password):
    return http("POST", "/api/v1/auth/login", {"email": email, "password": password})


def main():
    global ORIGINAL_SMTP
    print("== ETAPA 7 - PASSWORDS ==")

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

    print("\n--- 1. Migracion 0008 y seed admin@local ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    seed = db_rows("""
        SELECT id::text, email, full_name, role, status, password_hash
        FROM harness_users WHERE email = :email
    """, email=ADMIN_EMAIL)
    print("alembic:", version, "| seed:", {k: seed[0][k] for k in ("email", "full_name", "role", "status")} if seed else None)
    assert version == "0008" or version > "0008"
    assert seed and seed[0]["role"] == "ADMIN" and seed[0]["status"] == "ACTIVE"
    assert seed[0]["password_hash"].startswith("pbkdf2_sha256$")
    assert "admin" not in seed[0]["password_hash"]

    st, admin_login = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    print("admin@local login:", st, admin_login.get("user", {}).get("role") if isinstance(admin_login, dict) else admin_login)
    assert st == 200 and admin_login["user"]["role"] == "ADMIN"
    admin_token = admin_login["access_token"]
    st, me = http("GET", "/api/v1/auth/me", bearer=admin_token)
    assert st == 200 and me["user"]["email"] == ADMIN_EMAIL

    print("\n--- 2. SMTP mock y proyecto/ingeniero de prueba ---")
    server = MockSmtpServer(("127.0.0.1", 0), SmtpHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    st, cfg = http("PUT", "/api/v1/admin/smtp-config", {
        "host": "127.0.0.1", "port": port, "username": None, "password": "",
        "from_email": FROM_EMAIL, "use_tls": False, "use_ssl": False,
    }, bearer=admin_token)
    print("smtp mock:", st, port)
    assert st == 200

    try:
        st, ui = http("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("passwordBtn", "openSelfPasswordModal", "reset-password", "Resetear contrasena"):
            assert marker in ui, f"portal sin marcador de password: {marker}"
        print("portal password UI:", st, len(ui), "bytes")

        st, created = http("POST", "/api/v1/projects", {
            "project_slug": PROJECT_SLUG,
            "display_name": "Password E2E",
            "description": "Proyecto para cambio y reset de contrasenas",
            "default_branch": "main",
        }, bearer=admin_token)
        assert st == 200
        project = created["project"]

        st, member = http("POST", f"/api/v1/projects/{project['id']}/members", {
            "email": DEV_EMAIL, "full_name": "Password Dev", "password": DEV_INITIAL,
            "allowed_skills": ["python"],
        }, bearer=admin_token)
        print("create engineer:", st, member.get("user", {}).get("email"))
        assert st == 200
        dev_id = member["user"]["id"]

        st, dev_login = login(DEV_EMAIL, DEV_INITIAL)
        assert st == 200
        dev_token = dev_login["access_token"]

        print("\n--- 3. Cambio de contrasena por el propio usuario ---")
        st, weak = http("PUT", "/api/v1/auth/password", {
            "current_password": DEV_INITIAL, "new_password": "weakpass",
        }, bearer=dev_token)
        print("policy weak:", st, weak)
        assert st == 400

        st, wrong = http("PUT", "/api/v1/auth/password", {
            "current_password": "incorrecta", "new_password": DEV_CHANGED,
        }, bearer=dev_token)
        print("current incorrecta:", st, wrong)
        assert st == 401

        st, changed = http("PUT", "/api/v1/auth/password", {
            "current_password": DEV_INITIAL, "new_password": DEV_CHANGED,
        }, bearer=dev_token)
        print("self change:", st, {"email_sent": changed.get("email_sent"), "status": changed.get("status")})
        assert st == 200 and changed["email_sent"] is True
        assert "new_password" not in changed and "password" not in changed

        st, _ = login(DEV_EMAIL, DEV_INITIAL)
        print("login viejo:", st)
        assert st == 401
        st, new_login = login(DEV_EMAIL, DEV_CHANGED)
        print("login nuevo:", st)
        assert st == 200

        row = db_rows("SELECT password_hash FROM harness_users WHERE email = :email", email=DEV_EMAIL)[0]
        assert row["password_hash"].startswith("pbkdf2_sha256$")
        assert DEV_CHANGED not in row["password_hash"]

        changed_log = db_rows("""
            SELECT status, error_message FROM email_dispatch_logs
            WHERE recipient = :email AND template_name = 'password_changed'
            ORDER BY sent_at DESC LIMIT 1
        """, email=DEV_EMAIL)
        print("log password_changed:", dict(changed_log[0]) if changed_log else None)
        assert changed_log and changed_log[0]["status"] == "SENT"

        bodies = decoded_smtp_bodies(server)
        assert any(f"{PUBLIC_URL}/portal" in body for body in bodies)
        assert not any(DEV_CHANGED in body for body in bodies), "la confirmacion no debe incluir la contrasena"

        print("\n--- 4. Reset por el Administrador + notificacion ---")
        st, reset = http("PUT", f"/api/v1/admin/users/{dev_id}/reset-password", {
            "new_password": DEV_RESET, "generate_random": False,
        }, bearer=admin_token)
        print("admin reset:", st, {k: reset.get(k) for k in ("generated", "email_sent", "status")})
        assert st == 200 and reset["generated"] is False and reset["email_sent"] is True
        assert "new_password" not in reset and "password" not in reset

        st, _ = login(DEV_EMAIL, DEV_CHANGED)
        print("login password anterior tras reset:", st)
        assert st == 401
        st, reset_login = login(DEV_EMAIL, DEV_RESET)
        print("login password reseteada:", st)
        assert st == 200

        reset_log = db_rows("""
            SELECT status, error_message FROM email_dispatch_logs
            WHERE recipient = :email AND template_name = 'admin_password_reset'
            ORDER BY sent_at DESC LIMIT 1
        """, email=DEV_EMAIL)
        print("log admin_password_reset:", dict(reset_log[0]) if reset_log else None)
        assert reset_log and reset_log[0]["status"] == "SENT"
        bodies = decoded_smtp_bodies(server)
        assert any(DEV_RESET in body for body in bodies), "el correo de reset no contiene la clave temporal"

        print("\n--- 5. Generacion aleatoria sin exponer la clave ---")
        st, generated = http("PUT", f"/api/v1/admin/users/{dev_id}/reset-password", {
            "generate_random": True,
        }, bearer=admin_token)
        print("admin reset random:", st, {k: generated.get(k) for k in ("generated", "email_sent")})
        assert st == 200 and generated["generated"] is True and generated["email_sent"] is True
        assert "new_password" not in generated and "password" not in generated

        st, logs = http("GET", "/api/v1/admin/email-logs?limit=50", bearer=admin_token)
        templates = {entry["template_name"] for entry in logs.get("logs", [])}
        print("email templates:", sorted(templates))
        assert {"password_changed", "admin_password_reset"}.issubset(templates)

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
        print("\nE2E STAGE 7 PASSWORDS: OK")
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
