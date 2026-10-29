"""E2E Etapa 15: splitter por simbolos y tokenizer multi-runtime."""
import asyncio
import json
import os
import pathlib
import shutil
import socketserver
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

import sqlalchemy as sa
from sqlalchemy import create_engine

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
PROJECT = "stage15-split"
ENGINEER_EMAIL = "stage15-eng@example.com"
ENGINEER_PASSWORD = "Stage15Eng2026!"
REPOS = pathlib.Path("/var/lib/harness/repos")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")
WORKTREE_TASKS = []

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
            return resp.status, json.loads(raw) if raw else None
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


class ExactTokenizerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._reply(200, {"data": [{"id": "mock-exact"}]})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode() or "{}")
        path = self.path.rstrip("/")
        if path in ("/tokenize", "/v1/tokenize"):
            text = body.get("content") or body.get("input") or body.get("prompt") or ""
            self._reply(200, {"tokens": [1] * max(1, len(str(text).split()))})
            return
        if path == "/v1/chat/completions":
            sample = ""
            messages = body.get("messages") or []
            if messages:
                sample = str(messages[-1].get("content") or "")
            self._reply(200, {
                "id": "mock",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": max(1, len(sample.split()) + 7), "completion_tokens": 1},
            })
            return
        self._reply(404, {"error": "not found"})


class CalibrationOnlyHandler(ExactTokenizerHandler):
    def do_POST(self):
        path = self.path.rstrip("/")
        if path in ("/tokenize", "/v1/tokenize", "/api/tokenize"):
            self._reply(404, {"error": "no tokenizer endpoint"})
            return
        super().do_POST()


class MockServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_mock(handler):
    server = MockServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def write_project_files():
    repo = REPOS / PROJECT
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("# Stage 15 split\n\nProyecto de splitter.\n", encoding="utf-8")
    (repo / "docs" / "architecture.md").write_text("# Arquitectura\n\nSplitter por simbolos y tokens.\n", encoding="utf-8")
    functions = []
    for idx in range(1, 5):
        body = "\n".join(
            f"    value_{idx}_{n} = {n} + {idx}" for n in range(1, 16)
        )
        functions.append(
            f"def operation_{idx}(seed: int) -> int:\n"
            f'    """Operacion de prueba {idx}."""\n'
            f"{body}\n"
            f"    return seed + value_{idx}_1 + value_{idx}_8\n"
        )
    (repo / "src" / "big.py").write_text(
        '"""Operaciones de prueba para split."""\n\n\n' + "\n\n".join(functions),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(repo),
        "-c", "user.email=stage15@harness.local",
        "-c", "user.name=Stage15 Fixture",
        "commit", "-m", "stage15 fixture",
    ], check=True, capture_output=True)


def cleanup():
    if WORKTREES.exists():
        for child in WORKTREES.iterdir():
            git_file = child / ".git"
            try:
                if git_file.exists() and PROJECT in git_file.read_text(encoding="utf-8", errors="ignore"):
                    shutil.rmtree(child, ignore_errors=True)
            except Exception:
                pass
    for task_id in WORKTREE_TASKS:
        shutil.rmtree(WORKTREES / task_id, ignore_errors=True)
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
    WORKTREE_TASKS.clear()


async def main():
    print("== ETAPA 15 - SPLITTER + TOKENIZER MULTI-RUNTIME ==")
    admin = None
    exact_server = calib_server = None
    original_profiles = {}
    try:
        cleanup()
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]

        print("\n--- 1. Tokenizer exacto llama.cpp (mock) ---")
        exact_server, exact_thread = start_mock(ExactTokenizerHandler)
        exact_url = f"http://127.0.0.1:{exact_server.server_address[1]}"
        calib_server, calib_thread = start_mock(CalibrationOnlyHandler)
        calib_url = f"http://127.0.0.1:{calib_server.server_address[1]}"

        st, profiles = await api("GET", "/api/v1/model-profiles", bearer=admin)
        assert st == 200, profiles
        original_profiles = {p["name"]: p for p in profiles["profiles"]}
        st, configured = await api("POST", "/api/v1/model-profiles/local-32k/tokenizer/config", {
            "tokenizer_kind": "llamacpp",
            "tokenizer_url": exact_url,
            "tokenizer_model": "mock-exact",
            "tokenizer_timeout_ms": 2000,
        }, bearer=admin)
        assert st == 200, configured
        st, health = await api("GET", "/api/v1/model-profiles/local-32k/tokenizer/health", bearer=admin)
        assert st == 200 and health["ok"] is True and health["status"] == "EXACT", health
        st, counted = await api("POST", "/api/v1/tokenizer/count", {
            "profile": "local-32k", "text": "uno dos tres cuatro",
        }, bearer=admin)
        assert st == 200 and counted["tokens"] == 4 and counted["status"] == "EXACT", counted
        print("exact:", counted)

        print("\n--- 2. Tokenizer OpenAI-compatible/Strata (calibrado) ---")
        st, configured = await api("POST", "/api/v1/model-profiles/local-120k/tokenizer/config", {
            "tokenizer_kind": "openai_compatible",
            "tokenizer_url": calib_url,
            "tokenizer_model": "mock-calib",
            "tokenizer_timeout_ms": 3000,
        }, bearer=admin)
        assert st == 200, configured
        st, calibrated = await api("POST", "/api/v1/model-profiles/local-120k/tokenizer/calibrate", {
            "sample": "uno dos tres cuatro cinco seis siete ocho nueve diez",
        }, bearer=admin)
        print("calibration:", calibrated)
        assert st == 200 and calibrated["status"] == "CALIBRATED", calibrated
        assert calibrated["chars_per_token"] > 0
        st, counted = await api("POST", "/api/v1/tokenizer/count", {
            "profile": "local-120k", "text": "uno dos tres cuatro cinco",
        }, bearer=admin)
        assert st == 200 and counted["tokens"] > 0, counted

        print("\n--- 3. Proyecto y tarea NEEDS_SPLIT ---")
        st, project = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT,
            "display_name": "Stage15 Split",
            "description": "Splitter y tokenizer",
            "default_branch": "main",
        }, bearer=admin)
        assert st == 200, project
        project_id = project["project"]["id"]
        write_project_files()
        st, indexed = await api("POST", "/api/v1/repos/index", {"repo_name": PROJECT, "branch": "main"}, bearer=admin)
        assert st == 200, indexed

        symbols = [f"src/big.py::operation_{i}" for i in range(1, 5)]
        plan_b = {"tasks": [{
            "title": "[Impl] Implementar operaciones de big.py",
            "description": "Implementar cuatro operaciones y dividir si el contexto excede el presupuesto.",
            "allowed_files": ["src/big.py"],
            "allowed_symbols": symbols,
            "acceptance_criteria": ["Las cuatro operaciones compilan", "No se toca otro archivo"],
            "test_commands": ["python -m compileall src/big.py"],
            "task_kind": "IMPLEMENT",
            "max_input_tokens": 3000,
        }]}
        plan_a = {"tasks": [dict(plan_b["tasks"][0])]}
        st, plan = await api("POST", "/api/v1/plans/propose", {
            "requested_by": "stage15-e2e",
            "repo_name": PROJECT,
            "project_id": project_id,
            "requirement_prompt": "Implementar operaciones de big.py con split por presupuesto",
            "plan_a_override": plan_a,
            "plan_b_override": plan_b,
            "model_profile": "local-32k",
        }, bearer=admin)
        assert st == 200, plan
        task = plan["created_tasks"][0]
        WORKTREE_TASKS.append(task["task_id"])
        print("task:", task["task_id"], "budget:", task["estimated_input_tokens"], "/", task["max_input_tokens"], task["budget_status"])
        assert task["budget_status"] == "NEEDS_SPLIT"

        print("\n--- 4. Dispatch bloquea NEEDS_SPLIT ---")
        st, user = await api("POST", "/api/v1/admin/users", {
            "email": ENGINEER_EMAIL, "full_name": "Stage15 Engineer", "password": ENGINEER_PASSWORD,
        }, bearer=admin)
        assert st == 200, user
        st, member = await api("POST", f"/api/v1/projects/{project_id}/members", {
            "user_id": user["user"]["id"], "allowed_skills": ["python"],
        }, bearer=admin)
        assert st == 200, member
        st, eng_login = await api("POST", "/api/v1/auth/login", {"email": ENGINEER_EMAIL, "password": ENGINEER_PASSWORD})
        assert st == 200, eng_login
        st, session = await api("POST", "/api/v1/portal/sessions", {
            "project_id": project_id, "agent_tool": "opencode", "llm_provider": "local-llm",
            "max_context_tokens": 32000, "model_profile": "local-32k",
        }, bearer=eng_login["access_token"])
        assert st == 200, session
        st, dispatched = await api("POST", "/api/v1/tasks/dispatch", bearer=admin)
        assert st == 200 and dispatched["count"] == 0, dispatched

        print("\n--- 5. Split por simbolos ---")
        st, split = await api("POST", f"/api/v1/tasks/{task['task_id']}/split", {
            "max_input_tokens": 3000,
        }, bearer=admin)
        assert st == 200, split
        print("split:", split["split_count"], "overhead:", split["overhead_tokens"], "available:", split["available_tokens"])
        assert split["split_count"] >= 2
        assert split["status"] == "SPLIT"
        children = split["children"]
        WORKTREE_TASKS.extend(c["task_id"] for c in children)
        all_symbols = []
        for child in children:
            all_symbols.extend(child["allowed_symbols"])
            assert child["estimated_input_tokens"] <= child["max_input_tokens"]
        assert sorted(all_symbols) == sorted(symbols)
        assert children[0]["depends_on"] == []
        for index in range(1, len(children)):
            assert children[index]["depends_on"] == [children[index - 1]["task_id"]]

        st, contract = await api("GET", f"/api/v1/tasks/{task['task_id']}/contract", bearer=admin)
        assert st == 200 and contract["task"]["status"] == "SPLIT", contract
        assert contract["children"]
        assert any(m["kind"] == "SPLIT" for m in contract["memory"]), contract["memory"]
        st, tree = await api("GET", f"/api/v1/tasks/{task['task_id']}/split-tree", bearer=admin)
        assert st == 200 and len(tree["nodes"]) == split["split_count"] + 1, tree

        print("\n--- 6. Dispatch de slices con DAG ---")
        st, dispatched = await api("POST", "/api/v1/tasks/dispatch", bearer=admin)
        assert st == 200 and dispatched["count"] == 1, dispatched
        assert dispatched["dispatched"][0]["task_id"] == children[0]["task_id"]
        print("dispatch slice:", dispatched["dispatched"][0]["task_id"])

        print("\n--- 7. Auto-split en plan ---")
        st, auto_plan = await api("POST", "/api/v1/plans/propose", {
            "requested_by": "stage15-e2e",
            "repo_name": PROJECT,
            "project_id": project_id,
            "requirement_prompt": "Segunda implementacion con auto split",
            "plan_a_override": plan_a,
            "plan_b_override": plan_b,
            "model_profile": "local-32k",
            "auto_split": True,
        }, bearer=admin)
        assert st == 200, auto_plan
        print("auto_split:", auto_plan.get("auto_split"))
        assert auto_plan.get("auto_split")
        assert any(item.get("status") == "SPLIT" for item in auto_plan["auto_split"])
        for item in auto_plan["auto_split"]:
            if item.get("status") == "SPLIT":
                WORKTREE_TASKS.extend(c["task_id"] for c in item["children"])

        print("\nE2E STAGE 15: OK")
    finally:
        for name, profile in original_profiles.items():
            try:
                await api("POST", f"/api/v1/model-profiles/{name}/tokenizer/config", {
                    "tokenizer_kind": profile.get("tokenizer_kind") or "auto",
                    "tokenizer_url": profile.get("tokenizer_url"),
                    "tokenizer_model": profile.get("tokenizer_model"),
                    "tokenizer_path": profile.get("tokenizer_path"),
                    "tokenizer_timeout_ms": profile.get("tokenizer_timeout_ms") or 2000,
                    "chars_per_token": profile.get("chars_per_token"),
                }, bearer=admin)
            except Exception as exc:
                print(f"restore tokenizer {name}: {exc}")
        for server in (exact_server, calib_server):
            if server:
                server.shutdown()
                server.server_close()
        try:
            cleanup()
        except Exception as exc:
            print("cleanup:", exc)


if __name__ == "__main__":
    asyncio.run(main())
