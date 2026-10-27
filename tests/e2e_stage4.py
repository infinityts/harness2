"""E2E Fase 3.5 + Etapa 4: JWT/WS, scoping, worktrees y Guardian de Git real."""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import jwt as pyjwt
import sqlalchemy as sa
from sqlalchemy import create_engine
import websockets

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
WS = BASE.replace("http", "ws", 1)
ADMIN_KEY = os.environ.get("HARNESS_ADMIN_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET = os.environ.get("HARNESS_JWT_SECRET", "")
TTL_HOURS = int(os.environ.get("SESSION_JWT_TTL_HOURS", "12"))
REPO = pathlib.Path("/var/lib/harness/repos/harness-self")
WORKTREES = pathlib.Path("/var/lib/harness/worktrees")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, admin=False, bearer=None):
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["X-Harness-Admin-Key"] = ADMIN_KEY
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
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


def git_repo(*args, check=True):
    return subprocess.run(["git", "-C", str(REPO), "-c", "user.name=harness", "-c", "user.email=harness@local", *args],
                          capture_output=True, text=True, check=check)


def register(username, skills):
    status, payload = http("POST", "/api/v1/sessions/register", {
        "engineer_username": username, "agent_tool": "opencode", "llm_provider": "local-qwen",
        "skills": skills, "max_context_tokens": 32000,
    }, admin=True)
    assert status == 200, (status, payload)
    return payload


def cleanup():
    tasks = db_rows("""
        SELECT t.id::text AS id FROM harness_tasks t
        JOIN feature_consensus_plans p ON p.id = t.plan_id
        WHERE p.requested_by LIKE 'e2e-stage4%'
    """)
    for task in tasks:
        shutil.rmtree(WORKTREES / task["id"], ignore_errors=True)
    db_exec("DELETE FROM harness_tasks WHERE plan_id IN (SELECT id FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage4%')")
    db_exec("DELETE FROM feature_consensus_plans WHERE requested_by LIKE 'e2e-stage4%'")
    db_exec("DELETE FROM agent_sessions WHERE engineer_username LIKE 'e2e-stage4-%' OR engineer_username LIKE 'e2e-scope-%'")
    db_exec("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE repo_name = 'harness-self'")
    git_repo("worktree", "prune", check=False)


def memory(task_id):
    return db_rows("SELECT failed_attempts_memory FROM harness_tasks WHERE id = CAST(:tid AS UUID)", tid=task_id)[0]["failed_attempts_memory"]


async def main():
    print("== FASE 3.5 + ETAPA 4 ==")
    cleanup()

    print("\n--- 1. JWT exp/jti ---")
    a = register("e2e-scope-a", ["python"])
    decoded = pyjwt.decode(a["session_key"], JWT_SECRET, algorithms=["HS256"])
    print("jti:", decoded.get("jti"), "ttl_hours:", (decoded["exp"] - decoded["iat"]) / 3600)
    assert decoded.get("jti") and decoded["exp"] - decoded["iat"] == TTL_HOURS * 3600

    print("\n--- 2. WS solo ?token= (debe rechazar 4003) ---")
    try:
        async with websockets.connect(f"{WS}/ws/agent/{a['session_id']}?token=invalid-token") as w:
            await asyncio.wait_for(w.recv(), 12)
        print("QUERY_TOKEN unexpected-open")
        raise AssertionError("query token accepted")
    except Exception as exc:
        code = getattr(exc, "code", None) or getattr(getattr(exc, "rcvd", None), "code", None)
        print("QUERY_TOKEN rejected:", type(exc).__name__, "code:", code)
        assert code == 4003

    print("\n--- 3. Scoping de /tasks ---")
    b = register("e2e-scope-b", ["rust"])
    scope_plan = str(uuid.uuid4())
    task_a, task_b = str(uuid.uuid4()), str(uuid.uuid4())
    db_exec("""
        INSERT INTO feature_consensus_plans
        (id, requested_by, requirement_prompt, plan_a_json, plan_b_json,
         arbiter_justification, master_plan_c_json, status)
        VALUES (CAST(:pid AS UUID), 'e2e-stage4-scope', 'scope', '{}', '{}', 'scope',
                CAST(:pc AS JSONB), 'APPROVED')
    """, pid=scope_plan, pc=json.dumps({"repo_name": "harness-self"}))
    for tid, sid, title, skills, branch in [
        (task_a, a["session_id"], "scope A", ["python"], "e2e/scope-a"),
        (task_b, b["session_id"], "scope B", ["rust"], "e2e/scope-b"),
    ]:
        db_exec("""
            INSERT INTO harness_tasks
            (id, plan_id, assigned_session_id, title, required_skills, git_branch, allowed_files, status)
            VALUES (CAST(:tid AS UUID), CAST(:pid AS UUID), CAST(:sid AS UUID), :title,
                    CAST(:skills AS JSONB), :branch, CAST(:files AS JSONB), 'IN_PROGRESS')
        """, tid=tid, pid=scope_plan, sid=sid, title=title, skills=json.dumps(skills),
             branch=branch, files=json.dumps(["src/main.py"]))

    st_a, list_a = http("GET", f"/api/v1/tasks?plan_id={scope_plan}", bearer=a["session_key"])
    st_admin, list_admin = http("GET", f"/api/v1/tasks?plan_id={scope_plan}", admin=True)
    print("agente A ve:", [t["title"] for t in list_a["tasks"]])
    print("admin ve:", [t["title"] for t in list_admin["tasks"]])
    assert st_a == 200 and len(list_a["tasks"]) == 1 and list_a["tasks"][0]["id"] == task_a
    assert st_admin == 200 and len(list_admin["tasks"]) == 2

    db_exec("DELETE FROM harness_tasks WHERE plan_id = CAST(:pid AS UUID)", pid=scope_plan)
    db_exec("DELETE FROM feature_consensus_plans WHERE id = CAST(:pid AS UUID)", pid=scope_plan)
    db_exec("DELETE FROM agent_sessions WHERE id IN (CAST(:a AS UUID), CAST(:b AS UUID))", a=a["session_id"], b=b["session_id"])

    print("\n--- 4. Plan + worktree aislado ---")
    s = register("e2e-stage4-agent", ["python", "fastapi"])
    ws = await websockets.connect(
        f"{WS}/ws/agent/{s['session_id']}",
        additional_headers={"Authorization": f"Bearer {s['session_key']}"},
    )
    await ws.send(json.dumps({"current_context_tokens": 10}))
    await asyncio.wait_for(ws.recv(), 5)

    overrides = {
        "plan_a_override": {
            "strategy": "CONSERVATIVE_MINIMAL_FOOTPRINT",
            "allow_dependency_changes": False,
            "estimated_context_per_task": 1200,
            "tasks": [{
                "title": "[Core] Implementar helper en src/main.py",
                "required_skills": ["python", "fastapi"],
                "allowed_files": ["src/main.py"],
                "allow_dependency_changes": False,
            }],
        },
        "plan_b_override": {
            "strategy": "MODULAR_SCALABLE_VERIFIED",
            "allow_dependency_changes": False,
            "estimated_context_per_task": 1500,
            "tasks": [
                {
                    "title": "[Impl] Implementar helper en src/main.py",
                    "required_skills": ["python", "fastapi"],
                    "allowed_files": ["src/main.py"],
                    "allow_dependency_changes": False,
                },
                {
                    "title": "[Verify/Integration] Validar helpers en src/indexer.py",
                    "required_skills": ["python", "fastapi"],
                    "allowed_files": ["src/indexer.py"],
                    "allow_dependency_changes": False,
                },
            ],
        },
    }
    st, plan = http("POST", "/api/v1/plans/propose", {
        "requested_by": "e2e-stage4",
        "repo_name": "harness-self",
        "requirement_prompt": "Agregar helper stage4 y validar el flujo de git real",
        "required_skills_hint": ["python", "fastapi"],
        **overrides,
    }, admin=True)
    print("plan:", st, plan.get("plan_id"), "tasks:", len(plan["created_tasks"]))
    assert st == 200 and len(plan["created_tasks"]) == 2
    task1 = plan["created_tasks"][0]

    st, dispatch = http("POST", "/api/v1/tasks/dispatch", admin=True)
    print("dispatch:", st, dispatch)
    assert st == 200 and dispatch["count"] == 1
    assigned = dispatch["dispatched"][0]
    assert assigned["task_id"] == task1["task_id"]

    evt = json.loads(await asyncio.wait_for(ws.recv(), 5))
    print("WS:", evt.get("event"))

    wt = pathlib.Path(assigned["worktree"])
    branch = assigned["git_branch"]
    print("worktree:", wt, "exists:", wt.exists())
    print("branch:", git_repo("worktree", "list", "--porcelain").stdout)
    assert wt.exists()
    head_branch = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    print("worktree HEAD:", head_branch)
    assert head_branch == branch
    assert subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip() == ""

    base_content = (wt / "src/main.py").read_text(encoding="utf-8")

    print("\n--- 5. Barrera 1: archivo fuera de contrato ---")
    st, res = http("POST", f"/api/v1/tasks/{task1['task_id']}/submit", {
        "file_changes": {"evil.py": "print('evil')\n"},
        "commit_message": "evil",
    }, bearer=s["session_key"])
    print("contract:", st, res)
    assert st == 403 and res["detail"]["error"] == "CONTRACT_VIOLATION"
    assert subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip() == ""

    print("\n--- 6. Barrera 2: dependencia prohibida ---")
    db_exec("UPDATE harness_tasks SET allowed_files = allowed_files || CAST(:extra AS JSONB) WHERE id = CAST(:tid AS UUID)",
            extra=json.dumps(["requirements.txt"]), tid=task1["task_id"])
    st, res = http("POST", f"/api/v1/tasks/{task1['task_id']}/submit", {
        "file_changes": {"requirements.txt": "fastapi==0.115.6\nrequests==2.32.0\n"},
        "commit_message": "deps",
    }, bearer=s["session_key"])
    print("dependency:", st, res)
    assert st == 403 and res["detail"]["error"] == "DEPENDENCY_FORBIDDEN"

    print("\n--- 7. Barrera 3a: error de sintaxis ---")
    st, res = http("POST", f"/api/v1/tasks/{task1['task_id']}/submit", {
        "file_changes": {"src/main.py": base_content + "\n\ndef broken(:\n    pass\n"},
        "commit_message": "broken",
    }, bearer=s["session_key"])
    print("syntax:", st, res)
    assert st == 422 and res["detail"]["error"] == "SYNTAX_CHECK_FAILED"
    print("memoria:", [m.get("barrier") for m in memory(task1["task_id"])])
    assert any(m.get("barrier") == "SYNTAX_CHECK_FAILED" for m in memory(task1["task_id"]))

    print("\n--- 8. Barrera 3b: simbolo alucinado ---")
    st, res = http("POST", f"/api/v1/tasks/{task1['task_id']}/submit", {
        "file_changes": {"src/main.py": base_content + "\n\ndef stage4_bad():\n    SessionRegisterRequest.metodo_alucinado()\n"},
        "commit_message": "hallucination",
    }, bearer=s["session_key"])
    print("hallucination:", st, res)
    assert st == 422 and res["detail"]["error"] == "HALLUCINATED_SYMBOL"
    assert any(m.get("barrier") == "HALLUCINATED_SYMBOL" for m in memory(task1["task_id"]))

    print("\n--- 9. Barrera 4: submit limpio, merge y reindex ---")
    helper = "stage4_verified_helper"
    if f"def {helper}" in base_content:
        helper = f"{helper}_{int(time.time())}"
    good_content = base_content + f"\n\ndef {helper}(x):\n    return x + 1\n"
    st, res = http("POST", f"/api/v1/tasks/{task1['task_id']}/submit", {
        "file_changes": {"src/main.py": good_content},
        "commit_message": f"add {helper}",
    }, bearer=s["session_key"])
    print("submit:", st, {k: res.get(k) for k in ("status", "task_commit", "main_commit", "changed_files", "reindex")})
    assert st == 200 and res["status"] == "MERGED"
    assert res["changed_files"] == ["src/main.py"]
    assert not wt.exists()

    events = []
    try:
        while True:
            events.append(json.loads(await asyncio.wait_for(ws.recv(), 3)))
            if events[-1].get("event") == "CLEAR_LLM_CONTEXT":
                break
    except Exception:
        pass
    print("WS post-submit:", events)
    assert any(e.get("event") == "CLEAR_LLM_CONTEXT" for e in events)

    task_row = db_rows("SELECT status FROM harness_tasks WHERE id = CAST(:tid AS UUID)", tid=task1["task_id"])[0]
    sess_row = db_rows("SELECT status, current_context_tokens FROM agent_sessions WHERE id = CAST(:sid AS UUID)", sid=s["session_id"])[0]
    locks = db_rows("SELECT count(*) AS n FROM repo_file_map WHERE locked_by_task_id = CAST(:tid AS UUID)", tid=task1["task_id"])[0]["n"]
    print("post-merge:", task_row["status"], dict(sess_row), "locks:", locks)
    assert task_row["status"] == "MERGED" and sess_row["status"] == "ONLINE" and sess_row["current_context_tokens"] == 0 and locks == 0

    merge_subject = git_repo("log", "-1", "--format=%s").stdout.strip()
    task_author = git_repo("log", "-1", "--format=%an <%ae>", "HEAD^2").stdout.strip()
    print("merge:", merge_subject)
    print("task author:", task_author)
    assert merge_subject.startswith("Merge ")
    assert "e2e-stage4-agent via opencode" in task_author
    assert f"def {helper}" in git_repo("show", "main:src/main.py").stdout

    commit_row = db_rows("SELECT commit_sha FROM repo_file_map WHERE repo_name='harness-self' AND file_path='src/main.py'")[0]
    print("map commit_sha:", commit_row["commit_sha"], "main_commit:", res["main_commit"])
    assert commit_row["commit_sha"] == res["main_commit"]

    st, val = http("POST", "/api/v1/guardrails/validate-symbols", {
        "repo_name": "harness-self", "referenced_symbols": [helper]
    }, admin=True)
    print("validate:", st, val)
    assert st == 200 and val["is_clean"] is True and helper in val["valid_symbols"]

    branches = git_repo("branch", "--list", branch).stdout.strip()
    print("branch restante:", branches or "(ninguna)")
    assert branches == ""

    print("\n--- 10. Cleanup ---")
    await ws.close()
    cleanup()
    counts = db_rows("""
        SELECT 'sessions' AS k, count(*) AS n FROM agent_sessions
        UNION ALL SELECT 'tasks', count(*) FROM harness_tasks
        UNION ALL SELECT 'plans', count(*) FROM feature_consensus_plans
        UNION ALL SELECT 'files', count(*) FROM repo_file_map WHERE repo_name='harness-self'
        UNION ALL SELECT 'vectors', count(*) FROM code_ast_vectors v JOIN repo_file_map m ON m.id=v.file_id WHERE m.repo_name='harness-self'
    """)
    print("counts finales:", {r["k"]: r["n"] for r in counts})
    print("\nE2E STAGE 3.5 + 4: OK")


asyncio.run(main())
