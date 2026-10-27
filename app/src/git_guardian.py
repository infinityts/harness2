import ast
import json
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

import sqlalchemy as sa

from src.indexer import REPOS_BASE_DIR, index_repository

WORKTREES_BASE_DIR = Path("/var/lib/harness/worktrees")

DEPENDENCY_MANIFESTS = {
    "requirements.txt", "pyproject.toml", "pipfile", "pipfile.lock", "poetry.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum"
}

# Un solo submit/merge a la vez dentro del proceso (uvicorn arranca 1 worker).
GIT_LOCK = threading.Lock()


class GitBarrierError(Exception):
    status_code = 400
    code = "GIT_BARRIER"

    def __init__(self, message: str, **details: Any):
        self.message = message
        self.details = details
        super().__init__(message)


class ContractViolationReal(GitBarrierError):
    status_code = 403
    code = "CONTRACT_VIOLATION"


class DependencyForbidden(GitBarrierError):
    status_code = 403
    code = "DEPENDENCY_FORBIDDEN"


class SyntaxCheckFailed(GitBarrierError):
    status_code = 422
    code = "SYNTAX_CHECK_FAILED"


class HallucinatedSymbol(GitBarrierError):
    status_code = 422
    code = "HALLUCINATED_SYMBOL"


class MergeConflictError(GitBarrierError):
    status_code = 409
    code = "MERGE_CONFLICT"


class NoChangesError(GitBarrierError):
    status_code = 400
    code = "NO_CHANGES"


class WorktreeError(GitBarrierError):
    status_code = 500
    code = "WORKTREE_ERROR"


class GitTaskPermissionError(Exception):
    pass


def _normalize(path: str) -> str:
    p = str(path).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def run_git(cwd: Path, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorktreeError("git no esta instalado") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        raise WorktreeError(f"git {' '.join(args[:2])} fallo: {detail}")
    return result


def prepare_task_worktree(repo_name: str, task_id: str, git_branch: str, base_branch: str = "main") -> Path:
    """Crea un Git Worktree fisico y aislado para la tarea partiendo de base_branch."""
    repo_path = REPOS_BASE_DIR / repo_name
    if not repo_path.exists():
        raise WorktreeError(f"Repository {repo_name} does not exist at {repo_path}")

    WORKTREES_BASE_DIR.mkdir(parents=True, exist_ok=True)
    wt_path = WORKTREES_BASE_DIR / task_id

    run_git(repo_path, ["config", "user.name", "Harness Control Plane"], check=False)
    run_git(repo_path, ["config", "user.email", "harness@controlplane.local"], check=False)

    if wt_path.exists():
        run_git(repo_path, ["worktree", "remove", "--force", str(wt_path)], check=False)
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
    run_git(repo_path, ["worktree", "prune"], check=False)
    run_git(repo_path, ["worktree", "add", "-B", git_branch, str(wt_path), base_branch], check=True)
    return wt_path


def cleanup_task_worktree(repo_name: str, task_id: str, git_branch: Optional[str] = None) -> None:
    repo_path = REPOS_BASE_DIR / repo_name
    wt_path = WORKTREES_BASE_DIR / task_id
    if repo_path.exists():
        run_git(repo_path, ["worktree", "remove", "--force", str(wt_path)], check=False)
    if wt_path.exists():
        shutil.rmtree(wt_path, ignore_errors=True)
    if repo_path.exists():
        run_git(repo_path, ["worktree", "prune"], check=False)
        if git_branch:
            run_git(repo_path, ["branch", "-D", git_branch], check=False)


def extract_python_qualified_calls_and_defs(source: str) -> Dict[str, Set[str]]:
    """Devuelve definiciones locales y llamadas Clase.metodo() detectadas por AST."""
    tree = ast.parse(source)
    local_defs: Set[str] = set()
    class_calls: Set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local_defs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            local_defs.add(node.name)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    local_defs.add(f"{node.name}.{item.name}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                caller = node.func.value.id
                method = node.func.attr
                if caller and caller[0].isupper():
                    class_calls.add(f"{caller}.{method}")

    return {"local_defs": local_defs, "class_calls": class_calls}


def _safe_target(wt: Path, rel_path: str) -> Path:
    rel = _normalize(rel_path)
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise ContractViolationReal("Ruta fuera del worktree", files=[rel_path])
    target = (wt / rel).resolve()
    wt_resolved = wt.resolve()
    if target != wt_resolved and wt_resolved not in target.parents:
        raise ContractViolationReal("Ruta fuera del worktree", files=[rel_path])
    return target


def _real_changed_files(wt: Path) -> List[str]:
    diff = run_git(wt, ["diff", "--name-only", "main"], check=True).stdout
    untracked = run_git(wt, ["ls-files", "--others", "--exclude-standard"], check=True).stdout
    files = set()
    for line in (diff + "\n" + untracked).splitlines():
        line = line.strip()
        if line:
            files.add(_normalize(line))
    return sorted(files)


def _revert_worktree(wt: Path) -> None:
    run_git(wt, ["reset", "--hard", "main"], check=False)
    run_git(wt, ["clean", "-fd"], check=False)


def _record_failure(
    engine: sa.Engine,
    task_id: str,
    session_id: Optional[str],
    barrier: str,
    message: str,
    files: Optional[List[str]] = None,
    status: str = "IN_PROGRESS",
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "barrier": barrier,
        "error_reason": message,
        "git_diff_summary": ", ".join(files or [])[:500] if files else None,
        "files": files or [],
    }
    with engine.begin() as conn:
        conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET failed_attempts_memory = failed_attempts_memory || CAST(:entry AS JSONB),
                    status = :status
                WHERE id = CAST(:tid AS UUID)
            """),
            {"entry": json.dumps(entry), "status": status, "tid": task_id}
        )


def _release_agent(engine: sa.Engine, session_id: Optional[str]) -> None:
    if not session_id:
        return
    with engine.begin() as conn:
        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'ONLINE', current_context_tokens = 0
                WHERE id = CAST(:sid AS UUID)
            """),
            {"sid": session_id}
        )


def _release_locks(engine: sa.Engine, task_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE locked_by_task_id = CAST(:tid AS UUID)"),
            {"tid": task_id}
        )


def _known_symbols(engine: sa.Engine, repo_name: str):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT file_path, exported_symbols FROM repo_file_map WHERE repo_name = :rname"),
            {"rname": repo_name}
        ).mappings().all()
    known_top: Set[str] = set()
    known_classes: Set[str] = set()
    known_qualified: Set[str] = set()
    for row in rows:
        for sym in (row["exported_symbols"] or []):
            name = sym.get("name")
            if name:
                known_top.add(name)
            if sym.get("qualified_name"):
                known_qualified.add(sym["qualified_name"])
            if sym.get("type") == "class" and name:
                known_classes.add(name)
    return known_top, known_classes, known_qualified


def _check_hallucinations(engine: sa.Engine, repo_name: str, wt: Path, changed_files: List[str]) -> None:
    known_top, known_classes, known_qualified = _known_symbols(engine, repo_name)
    local_defs: Set[str] = set()
    new_calls: Set[str] = set()

    for rel in changed_files:
        if not rel.endswith(".py"):
            continue
        path = wt / rel
        if not path.exists():
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            parsed = extract_python_qualified_calls_and_defs(source)
        except SyntaxError:
            continue
        local_defs |= parsed["local_defs"]

        base_source = ""
        if run_git(wt, ["cat-file", "-e", f"main:{rel}"], check=False).returncode == 0:
            base_source = run_git(wt, ["show", f"main:{rel}"], check=True).stdout
        base_calls: Set[str] = set()
        if base_source:
            try:
                base_calls = extract_python_qualified_calls_and_defs(base_source)["class_calls"]
            except SyntaxError:
                base_calls = set()
        new_calls |= (parsed["class_calls"] - base_calls)

    local_classes = {d for d in local_defs if "." not in d and d[:1].isupper()}
    bad = []
    for call in sorted(new_calls):
        if call in known_qualified or call in local_defs:
            continue
        cls = call.split(".", 1)[0]
        if cls in known_classes or cls in local_classes:
            bad.append(call)
    if bad:
        raise HallucinatedSymbol(
            "Llamada a metodo inexistente en una clase conocida del repositorio",
            files=bad
        )


def _apply_file_changes(wt: Path, file_changes: Optional[Dict[str, str]]) -> None:
    for rel, content in (file_changes or {}).items():
        target = _safe_target(wt, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _apply_unified_diff(wt: Path, unified_diff: Optional[str]) -> None:
    if not unified_diff or not unified_diff.strip():
        return
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8") as fh:
            fh.write(unified_diff)
            tmp_path = fh.name
        check = run_git(wt, ["apply", "--check", tmp_path], check=False)
        if check.returncode != 0:
            raise WorktreeError(f"unified_diff no aplica: {(check.stderr or check.stdout).strip()[:300]}")
        run_git(wt, ["apply", tmp_path], check=True)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _commit_worktree(wt: Path, message: str, author_name: str, author_email: str) -> str:
    run_git(wt, ["add", "-A"], check=True)
    result = run_git(
        wt,
        ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}", "commit", "-m", message],
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git commit fallo: {(result.stderr or result.stdout).strip()[:300]}")
    return run_git(wt, ["rev-parse", "HEAD"], check=True).stdout.strip()


def _merge_to_main(repo_name: str, git_branch: str, wt_path: Path) -> str:
    repo_path = REPOS_BASE_DIR / repo_name
    checkout = run_git(repo_path, ["checkout", "main"], check=False)
    if checkout.returncode != 0:
        raise WorktreeError(f"No se pudo cambiar a main: {(checkout.stderr or checkout.stdout).strip()[:300]}")
    dirty = run_git(repo_path, ["status", "--porcelain"], check=True).stdout.strip()
    if dirty:
        raise WorktreeError(f"Repo base con cambios sin commitear: {dirty[:300]}")

    merge = run_git(
        repo_path,
        ["merge", "--no-ff", git_branch, "-m", f"Merge {git_branch} into main"],
        check=False,
    )
    if merge.returncode != 0:
        conflicts = run_git(repo_path, ["diff", "--name-only", "--diff-filter=U"], check=False).stdout
        run_git(repo_path, ["merge", "--abort"], check=False)
        raise MergeConflictError(
            "Conflicto real de merge contra main",
            files=[line for line in conflicts.splitlines() if line.strip()]
        )

    main_commit = run_git(repo_path, ["rev-parse", "HEAD"], check=True).stdout.strip()
    run_git(repo_path, ["worktree", "remove", "--force", str(wt_path)], check=False)
    if wt_path.exists():
        shutil.rmtree(wt_path, ignore_errors=True)
    run_git(repo_path, ["branch", "-d", git_branch], check=False)
    run_git(repo_path, ["worktree", "prune"], check=False)
    return main_commit


def _load_task(engine: sa.Engine, task_id: str):
    with engine.connect() as conn:
        task = conn.execute(
            sa.text("""
                SELECT t.id::text AS id, t.status, t.assigned_session_id::text AS assigned_session_id,
                       t.title, t.git_branch, t.allowed_files, t.allow_dependency_changes,
                       p.master_plan_c_json->>'repo_name' AS repo_name
                FROM harness_tasks t
                JOIN feature_consensus_plans p ON p.id = t.plan_id
                WHERE t.id = CAST(:tid AS UUID)
            """),
            {"tid": task_id}
        ).mappings().fetchone()
        session = None
        if task and task["assigned_session_id"]:
            session = conn.execute(
                sa.text("SELECT engineer_username, agent_tool FROM agent_sessions WHERE id = CAST(:sid AS UUID)"),
                {"sid": task["assigned_session_id"]}
            ).mappings().fetchone()
    return task, session


def submit_task_changes(
    engine: sa.Engine,
    task_id: str,
    session_id: Optional[str],
    file_changes: Optional[Dict[str, str]],
    unified_diff: Optional[str],
    commit_message: str,
) -> Dict[str, Any]:
    """Aplica cambios reales, ejecuta las 4 barreras, mergea a main y reindexa."""
    with GIT_LOCK:
        return _submit_locked(engine, task_id, session_id, file_changes, unified_diff, commit_message)


def _submit_locked(
    engine: sa.Engine,
    task_id: str,
    session_id: Optional[str],
    file_changes: Optional[Dict[str, str]],
    unified_diff: Optional[str],
    commit_message: str,
) -> Dict[str, Any]:
    task, session = _load_task(engine, task_id)
    if not task:
        raise LookupError("task not found")
    if session_id and task["assigned_session_id"] != session_id:
        raise GitTaskPermissionError("task assigned to another session")
    if task["status"] not in ("IN_PROGRESS", "VALIDATING"):
        raise GitBarrierError(f"Tarea en estado {task['status']}; submit no permitido", status="INVALID_STATE")
    repo_name = task["repo_name"]
    if not repo_name:
        raise WorktreeError("La tarea no tiene repo_name en el plan")
    if not file_changes and not unified_diff:
        raise NoChangesError("submit sin file_changes ni unified_diff")

    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE harness_tasks SET status = 'VALIDATING' WHERE id = CAST(:tid AS UUID)"),
            {"tid": task_id}
        )

    wt_path = WORKTREES_BASE_DIR / task_id
    if not wt_path.exists():
        wt_path = prepare_task_worktree(repo_name, task_id, task["git_branch"])

    try:
        _apply_file_changes(wt_path, file_changes)
        _apply_unified_diff(wt_path, unified_diff)

        changed = _real_changed_files(wt_path)
        if not changed:
            raise NoChangesError("El submit no produjo cambios reales en Git")

        allowed = {_normalize(p) for p in (task["allowed_files"] or [])}
        outside = sorted(set(changed) - allowed)
        if outside:
            raise ContractViolationReal(
                "Archivos fuera de allowed_files (Git diff real)",
                files=outside
            )

        manifest_hits = [p for p in changed if Path(p).name.lower() in DEPENDENCY_MANIFESTS]
        if manifest_hits and not task["allow_dependency_changes"]:
            raise DependencyForbidden(
                "Modificacion de dependencias prohibida por contrato",
                files=manifest_hits
            )

        for rel in changed:
            if not rel.endswith(".py"):
                continue
            target = wt_path / rel
            if not target.exists():
                continue
            source = target.read_text(encoding="utf-8", errors="ignore")
            try:
                ast.parse(source)
            except SyntaxError as exc:
                raise SyntaxCheckFailed(
                    "Syntax check failed",
                    files=[rel],
                    line=exc.lineno,
                    column=exc.offset,
                    detail=exc.msg,
                ) from exc

        _check_hallucinations(engine, repo_name, wt_path, changed)

        author_name = f"{session['engineer_username']} via {session['agent_tool']}" if session else "harness-admin"
        author_email = f"{task['assigned_session_id'] or 'admin'}@harness.local"
        message = (commit_message or "").strip() or f"{task['title']} [{task_id[:8]}]"
        task_commit = _commit_worktree(wt_path, message, author_name, author_email)
    except MergeConflictError:
        raise
    except GitBarrierError as exc:
        _revert_worktree(wt_path)
        _record_failure(engine, task_id, session_id, exc.code, exc.message, exc.details.get("files"))
        raise
    except Exception as exc:
        _revert_worktree(wt_path)
        _record_failure(engine, task_id, session_id, "INTERNAL", str(exc))
        raise WorktreeError(f"Fallo interno en submit: {exc}") from exc

    try:
        main_commit = _merge_to_main(repo_name, task["git_branch"], wt_path)
    except MergeConflictError as exc:
        _record_failure(engine, task_id, session_id, exc.code, exc.message, exc.details.get("files"), status="CONFLICT")
        _release_locks(engine, task_id)
        _release_agent(engine, task["assigned_session_id"])
        raise

    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE harness_tasks SET status = 'MERGED' WHERE id = CAST(:tid AS UUID)"),
            {"tid": task_id}
        )
        conn.execute(
            sa.text("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE locked_by_task_id = CAST(:tid AS UUID)"),
            {"tid": task_id}
        )
        if task["assigned_session_id"]:
            conn.execute(
                sa.text("""
                    UPDATE agent_sessions
                    SET status = 'ONLINE', current_context_tokens = 0
                    WHERE id = CAST(:sid AS UUID)
                """),
                {"sid": task["assigned_session_id"]}
            )

    try:
        reindex = index_repository(engine, repo_name)
    except Exception as exc:
        reindex = {"error": str(exc)}

    return {
        "task_id": task_id,
        "status": "MERGED",
        "task_commit": task_commit,
        "main_commit": main_commit,
        "changed_files": changed,
        "author": {"name": author_name, "email": author_email},
        "assigned_session_id": task["assigned_session_id"],
        "reindex": reindex,
    }


def prepare_worktree_for_task(engine: sa.Engine, task_id: str) -> Dict[str, Any]:
    task, _ = _load_task(engine, task_id)
    if not task:
        raise LookupError("task not found")
    repo_name = task["repo_name"]
    if not repo_name:
        raise WorktreeError("La tarea no tiene repo_name en el plan")
    wt_path = prepare_task_worktree(repo_name, task_id, task["git_branch"])
    return {
        "task_id": task_id,
        "repo_name": repo_name,
        "git_branch": task["git_branch"],
        "worktree": str(wt_path),
    }
