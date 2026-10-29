"""E2E Etapa 14: contrato de tarea, presupuesto, contexto y memoria episodica."""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
PROJECT = "stage14-contract"
ENGINEER_EMAIL = "stage14-eng@example.com"
ENGINEER_PASSWORD = "Stage14Eng2026!"
REPOS = pathlib.Path("/var/lib/harness/repos")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
CREATED_TASK_IDS = []

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, timeout=120):
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


async def api(method, path, body=None, bearer=None, timeout=120):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def write_project_files():
    repo = REPOS / PROJECT
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text(
        "# Stage 14 Contract\n\nProyecto de prueba para Task Contract v2 y Memory Kernel.\n",
        encoding="utf-8",
    )
    (repo / "docs" / "architecture.md").write_text(
        "# Arquitectura\n\nEl modulo calc calcula totales y formatting presenta moneda.\n",
        encoding="utf-8",
    )
    (repo / "src" / "calc.py").write_text(
        '"""Calculos de facturacion."""\n\n\n'
        "def calculate_invoice_total(subtotal: float, tax_rate: float) -> float:\n"
        '    """Calcula el total con impuestos."""\n'
        "    return round(subtotal * (1 + tax_rate), 2)\n",
        encoding="utf-8",
    )
    (repo / "src" / "formatting.py").write_text(
        '"""Formato de moneda."""\n\n\n'
        "def format_currency(value: float, currency: str = \"USD\") -> str:\n"
        '    """Formatea un valor monetario."""\n'
        "    return f\"{value:.2f} {currency}\"\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(repo),
        "-c", "user.email=stage14@harness.local",
        "-c", "user.name=Stage14 Fixture",
        "commit", "-m", "stage14 fixture",
    ], check=True, capture_output=True)


def cleanup():
    for task_id in CREATED_TASK_IDS:
        shutil.rmtree(WORKTREES / task_id, ignore_errors=True)
    if WORKTREES.exists():
        for child in WORKTREES.iterdir():
            git_file = child / ".git"
            try:
                if git_file.exists() and PROJECT in git_file.read_text(encoding="utf-8", errors="ignore"):
                    shutil.rmtree(child, ignore_errors=True)
            except Exception:
                pass
    project = db_rows("SELECT id::text FROM harness_projects WHERE project_slug = :slug", slug=PROJECT)
    if project:
        project_id = project[0]["id"]
        db_exec("""
            DELETE FROM harness_tasks
            WHERE plan_id IN (
                SELECT id FROM feature_consensus_plans WHERE project_id = CAST(:pid AS UUID)
            )
        """, pid=project_id)
        db_exec("DELETE FROM feature_consensus_plans WHERE project_id = CAST(:pid AS UUID)", pid=project_id)
    db_exec("DELETE FROM harness_projects WHERE project_slug = :slug", slug=PROJECT)
    db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=PROJECT)
    db_exec("DELETE FROM harness_users WHERE email = :email", email=ENGINEER_EMAIL)
    db_exec("DELETE FROM email_dispatch_logs WHERE recipient = :email", email=ENGINEER_EMAIL)
    shutil.rmtree(REPOS / PROJECT, ignore_errors=True)
    CREATED_TASK_IDS.clear()


async def main():
    print("== ETAPA 14 - TASK CONTRACT + MEMORY KERNEL ==")
    admin = None
    session_key = None
    try:
        cleanup()
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]

        print("\n--- 1. Proyecto e indice ---")
        st, project = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT,
            "display_name": "Stage14 Contract",
            "description": "Task contract y memoria",
            "default_branch": "main",
        }, bearer=admin)
        assert st == 200, project
        project_id = project["project"]["id"]
        write_project_files()
        st, indexed = await api("POST", "/api/v1/repos/index", {"repo_name": PROJECT, "branch": "main"}, bearer=admin)
        assert st == 200, indexed
        st, doc = await api("POST", f"/api/v1/projects/{project_id}/documents", {
            "source_uri": "repo://docs/architecture.md",
            "title": "Arquitectura Stage14",
            "doc_type": "ARCHITECTURE",
            "content": (REPOS / PROJECT / "docs" / "architecture.md").read_text(encoding="utf-8"),
            "metadata": {"source": "stage14"},
        }, bearer=admin)
        assert st == 200, doc
        print("proyecto:", project_id, "archivos indexados:", indexed.get("files"))

        print("\n--- 2. Plan con contrato y presupuesto 120k ---")
        plan_a = {"tasks": [{
            "title": "[Impl] Implementar total en src/calc.py",
            "description": "Implementar calculate_invoice_total respetando el contrato.",
            "allowed_files": ["src/calc.py"],
            "allowed_symbols": ["src/calc.py::calculate_invoice_total"],
            "acceptance_criteria": ["calculate_invoice_total existe y compila"],
            "test_commands": ["python -m compileall src/calc.py"],
            "task_kind": "IMPLEMENT",
        }]}
        plan_b = {"tasks": [
            {
                "title": "[Impl] Implementar total en src/calc.py",
                "description": "Implementar calculate_invoice_total y dejar handoff.",
                "allowed_files": ["src/calc.py"],
                "allowed_symbols": ["src/calc.py::calculate_invoice_total"],
                "acceptance_criteria": ["calculate_invoice_total existe y compila"],
                "test_commands": ["python -m compileall src/calc.py"],
                "task_kind": "IMPLEMENT",
            },
            {
                "title": "[Verify] Validar formato de moneda en src/formatting.py",
                "description": "Validar format_currency; forzar presupuesto bajo para probar NEEDS_SPLIT.",
                "allowed_files": ["src/formatting.py"],
                "allowed_symbols": ["src/formatting.py::format_currency"],
                "acceptance_criteria": ["format_currency formatea USD"],
                "test_commands": ["python -m compileall src/formatting.py"],
                "depends_on_steps": [1],
                "task_kind": "VERIFY",
                "max_input_tokens": 100,
            },
        ]}
        st, plan = await api("POST", "/api/v1/plans/propose", {
            "requested_by": "stage14-e2e",
            "repo_name": PROJECT,
            "project_id": project_id,
            "requirement_prompt": "Implementar calculo de facturacion con validacion de formato y handoff persistente",
            "plan_a_override": plan_a,
            "plan_b_override": plan_b,
            "model_profile": "local-120k",
        }, bearer=admin)
        assert st == 200, plan
        tasks = plan["created_tasks"]
        CREATED_TASK_IDS.extend(t["task_id"] for t in tasks)
        print("plan:", plan["plan_id"], "tareas:", len(tasks))
        assert len(tasks) == 2
        assert plan["master_plan_c"]["model_profile"] == "local-120k"
        assert plan["master_plan_c"]["plan_budget_tokens"] == 64000
        first, second = tasks
        assert first["description"] and first["acceptance_criteria"]
        assert first["allowed_symbols"] == ["src/calc.py::calculate_invoice_total"]
        assert first["estimated_input_tokens"] > 0
        assert first["max_input_tokens"] == 64000
        assert second["depends_on"] == [first["task_id"]]
        assert second["budget_status"] == "NEEDS_SPLIT"
        print("task1:", first["task_id"], "estimate:", first["estimated_input_tokens"])
        print("task2:", second["task_id"], "budget:", second["budget_status"], "depends_on:", second["depends_on"])

        print("\n--- 2b. Plan default deterministico ---")
        st, default_plan = await api("POST", "/api/v1/plans/propose", {
            "requested_by": "stage14-e2e",
            "repo_name": PROJECT,
            "project_id": project_id,
            "requirement_prompt": "Agregar validacion de moneda al calculo de facturacion",
            "model_profile": "local-32k",
        }, bearer=admin)
        assert st == 200, default_plan
        default_tasks = default_plan["created_tasks"]
        CREATED_TASK_IDS.extend(t["task_id"] for t in default_tasks)
        assert len(default_tasks) == 2
        assert default_plan["master_plan_c"]["model_profile"] == "local-32k"
        assert default_plan["master_plan_c"]["plan_budget_tokens"] == 20000
        assert all(t["description"] and t["acceptance_criteria"] for t in default_tasks)
        assert default_tasks[0]["estimated_input_tokens"] > 0
        assert default_tasks[1]["depends_on"] == [default_tasks[0]["task_id"]]
        print("default tasks:", [(t["task_kind"], t["estimated_input_tokens"], t["budget_status"]) for t in default_tasks])

        print("\n--- 3. Plan y contrato consultables ---")
        st, detail = await api("GET", f"/api/v1/plans/{plan['plan_id']}", bearer=admin)
        assert st == 200 and len(detail["tasks"]) == 2, detail
        assert detail["plan"]["model_profile"] == "local-120k"
        assert detail["tasks"][0]["acceptance_criteria"]
        st, contract = await api("GET", f"/api/v1/tasks/{first['task_id']}/contract", bearer=admin)
        assert st == 200, contract
        assert contract["task"]["allowed_symbols"] == ["src/calc.py::calculate_invoice_total"]
        assert contract["plan"]["requirement_prompt"]

        print("\n--- 4. Session local-120k ---")
        st, user = await api("POST", "/api/v1/admin/users", {
            "email": ENGINEER_EMAIL,
            "full_name": "Stage14 Engineer",
            "password": ENGINEER_PASSWORD,
        }, bearer=admin)
        assert st == 200, user
        st, member = await api("POST", f"/api/v1/projects/{project_id}/members", {
            "user_id": user["user"]["id"], "allowed_skills": ["python"],
        }, bearer=admin)
        assert st == 200, member
        st, eng_login = await api("POST", "/api/v1/auth/login", {
            "email": ENGINEER_EMAIL, "password": ENGINEER_PASSWORD,
        })
        assert st == 200, eng_login
        engineer = eng_login["access_token"]
        st, session = await api("POST", "/api/v1/portal/sessions", {
            "project_id": project_id,
            "agent_tool": "opencode",
            "llm_provider": "local-llm",
            "max_context_tokens": 32000,
            "model_profile": "local-120k",
        }, bearer=engineer)
        assert st == 200, session
        assert session["max_context_tokens"] == 120000
        assert session["model_profile"] == "local-120k"
        session_key = session["session_key"]
        st, profiles = await api("GET", "/api/v1/model-profiles", bearer=admin)
        assert st == 200 and any(p["name"] == "local-120k" for p in profiles["profiles"])

        print("\n--- 4b. Dispatch con DAG ---")
        st, dispatched = await api("POST", "/api/v1/tasks/dispatch", bearer=admin)
        assert st == 200, dispatched
        assert dispatched["count"] == 1, dispatched
        assert dispatched["dispatched"][0]["task_id"] == first["task_id"]
        second_row = db_rows(
            "SELECT status, assigned_session_id::text FROM harness_tasks WHERE id = CAST(:tid AS UUID)",
            tid=second["task_id"],
        )[0]
        assert second_row["status"] == "PENDING" and second_row["assigned_session_id"] is None
        print("dispatch:", dispatched["dispatched"][0]["task_id"], "segunda tarea bloqueada por DAG")

        print("\n--- 5. Checkpoint, memoria y resume ---")
        st, checkpoint = await api("POST", f"/api/v1/tasks/{first['task_id']}/checkpoint", {
            "summary": "Total implementado; falta validar redondeo final",
            "decisions": ["usar round a 2 decimales"],
            "files": ["src/calc.py"],
            "tests": ["python -m compileall src/calc.py"],
            "next_steps": ["validar formato"],
            "details": {"reason": "context_70pct"},
        }, bearer=session_key)
        assert st == 200, checkpoint
        assert checkpoint["memory_epoch"] == 1
        st, memory = await api("GET", f"/api/v1/tasks/{first['task_id']}/memory", bearer=session_key)
        assert st == 200 and memory["memory"], memory
        assert memory["handoff"]["summary"].startswith("Total implementado")
        st, resumed = await api("POST", f"/api/v1/tasks/{first['task_id']}/resume", bearer=session_key)
        assert st == 200 and resumed["handoff"], resumed
        print("memory entries:", len(memory["memory"]), "handoff:", memory["handoff"]["summary"])

        print("\n--- 6. Context pack v2 con task_id ---")
        st, pack = await api("POST", "/api/v1/context/pack", {
            "task_id": first["task_id"],
            "query": "calculate_invoice_total facturacion impuestos",
            "token_budget": 32000,
            "include_docs": True,
            "model_profile": "local-120k",
        }, bearer=session_key)
        assert st == 200, pack
        print("pack:", pack["estimated_tokens_used"], "/", pack["token_budget"], pack["manifest"])
        assert pack["estimated_tokens_used"] <= pack["token_budget"]
        assert pack["model_profile"] == "local-120k"
        assert pack["manifest"]["sections"]["ast"] > 0
        assert pack["manifest"]["sections"]["documentation"] > 0
        assert pack["task_contract"]["id"] == first["task_id"]
        assert pack["handoff"]["summary"].startswith("Total implementado")
        assert any("calculate_invoice_total" in c["symbol_name"] for c in pack["surgical_ast_chunks"])

        print("\n--- 7. Fallo persistente ---")
        st, failed = await api("POST", f"/api/v1/tasks/{first['task_id']}/fail-checkpoint", {
            "error_reason": "prueba de memoria de fallo",
            "git_diff_summary": "src/calc.py",
        }, bearer=session_key)
        assert st == 200, failed
        rows = db_rows("""
            SELECT kind, summary FROM harness_task_memory
            WHERE task_id = CAST(:tid AS UUID) ORDER BY id DESC LIMIT 5
        """, tid=first["task_id"])
        assert any(r["kind"] == "FAILURE" for r in rows), rows

        print("\n--- 8. Handoff de exito ---")
        db_exec("UPDATE harness_tasks SET status='IN_PROGRESS', assigned_session_id=NULL WHERE id=CAST(:tid AS UUID)", tid=first["task_id"])
        st, submitted = await api("POST", f"/api/v1/tasks/{first['task_id']}/submit", {
            "file_changes": {
                "src/calc.py": (
                    '"""Calculos de facturacion."""\n\n\n'
                    "def calculate_invoice_total(subtotal: float, tax_rate: float) -> float:\n"
                    '    """Calcula el total con impuestos."""\n'
                    "    return round(subtotal * (1 + tax_rate), 2)\n\n"
                    "# stage14 handoff\n"
                )
            },
            "commit_message": "stage14 task contract handoff",
            "summary": "Total implementado y mergeado en stage14",
            "tests": ["python -m compileall src/calc.py"],
        }, bearer=admin)
        assert st == 200, submitted
        row = db_rows("SELECT handoff_summary FROM harness_tasks WHERE id=CAST(:tid AS UUID)", tid=first["task_id"])[0]
        assert row["handoff_summary"] == "Total implementado y mergeado en stage14"
        rows = db_rows("""
            SELECT kind, summary FROM harness_task_memory
            WHERE task_id = CAST(:tid AS UUID) ORDER BY id DESC LIMIT 5
        """, tid=first["task_id"])
        assert any(r["kind"] == "HANDOFF" for r in rows), rows
        print("submit:", submitted["task_commit"][:8], "handoff persistido")

        print("\nE2E STAGE 14: OK")
    finally:
        try:
            cleanup()
        except Exception as exc:
            print("cleanup:", exc)


if __name__ == "__main__":
    asyncio.run(main())
