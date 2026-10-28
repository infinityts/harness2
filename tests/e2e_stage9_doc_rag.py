"""E2E Etapa 9: RAG documental nativo, worker headless y context pack hibrido."""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS_BASE = BASE.replace("http", "ws", 1)
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_KEY = os.environ.get("HARNESS_ADMIN_API_KEY", "")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
REPOS = pathlib.Path("/var/lib/harness/repos")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
BRIDGE = pathlib.Path("/app/client/harness_mcp_bridge.py")

PROJECT_A = "stage9-doc-rag"
PROJECT_B = "stage9-iso"
USER_EMAIL = "e2e-stage9@example.com"
USER_PASSWORD = "Stage9Doc2026!"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, admin=False, timeout=180):
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


async def api(method, path, body=None, bearer=None, admin=False, timeout=180):
    return await asyncio.to_thread(http, method, path, body, bearer, admin, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def cleanup():
    for slug in (PROJECT_A, PROJECT_B):
        db_exec("DELETE FROM harness_projects WHERE project_slug = :slug", slug=slug)
        db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=slug)
        shutil.rmtree(REPOS / slug, ignore_errors=True)
        shutil.rmtree(WORKTREES / slug, ignore_errors=True)
    db_exec("DELETE FROM harness_users WHERE email = :email", email=USER_EMAIL)
    db_exec("DELETE FROM email_dispatch_logs WHERE recipient = :email", email=USER_EMAIL)


def write_repo_files(slug):
    repo = REPOS / slug
    docs = repo / "docs"
    src = repo / "src"
    docs.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text(
        "# Stage 9 Doc RAG\n\n"
        "## Objetivo\n\n"
        "Proyecto de prueba para el RAG documental nativo de Harness2.\n\n"
        "## Contratos\n\n"
        "- Aislamiento estricto por proyecto.\n"
        "- La documentacion se indexa bajo demanda.\n",
        encoding="utf-8",
    )
    (docs / "architecture.md").write_text(
        "# Arquitectura del modulo de facturacion\n\n"
        "## Componentes\n\n"
        "El servicio `billing` calcula totales de factura y valida los impuestos.\n\n"
        "## Flujo\n\n"
        "1. Recibe una factura con subtotal e impuesto.\n"
        "2. Calcula el total con la funcion calculate_invoice_total.\n"
        "3. Persiste el resultado y registra evidencia.\n",
        encoding="utf-8",
    )
    (src / "billing.py").write_text(
        '"""Servicio de facturacion de prueba."""\n\n\n'
        "def calculate_invoice_total(subtotal: float, tax_rate: float) -> float:\n"
        '    """Calcula el total de una factura aplicando impuestos."""\n'
        "    return round(subtotal * (1 + tax_rate), 2)\n\n\n"
        "def validate_tax_rate(tax_rate: float) -> bool:\n"
        '    """Valida que la tasa de impuesto este en rango."""\n'
        "    return 0 <= tax_rate <= 1\n",
        encoding="utf-8",
    )


async def open_agent_ws(session_id, session_key):
    ws = await websockets.connect(
        f"{WS_BASE}/ws/agent/{session_id}",
        additional_headers={"Authorization": f"Bearer {session_key}"},
    )

    async def heartbeat():
        try:
            while True:
                await ws.send(json.dumps({"current_context_tokens": 10, "status": "ONLINE"}))
                await asyncio.sleep(3)
        except Exception:
            pass

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.3)
    return ws, task


def run_bridge(tool, args, session_id, session_key):
    env = dict(os.environ)
    env.update({
        "HARNESS_URL": BASE,
        "HARNESS_SESSION_ID": session_id,
        "HARNESS_SESSION_KEY": session_key,
        "HARNESS_HEARTBEAT_SECONDS": "60",
    })
    proc = subprocess.run(
        ["python", str(BRIDGE), "--cli", tool, json.dumps(args)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"bridge {tool} fallo: {proc.stderr[-1200:]}")
    raw = proc.stdout.strip()
    return json.loads(raw)


async def main():
    print("== ETAPA 9 - RAG DOCUMENTAL NATIVO ==")
    cleanup()

    print("\n--- 1. Migracion 0011, tablas, indices y skill oficial ---")
    version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    print("alembic:", version)
    assert version >= "0011"

    cols = {r["column_name"] for r in db_rows("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'agent_sessions'
    """)}
    assert "is_headless_worker" in cols
    tables = {r["table_name"] for r in db_rows("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('harness_documents', 'harness_document_chunks')
    """)}
    assert tables == {"harness_documents", "harness_document_chunks"}
    indexes = {r["indexname"] for r in db_rows("""
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN ('harness_documents', 'harness_document_chunks', 'agent_sessions')
    """)}
    print("indices:", sorted(i for i in indexes if "document" in i or "headless" in i))
    assert "idx_document_chunks_hnsw" in indexes
    assert "uq_agent_sessions_headless_project" in indexes

    skill = db_rows("""
        SELECT slug, is_official, compiled_tokens, compiled_skill_md
        FROM harness_skills WHERE slug = 'rag-docs-curator'
    """)
    assert skill and skill[0]["is_official"] is True
    assert skill[0]["compiled_tokens"] <= 500
    assert "Mandatory Steps" in skill[0]["compiled_skill_md"]
    print("skill rag-docs-curator:", skill[0]["compiled_tokens"], "tokens")

    st, login = await api("POST", "/api/v1/auth/login", {
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    })
    assert st == 200, login
    admin_token = login["access_token"]

    print("\n--- 2. Proyectos, repos y sesiones 1:1 ---")
    st, created_a = await api("POST", "/api/v1/projects", {
        "project_slug": PROJECT_A,
        "display_name": "Stage 9 Doc RAG",
        "description": "RAG documental nativo y worker docs-curator",
        "default_branch": "main",
    }, bearer=admin_token)
    assert st == 200, created_a
    project_a = created_a["project"]
    st, created_b = await api("POST", "/api/v1/projects", {
        "project_slug": PROJECT_B,
        "display_name": "Stage 9 Isolation",
        "description": "Proyecto de control para aislamiento documental",
        "default_branch": "main",
    }, bearer=admin_token)
    assert st == 200, created_b
    project_b = created_b["project"]

    write_repo_files(PROJECT_A)
    write_repo_files(PROJECT_B)

    st, indexed = await api("POST", "/api/v1/repos/index", {
        "repo_name": PROJECT_A, "branch": "main",
    }, admin=True)
    assert st == 200, indexed
    st, indexed_b = await api("POST", "/api/v1/repos/index", {
        "repo_name": PROJECT_B, "branch": "main",
    }, admin=True)
    assert st == 200, indexed_b

    st, new_user = await api("POST", "/api/v1/admin/users", {
        "email": USER_EMAIL, "full_name": "Stage 9 Engineer", "password": USER_PASSWORD,
    }, bearer=admin_token)
    assert st == 200, new_user
    user_id = new_user["user"]["id"]

    for project in (project_a, project_b):
        st, member = await api("POST", f"/api/v1/projects/{project['id']}/members", {
            "user_id": user_id,
            "allowed_skills": ["rag-docs-curator", "tech-writer"],
        }, bearer=admin_token)
        assert st == 200, member
        st, _ = await api("PUT", f"/api/v1/projects/{project['id']}/skills", {
            "skill_slugs": ["rag-docs-curator", "tech-writer", "doc-analyzer"],
        }, bearer=admin_token)
        assert st == 200

    st, user_login = await api("POST", "/api/v1/auth/login", {
        "email": USER_EMAIL, "password": USER_PASSWORD,
    })
    assert st == 200, user_login
    user_token = user_login["access_token"]

    st, session_a = await api("POST", "/api/v1/portal/sessions", {
        "project_id": project_a["id"], "agent_tool": "opencode",
        "llm_provider": "local-llm", "max_context_tokens": 32000,
    }, bearer=user_token)
    assert st == 200, session_a
    assert "rag-docs-curator" in session_a["skills"]
    st, session_b = await api("POST", "/api/v1/portal/sessions", {
        "project_id": project_b["id"], "agent_tool": "opencode",
        "llm_provider": "local-llm", "max_context_tokens": 32000,
    }, bearer=user_token)
    assert st == 200, session_b

    ws_a, hb_a = await open_agent_ws(session_a["session_id"], session_a["session_key"])
    ws_b, hb_b = await open_agent_ws(session_b["session_id"], session_b["session_key"])

    print("\n--- 3. Bootstrap con worker headless visible en War Room ---")
    st, boot = await api(
        "POST", f"/api/v1/projects/{project_a['id']}/documents/bootstrap",
        bearer=admin_token,
    )
    print("bootstrap:", st, {k: boot.get(k) for k in ("scanned", "created", "updated", "skipped", "documents_count")})
    assert st == 200 and boot["documents_count"] >= 3
    assert boot["errors"] == []

    st, sessions = await api("GET", "/api/v1/sessions", bearer=admin_token)
    assert st == 200
    workers = [
        s for s in sessions["sessions"]
        if s.get("is_headless_worker") and s.get("project_id") == project_a["id"]
    ]
    print("workers headless:", [(w["id"][:8], w["agent_tool"], w["status"]) for w in workers])
    assert len(workers) == 1
    worker = workers[0]
    assert worker["agent_tool"] == "headless-worker"
    assert worker["engineer_username"] == "docs-curator"
    assert {"tech-writer", "doc-analyzer", "rag-docs-curator"}.issubset(set(worker["skills"]))

    st, topology = await api("GET", "/api/v1/war-room/topology", admin=True)
    assert st == 200
    node = next((n for n in topology["nodes"] if n["session_id"] == worker["id"]), None)
    print("nodo topologia:", node and {k: node[k] for k in ("agent_tool", "project_slug", "status", "is_headless_worker")})
    assert node and node["is_headless_worker"] is True
    assert node["project_slug"] == PROJECT_A
    event_types = {e["event_type"] for e in topology["events"]}
    assert "HEADLESS_WORKER_READY" in event_types
    assert "DOCS_BOOTSTRAP_COMPLETED" in event_types

    st, docs = await api("GET", f"/api/v1/projects/{project_a['id']}/documents", bearer=admin_token)
    assert st == 200
    sources = {d["source_uri"] for d in docs["documents"]}
    print("fuentes:", sorted(sources))
    assert {"repo://README.md", "repo://docs/architecture.md",
            "adr://0001-initial-architecture.md", "runbook://development-contracts.md",
            "auto://architecture-overview.md"}.issubset(sources)

    st, boot_again = await api(
        "POST", f"/api/v1/projects/{project_a['id']}/documents/bootstrap",
        bearer=admin_token,
    )
    assert st == 200 and boot_again["created"] == 0 and boot_again["updated"] == 0
    st, sessions_again = await api("GET", "/api/v1/sessions", bearer=admin_token)
    workers_again = [
        s for s in sessions_again["sessions"]
        if s.get("is_headless_worker") and s.get("project_id") == project_a["id"]
        and s["status"] != "REVOKED"
    ]
    assert len(workers_again) == 1 and workers_again[0]["id"] == worker["id"]

    print("\n--- 4. Ingesta idempotente y redaccion de secretos ---")
    idem_source = "manual://e2e-idempotent.md"
    idem_content = (
        "# Runbook de despliegue\n\n"
        "## Pasos\n\n"
        "1. Ejecutar migraciones Alembic.\n"
        "2. Verificar health del contenedor.\n"
        "3. Registrar evidencia de la ventana de cambio.\n"
    )
    st, first = await api("POST", f"/api/v1/projects/{project_a['id']}/documents", {
        "source_uri": idem_source,
        "title": "Runbook de despliegue E2E",
        "doc_type": "RUNBOOK",
        "content": idem_content,
        "metadata": {"origin": "e2e-stage9"},
    }, bearer=session_a["session_key"])
    print("ingesta 1:", st, first.get("status"), first.get("chunks"))
    assert st == 200 and first["status"] in ("CREATED", "UPDATED") and first["chunks"] > 0
    st, second = await api("POST", f"/api/v1/projects/{project_a['id']}/documents", {
        "source_uri": idem_source,
        "title": "Runbook de despliegue E2E",
        "doc_type": "RUNBOOK",
        "content": idem_content,
    }, bearer=session_a["session_key"])
    print("ingesta 2:", st, second.get("status"), second.get("chunks"))
    assert st == 200 and second["status"] == "SKIPPED" and second["chunks"] == 0
    assert first["content_checksum"] == second["content_checksum"]

    secret_source = "manual://e2e-secrets.md"
    secret_content = (
        "# Credenciales de prueba\n\n"
        "## Base de datos\n\n"
        "postgresql://harness:SuperSecret123@db.local:5432/harness_core\n\n"
        "## API\n\n"
        'api_key = "sk-live-ABC123456789"\n\n'
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.firma\n"
    )
    st, secret_doc = await api("POST", f"/api/v1/projects/{project_a['id']}/documents", {
        "source_uri": secret_source,
        "title": "Credenciales de prueba",
        "doc_type": "OTHER",
        "content": secret_content,
    }, bearer=session_a["session_key"])
    print("ingesta secretos:", st, secret_doc.get("status"))
    assert st == 200

    rows = db_rows("""
        SELECT raw_content FROM harness_documents
        WHERE project_id = CAST(:pid AS UUID) AND source_uri = :source
    """, pid=project_a["id"], source=secret_source)
    assert rows
    stored = rows[0]["raw_content"]
    assert "[REDACTED_SECRET]" in stored
    for leaked in ("SuperSecret123", "sk-live-ABC123456789", "eyJhbGciOiJIUzI1NiJ9"):
        assert leaked not in stored, f"secreto filtrado: {leaked}"
    chunk_texts = db_rows("""
        SELECT c.chunk_text FROM harness_document_chunks c
        JOIN harness_documents d ON d.id = c.document_id
        WHERE d.project_id = CAST(:pid AS UUID) AND d.source_uri = :source
    """, pid=project_a["id"], source=secret_source)
    assert chunk_texts and any("[REDACTED_SECRET]" in r["chunk_text"] for r in chunk_texts)
    for r in chunk_texts:
        for leaked in ("SuperSecret123", "sk-live-ABC123456789", "eyJhbGciOiJIUzI1NiJ9"):
            assert leaked not in r["chunk_text"], f"secreto filtrado en chunk: {leaked}"

    st, secret_search = await api("POST", f"/api/v1/projects/{project_a['id']}/documents/search", {
        "query": "credenciales base de datos api token",
        "limit": 8,
    }, bearer=session_a["session_key"])
    assert st == 200 and secret_search["chunks"]
    assert any(c["source_uri"] == secret_source for c in secret_search["chunks"])
    for chunk in secret_search["chunks"]:
        assert "SuperSecret123" not in chunk["chunk_text"]
        assert "sk-live-ABC123456789" not in chunk["chunk_text"]

    print("\n--- 5. Aislamiento cross-project ---")
    st, denied_ingest = await api("POST", f"/api/v1/projects/{project_a['id']}/documents", {
        "source_uri": "manual://cross-project.md",
        "title": "Intento cross project",
        "doc_type": "OTHER",
        "content": "# No permitido\n\nContenido de otro proyecto.",
    }, bearer=session_b["session_key"])
    print("ingest cross:", st, denied_ingest)
    assert st == 403 and "Cross-Project Isolation Violation" in json.dumps(denied_ingest)

    st, denied_search = await api("POST", f"/api/v1/projects/{project_a['id']}/documents/search", {
        "query": "credenciales secretas",
    }, bearer=session_b["session_key"])
    print("search cross:", st)
    assert st == 403

    st, denied_list = await api("GET", f"/api/v1/projects/{project_a['id']}/documents", bearer=session_b["session_key"])
    print("list cross:", st)
    assert st == 403

    st, own_search = await api("POST", f"/api/v1/projects/{project_b['id']}/documents/search", {
        "query": "credenciales secretas facturacion",
        "limit": 10,
    }, bearer=session_b["session_key"])
    assert st == 200
    assert all(c["source_uri"] != secret_source for c in own_search["chunks"])
    st, own_docs = await api("GET", f"/api/v1/projects/{project_b['id']}/documents", bearer=session_b["session_key"])
    assert st == 200
    assert all(d["source_uri"] != secret_source for d in own_docs["documents"])

    print("\n--- 6. Context pack hibrido codigo + documentacion ---")
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
    assert pack["documentation_tokens_used"] <= 560
    assert pack["relevant_doc_chunks"], "sin chunks documentales"
    assert pack["surgical_ast_chunks"], "sin chunks AST de codigo"
    assert pack["repo_quick_map"]
    assert any("billing.py" in c["file_path"] for c in pack["surgical_ast_chunks"])
    assert any("architecture" in c["source_uri"] or "README" in c["source_uri"]
               for c in pack["relevant_doc_chunks"])

    st, pack_no_docs = await api("POST", "/api/v1/context/pack", {
        "repo_name": PROJECT_A,
        "query": "calculate_invoice_total facturacion impuestos",
        "token_budget": 2000,
        "top_k": 6,
        "include_docs": False,
    }, bearer=session_a["session_key"])
    assert st == 200 and pack_no_docs["relevant_doc_chunks"] == []
    assert pack_no_docs["estimated_tokens_used"] <= 2000

    print("\n--- 7. Tools MCP RAG y portal ---")
    listed = run_bridge("harness_rag_list", {}, session_a["session_id"], session_a["session_key"])
    assert any(d["source_uri"] == secret_source for d in listed["documents"])
    found = run_bridge("harness_rag_search", {
        "query": "facturacion impuestos", "limit": 5,
    }, session_a["session_id"], session_a["session_key"])
    assert found["chunks"] and all("project_id" in c for c in found["chunks"])
    ingested = run_bridge("harness_rag_ingest", {
        "source_uri": "manual://e2e-mcp-tool.md",
        "title": "Ingesta via MCP",
        "doc_type": "OTHER",
        "content": "# Ingesta MCP\n\nLa tool harness_rag_ingest funciona de extremo a extremo.",
    }, session_a["session_id"], session_a["session_key"])
    print("mcp ingest:", ingested.get("status"), ingested.get("chunks"))
    assert ingested["status"] in ("CREATED", "UPDATED")

    st, ui = await api("GET", "/portal")
    assert st == 200 and isinstance(ui, str)
    for marker in ("viewKnowledge", "Base de Conocimiento", "kbBootstrap", "kbSearch",
                   "harness_rag_search", "rag-docs-curator"):
        assert marker in ui, f"UI sin marcador: {marker}"
    print("portal knowledge UI:", st, len(ui), "bytes")

    print("\n--- 8. Kill switch y reemplazo del worker ---")
    st, revoked = await api("POST", f"/api/v1/sessions/{worker['id']}/revoke", bearer=admin_token)
    print("revoke worker:", st, revoked)
    assert st == 200 and revoked["status"] == "REVOKED"
    revoked_row = db_rows("SELECT status FROM agent_sessions WHERE id = CAST(:sid AS UUID)", sid=worker["id"])
    assert revoked_row and revoked_row[0]["status"] == "REVOKED"

    st, reboot = await api(
        "POST", f"/api/v1/projects/{project_a['id']}/documents/bootstrap",
        bearer=admin_token,
    )
    assert st == 200
    st, sessions_after = await api("GET", "/api/v1/sessions", bearer=admin_token)
    active_workers = [
        s for s in sessions_after["sessions"]
        if s.get("is_headless_worker") and s.get("project_id") == project_a["id"]
        and s["status"] != "REVOKED"
    ]
    print("workers activos tras revoke/bootstrap:", [(w["id"][:8], w["status"]) for w in active_workers])
    assert len(active_workers) == 1 and active_workers[0]["id"] != worker["id"]

    print("\n--- 9. Cleanup ---")
    for task, ws in ((hb_a, ws_a), (hb_b, ws_b)):
        task.cancel()
        await ws.close()
    cleanup()
    counts = db_rows("""
        SELECT 'documents' AS k, count(*) AS n FROM harness_documents WHERE project_id NOT IN (SELECT id FROM harness_projects)
        UNION ALL SELECT 'chunks', count(*) FROM harness_document_chunks WHERE project_id NOT IN (SELECT id FROM harness_projects)
        UNION ALL SELECT 'headless', count(*) FROM agent_sessions WHERE is_headless_worker = true AND project_id NOT IN (SELECT id FROM harness_projects)
    """)
    print("huerfanos:", {r["k"]: r["n"] for r in counts})
    assert all(r["n"] == 0 for r in counts)
    print("\nE2E STAGE 9 DOC RAG: OK")


try:
    asyncio.run(main())
finally:
    try:
        cleanup()
    except Exception:
        pass
