"""E2E Etapa 7 SMTP: configuracion, test, auditoria y correo de invitacion."""
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
ADMIN_EMAIL = os.environ.get("HARNESS_INITIAL_ADMIN_EMAIL", "admin@harness.local")
ADMIN_PASSWORD = os.environ.get("HARNESS_INITIAL_ADMIN_PASSWORD", "AdminHarness2026!")
PUBLIC_URL = os.environ.get("HARNESS_PUBLIC_URL", "http://11.0.0.3:8444").rstrip("/")
REPOS = pathlib.Path("/var/lib/harness/repos")
PROJECT_SLUG = "smtp-e2e"
DEV_EMAIL = "smtp-dev@empresa.com"
DEV_PASSWORD = "SmtpDev2026!"
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
    db_exec("""
        DELETE FROM email_dispatch_logs
        WHERE recipient = :dev
           OR (recipient = :admin AND template_name = 'smtp_test')
    """, dev=DEV_EMAIL, admin=ADMIN_EMAIL)
    shutil.rmtree(REPOS / PROJECT_SLUG, ignore_errors=True)


def main():
    global ORIGINAL_SMTP
    print("== ETAPA 7 - SMTP ==")
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

    print("\n--- 1. Migracion 0007 y tablas SMTP ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    tables = {r["tablename"] for r in db_rows("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    print("alembic:", version, "| tablas:", sorted({"system_smtp_config", "email_dispatch_logs"} & tables))
    assert version == "0007" or version > "0007"
    assert {"system_smtp_config", "email_dispatch_logs"}.issubset(tables)

    print("\n--- 2. Login admin y configuracion SMTP mock ---")
    st, login = http("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert st == 200, login
    admin_token = login["access_token"]

    st, current = http("GET", "/api/v1/admin/smtp-config", bearer=admin_token)
    print("smtp config inicial:", st, current)
    assert st == 200 and current["host"]
    assert "password_set" in current

    st, ui = http("GET", "/portal")
    assert st == 200 and isinstance(ui, str)
    for marker in ("viewSmtp", "smtpHost", "smtpSaveTest", "/api/v1/admin/smtp-config", "SMTP y Correos"):
        assert marker in ui, f"portal sin marcador SMTP: {marker}"
    print("portal SMTP UI:", st, len(ui), "bytes")

    st, encrypted = http("PUT", "/api/v1/admin/smtp-config", {
        "host": "127.0.0.1",
        "port": 2525,
        "username": "smtp-user",
        "password": "dummy-secret",
        "from_email": FROM_EMAIL,
        "use_tls": False,
        "use_ssl": False,
    }, bearer=admin_token)
    raw_password = db_rows("SELECT password FROM system_smtp_config WHERE id = 1")[0]["password"]
    print("password cifrado:", st, encrypted["password_set"], raw_password[:12] + "...")
    assert st == 200 and encrypted["password_set"] is True
    assert raw_password.startswith("vault:") and "dummy-secret" not in raw_password

    server = MockSmtpServer(("127.0.0.1", 0), SmtpHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("mock SMTP en 127.0.0.1:", port)

    try:
        st, saved = http("PUT", "/api/v1/admin/smtp-config", {
            "host": "127.0.0.1",
            "port": port,
            "username": None,
            "password": "",
            "from_email": FROM_EMAIL,
            "use_tls": False,
            "use_ssl": False,
            "test_recipient": ADMIN_EMAIL,
        }, bearer=admin_token)
        print("PUT smtp + test:", st, saved)
        assert st == 200 and saved["host"] == "127.0.0.1" and saved["port"] == port
        assert saved["password_set"] is False
        assert saved["test_sent"] is True

        cfg_row = db_rows("SELECT host, port, from_email, use_tls, use_ssl FROM system_smtp_config WHERE id = 1")[0]
        print("config en BD:", dict(cfg_row))
        assert cfg_row["host"] == "127.0.0.1" and cfg_row["port"] == port
        assert cfg_row["use_tls"] is False and cfg_row["use_ssl"] is False

        logs = db_rows("""
            SELECT recipient, subject, template_name, status, error_message
            FROM email_dispatch_logs
            WHERE template_name = 'smtp_test' AND recipient = :rec
            ORDER BY sent_at DESC LIMIT 1
        """, rec=ADMIN_EMAIL)
        print("log smtp_test:", dict(logs[0]) if logs else None)
        assert logs and logs[0]["status"] == "SENT" and logs[0]["error_message"] is None
        test_bodies = decoded_smtp_bodies(server)
        assert any("Correo de prueba" in body for body in test_bodies)

        print("\n--- 3. Fallo SMTP auditado ---")
        st, failed = http("PUT", "/api/v1/admin/smtp-config", {
            "host": "127.0.0.1",
            "port": 1,
            "username": None,
            "password": None,
            "from_email": FROM_EMAIL,
            "use_tls": False,
            "use_ssl": False,
            "test_recipient": ADMIN_EMAIL,
        }, bearer=admin_token)
        print("PUT smtp puerto cerrado:", st, failed)
        assert st == 200 and failed["test_sent"] is False
        failed_log = db_rows("""
            SELECT status, error_message FROM email_dispatch_logs
            WHERE template_name = 'smtp_test' AND recipient = :rec
            ORDER BY sent_at DESC LIMIT 1
        """, rec=ADMIN_EMAIL)[0]
        assert failed_log["status"] == "FAILED" and failed_log["error_message"]

        # Restaurar el mock para la invitacion.
        st, _ = http("PUT", "/api/v1/admin/smtp-config", {
            "host": "127.0.0.1",
            "port": port,
            "username": None,
            "password": None,
            "from_email": FROM_EMAIL,
            "use_tls": False,
            "use_ssl": False,
        }, bearer=admin_token)
        assert st == 200

        print("\n--- 4. Proyecto e invitacion por correo ---")
        st, created = http("POST", "/api/v1/projects", {
            "project_slug": PROJECT_SLUG,
            "display_name": "SMTP E2E",
            "description": "Proyecto para validar correo de invitacion",
            "default_branch": "main",
        }, bearer=admin_token)
        print("create project:", st, created.get("index") if isinstance(created, dict) else created)
        assert st == 200
        project = created["project"]

        st, member = http("POST", f"/api/v1/projects/{project['id']}/members", {
            "email": DEV_EMAIL,
            "full_name": "SMTP Dev",
            "password": DEV_PASSWORD,
            "allowed_skills": ["python"],
        }, bearer=admin_token)
        print("invite member:", st, {k: member.get(k) for k in ("email_queued", "email_recipient")})
        assert st == 200 and member["email_queued"] is True
        assert member["membership"]["status"] == "APPROVED"

        deadline = time.time() + 20
        invite_log = None
        while time.time() < deadline:
            rows = db_rows("""
                SELECT recipient, subject, template_name, status, error_message
                FROM email_dispatch_logs
                WHERE recipient = :rec AND template_name = 'project_invite'
                ORDER BY sent_at DESC LIMIT 1
            """, rec=DEV_EMAIL)
            if rows:
                invite_log = rows[0]
                break
            time.sleep(0.5)
        print("log project_invite:", dict(invite_log) if invite_log else None)
        assert invite_log and invite_log["status"] == "SENT"
        assert invite_log["error_message"] is None
        bodies = decoded_smtp_bodies(server)
        assert any(PROJECT_SLUG in body and f"{PUBLIC_URL}/portal" in body for body in bodies), \
            "el correo de invitacion no contiene proyecto y portal"
        assert any(DEV_PASSWORD in body for body in bodies), \
            "el correo de invitacion no contiene las credenciales iniciales"

        st, logs_api = http("GET", "/api/v1/admin/email-logs?limit=50", bearer=admin_token)
        print("email-logs:", st, len(logs_api.get("logs", [])) if isinstance(logs_api, dict) else logs_api)
        assert st == 200 and any(l["recipient"] == DEV_EMAIL for l in logs_api["logs"])
        assert any(l["status"] == "FAILED" for l in logs_api["logs"])

        print("\n--- 5. Cleanup ---")
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
        print("\nE2E STAGE 7 SMTP: OK")
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
