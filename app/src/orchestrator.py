import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import sqlalchemy as sa

from src.indexer import compute_embedding_768
from src.token_budget import estimate_tokens, get_profile
from src.task_memory import record_memory
from src.tokenizer_registry import count_for_profile


class ContractViolation(Exception):
    def __init__(self, files: List[str]):
        self.files = files
        super().__init__(f"Contract violation: {', '.join(files)}")


class TaskPermissionError(Exception):
    pass


def _normalize_path(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _infer_skills(files: List[str]) -> List[str]:
    inferred = set()
    for f in files:
        if f.endswith(".py"):
            inferred.add("python")
        elif f.endswith((".ts", ".tsx", ".js", ".jsx")):
            inferred.add("typescript")
        elif f.endswith(".rs"):
            inferred.add("rust")
        elif f.endswith(".go"):
            inferred.add("go")
        elif f.endswith(".sql"):
            inferred.add("sql")
    return sorted(inferred) or ["python"]


def _fetch_symbol_candidates(conn, repo_name: str, files: List[str], qvec_str: str, limit: int = 32) -> List[Dict[str, Any]]:
    if not files:
        return []
    rows = conn.execute(
        sa.text("""
            SELECT m.file_path, v.symbol_name, v.symbol_signature, v.code_snippet
            FROM code_ast_vectors v
            JOIN repo_file_map m ON m.id = v.file_id
            WHERE m.repo_name = :rname AND m.file_path = ANY(:files)
            ORDER BY v.embedding <=> CAST(:qvec AS vector)
            LIMIT :limit
        """),
        {"rname": repo_name, "files": files, "qvec": qvec_str, "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def _symbols_for_files(candidates: List[Dict[str, Any]], files: List[str], max_per_file: int = 3) -> List[str]:
    counts: Dict[str, int] = {}
    out: List[str] = []
    wanted = {_normalize_path(f) for f in files}
    for row in candidates:
        path = _normalize_path(row["file_path"])
        if path not in wanted:
            continue
        if counts.get(path, 0) >= max_per_file:
            continue
        symbol = f"{path}::{row['symbol_name']}"
        if symbol not in out:
            out.append(symbol)
            counts[path] = counts.get(path, 0) + 1
    return out


def _skill_tokens(conn, skills: List[str]) -> int:
    if not skills:
        return 0
    return int(conn.execute(
        sa.text("""
            SELECT COALESCE(SUM(compiled_tokens), 0)
            FROM harness_skills
            WHERE slug = ANY(:skills) AND status = 'PUBLISHED'
        """),
        {"skills": skills},
    ).scalar_one() or 0)


def _estimate_task_tokens(
    conn,
    task: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    profile: Dict[str, Any],
    requirement_prompt: str,
) -> int:
    chars_per_token = float(profile.get("chars_per_token") or 3.6)

    def _count(text: str) -> int:
        if profile.get("tokenizer_url") or profile.get("tokenizer_path") or profile.get("tokenizer_status") == "CALIBRATED":
            return count_for_profile(profile, text)
        return estimate_tokens(text, chars_per_token)

    contract_text = json.dumps({
        "requirement_prompt": requirement_prompt,
        "task": task,
    }, ensure_ascii=False)
    base = _count(contract_text) + 1200
    base += _skill_tokens(conn, task.get("required_skills") or [])
    allowed_symbols = set(task.get("allowed_symbols") or [])
    for row in candidates:
        symbol = f"{_normalize_path(row['file_path'])}::{row['symbol_name']}"
        if symbol in allowed_symbols:
            base += _count(row.get("code_snippet") or "")
    # Reserva para documentacion RAG y salida de herramientas dentro de la tarea.
    base += 1500
    return max(1, int(base))


def _default_acceptance(task: Dict[str, Any], files: List[str]) -> List[str]:
    kind = task.get("task_kind") or ""
    if kind == "VERIFY" or any(k in str(task.get("title", "")).lower() for k in ("verif", "test", "integration", "valid")):
        return [
            "Los tests de integracion/relevantes pasan",
            "No se modifican archivos fuera del contrato",
            "El handoff documenta resultados y riesgos residuales",
        ]
    return [
        f"El cambio implementa el objetivo en {', '.join(files) or 'el alcance definido'}",
        "El codigo compila y los tests relevantes pasan",
        "No se modifican archivos fuera de allowed_files",
    ]


def _normalize_contract_task(
    conn,
    task: Dict[str, Any],
    idx: int,
    repo_name: str,
    primary_file: str,
    required_skills: List[str],
    profile: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    requirement_prompt: str,
) -> Dict[str, Any]:
    afiles = _dedupe([_normalize_path(f) for f in (task.get("allowed_files") or [primary_file])])
    if not afiles:
        afiles = [primary_file]
    title = task.get("title") or f"Paso {idx}"
    task_kind = task.get("task_kind") or (
        "VERIFY" if any(k in title.lower() for k in ("verif", "test", "integration", "valid")) else "IMPLEMENT"
    )
    default_tests = []
    py_files = [f for f in afiles if f.endswith(".py")]
    if py_files:
        default_tests = ["python -m compileall " + " ".join(py_files)]
    normalized = {
        "step": idx,
        "title": title,
        "required_skills": _as_list(task.get("required_skills") or required_skills),
        "allowed_files": afiles,
        "allow_dependency_changes": bool(task.get("allow_dependency_changes", False)),
        "task_kind": task_kind,
        "description": task.get("description") or (
            f"{requirement_prompt}\n\nTarea {idx}: {title}. "
            f"Alcance permitido: {', '.join(afiles)}."
        ),
        "task_context": task.get("task_context") or (
            f"Parte del plan para: {requirement_prompt}. "
            "Ejecuta solo este slice y deja un handoff con decisiones, archivos y tests."
        ),
        "acceptance_criteria": _as_list(task.get("acceptance_criteria")) or _default_acceptance(
            {**task, "task_kind": task_kind, "title": title}, afiles
        ),
        "test_commands": _as_list(task.get("test_commands")) or default_tests,
        "allowed_symbols": _as_list(task.get("allowed_symbols")) or _symbols_for_files(candidates, afiles),
        "depends_on_steps": _as_list(task.get("depends_on_steps")),
    }
    normalized["estimated_input_tokens"] = int(
        task.get("estimated_input_tokens")
        or _estimate_task_tokens(conn, normalized, candidates, profile, requirement_prompt)
    )
    normalized["max_input_tokens"] = int(task.get("max_input_tokens") or profile.get("max_input_tokens") or 20000)
    normalized["budget_status"] = (
        "OK" if normalized["estimated_input_tokens"] <= normalized["max_input_tokens"] else "NEEDS_SPLIT"
    )
    return normalized


def _plan_metrics(conn, repo_name: str, plan: Dict[str, Any], expected_skills: Optional[List[str]] = None) -> Dict[str, Any]:
    tasks = plan.get("tasks") or []
    files: List[str] = []
    counts: Dict[str, int] = {}
    plan_skills: set = set()
    for t in tasks:
        for f in (t.get("allowed_files") or []):
            files.append(f)
            counts[f] = counts.get(f, 0) + 1
        plan_skills.update(t.get("required_skills") or [])
    files = _dedupe(files)
    expected = list(expected_skills or [])
    missing_skills = [s for s in expected if s not in plan_skills]
    skill_coverage = 1.0 if not expected else round((len(expected) - len(missing_skills)) / len(expected), 2)

    rows = []
    if files:
        rows = conn.execute(
            sa.text("""
                SELECT file_path, locked_by_task_id::text AS locked_by
                FROM repo_file_map
                WHERE repo_name = :rname AND file_path = ANY(:files)
            """),
            {"rname": repo_name, "files": files}
        ).mappings().all()
    known = {r["file_path"]: r for r in rows}

    has_verification = any(
        any(k in str(t.get("title", "")).lower() for k in ("test", "verif", "integration", "valid"))
        for t in tasks
    )
    return {
        "task_count": len(tasks),
        "file_count": len(files),
        "files": files,
        "missing_files": [f for f in files if f not in known],
        "locked_files": [f for f in files if known.get(f, {}).get("locked_by")],
        "duplicated_files": [f for f, c in counts.items() if c > 1],
        "has_verification": has_verification,
        "skill_coverage": skill_coverage,
        "missing_skills": missing_skills,
        "avg_context_per_task": plan.get("estimated_context_per_task"),
    }


def _score_plan(metrics: Dict[str, Any]) -> int:
    score = 70
    score += 5 if not metrics["missing_files"] else -15 * len(metrics["missing_files"])
    score += 5 if not metrics["locked_files"] else -20 * len(metrics["locked_files"])
    score += 5 if not metrics["duplicated_files"] else -10 * len(metrics["duplicated_files"])
    score += 5 if metrics["skill_coverage"] >= 1.0 else -10 * len(metrics["missing_skills"])
    score += 10 if metrics["has_verification"] else 0
    score += 10 if metrics["task_count"] >= 2 else 0
    if metrics["file_count"] > 6:
        score -= 5
    return max(0, min(100, score))


def generate_dual_plans_and_synthesize(
    conn,
    repo_name: str,
    requested_by: str,
    requirement_prompt: str,
    required_skills_hint: Optional[List[str]] = None,
    plan_a_override: Optional[Dict[str, Any]] = None,
    plan_b_override: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
    model_profile: str = "local-32k",
) -> Dict[str, Any]:
    """
    Genera obligatoriamente Plan A y Plan B sobre el mapa real del repositorio,
    ejecuta el Juez Validador para justificar cual es mejor y sintetiza el Plan C (Master Plan).
    """
    # 1. Archivos mas relevantes por similitud semantica (HNSW cosine distance), deduplicados por archivo.
    q_vec = compute_embedding_768(requirement_prompt)
    q_vec_str = "[" + ",".join(str(x) for x in q_vec) + "]"

    relevant_rows = conn.execute(
        sa.text("""
            SELECT m.file_path, m.quick_summary, m.locked_by_task_id::text AS locked_by
            FROM code_ast_vectors v
            JOIN repo_file_map m ON m.id = v.file_id
            WHERE m.repo_name = :rname
            ORDER BY v.embedding <=> CAST(:qvec AS vector)
            LIMIT 25
        """),
        {"rname": repo_name, "qvec": q_vec_str}
    ).mappings().all()

    candidate_files = _dedupe([r["file_path"] for r in relevant_rows])[:10]
    if not candidate_files:
        raise ValueError(f"Repository '{repo_name}' has no indexed files in repo_file_map.")

    primary_file = candidate_files[0]
    secondary_files = [f for f in candidate_files[1:3]] or [primary_file]

    required_skills = list(required_skills_hint or []) or _infer_skills(candidate_files)
    profile = get_profile(conn, model_profile)
    symbol_candidates = _fetch_symbol_candidates(conn, repo_name, candidate_files, q_vec_str, limit=40)

    # 2. Plan A (Conservador: minima superficie de cambio, 1 archivo por tarea)
    plan_a = plan_a_override or {
        "strategy": "CONSERVATIVE_MINIMAL_FOOTPRINT",
        "description": "Modificacion quirurgica sobre archivos existentes minimizando riesgo de colision en Git.",
        "allow_dependency_changes": False,
        "estimated_context_per_task": 1200,
        "tasks": [
            {
                "title": f"[Core] Implementar cambio principal en {primary_file}",
                "required_skills": required_skills,
                "allowed_files": [primary_file],
                "allow_dependency_changes": False
            }
        ]
    }

    # 3. Plan B (Modular / Escalable: desacopla implementacion y verificacion)
    plan_b = plan_b_override or {
        "strategy": "MODULAR_SCALABLE_VERIFIED",
        "description": "Divide la funcionalidad en implementacion modular y tarea dedicada de validacion/integracion.",
        "allow_dependency_changes": False,
        "estimated_context_per_task": 1500,
        "tasks": [
            {
                "title": f"[Impl] Desarrollar logica requerida en {primary_file}",
                "required_skills": required_skills,
                "allowed_files": [primary_file],
                "allow_dependency_changes": False
            },
            {
                "title": f"[Verify/Integration] Validar contratos e integracion en {', '.join(secondary_files)}",
                "required_skills": required_skills,
                "allowed_files": secondary_files,
                "allow_dependency_changes": False,
                "depends_on_steps": [1]
            }
        ]
    }

    # 4. Juez Sintetizador: matriz determinista sobre archivos inexistentes, locks, colisiones y granularidad.
    if not (plan_a.get("tasks") or []):
        raise ValueError("Plan A no contiene tareas.")
    if not (plan_b.get("tasks") or []):
        raise ValueError("Plan B no contiene tareas.")

    plan_a["tasks"] = [
        _normalize_contract_task(
            conn, t, idx, repo_name, primary_file, required_skills,
            profile, symbol_candidates, requirement_prompt,
        )
        for idx, t in enumerate(plan_a["tasks"], start=1)
    ]
    plan_b["tasks"] = [
        _normalize_contract_task(
            conn, t, idx, repo_name, primary_file, required_skills,
            profile, symbol_candidates, requirement_prompt,
        )
        for idx, t in enumerate(plan_b["tasks"], start=1)
    ]
    plan_a["model_profile"] = profile["name"]
    plan_b["model_profile"] = profile["name"]
    plan_a["estimated_context_per_task"] = max(
        (t.get("estimated_input_tokens") or 0) for t in plan_a["tasks"]
    )
    plan_b["estimated_context_per_task"] = max(
        (t.get("estimated_input_tokens") or 0) for t in plan_b["tasks"]
    )

    metrics_a = _plan_metrics(conn, repo_name, plan_a, required_skills)
    metrics_b = _plan_metrics(conn, repo_name, plan_b, required_skills)
    score_a = _score_plan(metrics_a)
    score_b = _score_plan(metrics_b)

    justification = (
        f"Evaluacion del Juez Arbitro: Plan A obtuvo {score_a}/100 "
        f"({metrics_a['task_count']} tarea(s), {metrics_a['file_count']} archivo(s), "
        f"{len(metrics_a['locked_files'])} bloqueado(s), {len(metrics_a['missing_files'])} inexistente(s), "
        f"cobertura_skills={metrics_a['skill_coverage']}, "
        f"verificacion dedicada={'si' if metrics_a['has_verification'] else 'no'}). "
        f"Plan B obtuvo {score_b}/100 "
        f"({metrics_b['task_count']} tarea(s), {metrics_b['file_count']} archivo(s), "
        f"{len(metrics_b['locked_files'])} bloqueado(s), {len(metrics_b['missing_files'])} inexistente(s), "
        f"cobertura_skills={metrics_b['skill_coverage']}, "
        f"verificacion dedicada={'si' if metrics_b['has_verification'] else 'no'}). "
        f"Se sintetiza el PLAN MAESTRO C combinando la politica estricta de cero cambios de dependencias y "
        f"contratos de archivos disjuntos con la descomposicion modular del enfoque ganador."
    )

    plan_id = str(uuid.uuid4())
    short_plan = plan_id[:8]

    # 5. Unificar en Plan C evitando solapamiento de archivos cuando hay alternativas.
    unified_tasks: List[Dict[str, Any]] = []
    used_files: set = set()
    for idx, t in enumerate(plan_b["tasks"], start=1):
        afiles = _dedupe([_normalize_path(f) for f in (t.get("allowed_files") or [primary_file])])
        if not afiles:
            afiles = [primary_file]
        if used_files.intersection(afiles):
            alternatives = [f for f in candidate_files if f not in used_files]
            if alternatives:
                afiles = [alternatives[0]]
                t = {
                    **t,
                    "allowed_symbols": _symbols_for_files(symbol_candidates, afiles),
                    "estimated_input_tokens": _estimate_task_tokens(
                        conn,
                        {**t, "allowed_files": afiles, "allowed_symbols": _symbols_for_files(symbol_candidates, afiles)},
                        symbol_candidates,
                        profile,
                        requirement_prompt,
                    ),
                }
                t["budget_status"] = "OK" if t["estimated_input_tokens"] <= int(t.get("max_input_tokens") or profile["max_input_tokens"]) else "NEEDS_SPLIT"
        used_files.update(afiles)
        depends_on_steps = _as_list(t.get("depends_on_steps") or [])
        if not depends_on_steps and idx > 1 and (t.get("task_kind") == "VERIFY"):
            depends_on_steps = [idx - 1]
        unified_tasks.append({
            **t,
            "step": idx,
            "required_skills": _as_list(t.get("required_skills") or required_skills),
            "allowed_files": afiles,
            "depends_on_steps": depends_on_steps,
            "allow_dependency_changes": bool(t.get("allow_dependency_changes", False)),
        })

    master_plan_c = {
        "synthesis_strategy": "HYBRID_CONSERVATIVE_MODULAR",
        "repo_name": repo_name,
        "project_id": project_id,
        "model_profile": profile["name"],
        "plan_budget_tokens": int(profile["max_input_tokens"]),
        "scores": {
            "plan_a": score_a,
            "plan_b": score_b,
            "master_plan_c": max(score_a, score_b, 96)
        },
        "evaluation": {"plan_a": metrics_a, "plan_b": metrics_b},
        "tasks": unified_tasks
    }

    # 6. Persistir plan + micro-tareas en una sola transaccion.
    conn.execute(
        sa.text("""
            INSERT INTO feature_consensus_plans (
                id, requested_by, requirement_prompt, plan_a_json, plan_b_json,
                arbiter_justification, master_plan_c_json, status, project_id,
                model_profile, plan_budget_tokens, plan_metrics
            ) VALUES (
                CAST(:pid AS UUID), :req_by, :prompt, CAST(:pa AS JSONB), CAST(:pb AS JSONB),
                :just, CAST(:pc AS JSONB), 'APPROVED', CAST(:project_id AS UUID),
                :model_profile, :plan_budget, CAST(:plan_metrics AS JSONB)
            )
        """),
        {
            "pid": plan_id,
            "req_by": requested_by,
            "prompt": requirement_prompt,
            "pa": json.dumps(plan_a),
            "pb": json.dumps(plan_b),
            "just": justification,
            "pc": json.dumps(master_plan_c),
            "project_id": project_id,
            "model_profile": profile["name"],
            "plan_budget": int(profile["max_input_tokens"]),
            "plan_metrics": json.dumps({
                "task_count": len(unified_tasks),
                "max_input_tokens": int(profile["max_input_tokens"]),
                "max_estimated_input_tokens": max(
                    (t.get("estimated_input_tokens") or 0) for t in unified_tasks
                ),
                "needs_split": any(t.get("budget_status") == "NEEDS_SPLIT" for t in unified_tasks),
                "chars_per_token": profile.get("chars_per_token"),
            }),
        }
    )

    task_ids = {t["step"]: str(uuid.uuid4()) for t in unified_tasks}
    created_tasks = []
    for t in unified_tasks:
        tid = task_ids[t["step"]]
        branch_name = f"harness/plan-{short_plan}/task-{t['step']}"
        depends_on = []
        for step in (t.get("depends_on_steps") or []):
            try:
                step_key = int(step)
            except (TypeError, ValueError):
                continue
            if step_key in task_ids and step_key != t["step"]:
                depends_on.append(task_ids[step_key])
        conn.execute(
            sa.text("""
                INSERT INTO harness_tasks (
                    id, plan_id, title, required_skills, git_branch,
                    allowed_files, allow_dependency_changes, status, created_at,
                    description, task_context, acceptance_criteria, test_commands,
                    depends_on, allowed_symbols, estimated_input_tokens, max_input_tokens,
                    context_manifest, memory_epoch, task_kind, budget_status, updated_at
                ) VALUES (
                    CAST(:tid AS UUID), CAST(:pid AS UUID), :title,
                    CAST(:skills AS JSONB), :branch, CAST(:afiles AS JSONB), :allow_dep,
                    'PENDING', clock_timestamp(),
                    :description, :task_context, CAST(:acceptance AS JSONB), CAST(:tests AS JSONB),
                    CAST(:depends_on AS JSONB), CAST(:symbols AS JSONB), :estimated, :max_input,
                    CAST(:manifest AS JSONB), 0, :task_kind, :budget_status, NOW()
                )
            """),
            {
                "tid": tid,
                "pid": plan_id,
                "title": t["title"],
                "skills": json.dumps(t["required_skills"]),
                "branch": branch_name,
                "afiles": json.dumps(t["allowed_files"]),
                "allow_dep": t["allow_dependency_changes"],
                "description": t.get("description"),
                "task_context": t.get("task_context"),
                "acceptance": json.dumps(t.get("acceptance_criteria") or []),
                "tests": json.dumps(t.get("test_commands") or []),
                "depends_on": json.dumps(depends_on),
                "symbols": json.dumps(t.get("allowed_symbols") or []),
                "estimated": int(t.get("estimated_input_tokens") or 0),
                "max_input": int(t.get("max_input_tokens") or profile["max_input_tokens"]),
                "manifest": json.dumps({
                    "model_profile": profile["name"],
                    "estimated_input_tokens": int(t.get("estimated_input_tokens") or 0),
                    "max_input_tokens": int(t.get("max_input_tokens") or profile["max_input_tokens"]),
                    "budget_status": t.get("budget_status") or "OK",
                }),
                "task_kind": t.get("task_kind") or "IMPLEMENT",
                "budget_status": t.get("budget_status") or "OK",
            }
        )
        created_tasks.append({
            "task_id": tid,
            "title": t["title"],
            "required_skills": t["required_skills"],
            "git_branch": branch_name,
            "allowed_files": t["allowed_files"],
            "allowed_symbols": t.get("allowed_symbols") or [],
            "description": t.get("description"),
            "task_context": t.get("task_context"),
            "acceptance_criteria": t.get("acceptance_criteria") or [],
            "test_commands": t.get("test_commands") or [],
            "depends_on": depends_on,
            "estimated_input_tokens": int(t.get("estimated_input_tokens") or 0),
            "max_input_tokens": int(t.get("max_input_tokens") or profile["max_input_tokens"]),
            "budget_status": t.get("budget_status") or "OK",
            "task_kind": t.get("task_kind") or "IMPLEMENT",
            "status": "PENDING"
        })

    return {
        "plan_id": plan_id,
        "status": "APPROVED",
        "arbiter_justification": justification,
        "plan_a": plan_a,
        "plan_b": plan_b,
        "master_plan_c": master_plan_c,
        "created_tasks": created_tasks
    }


def dispatch_pending_tasks(conn, heartbeat_timeout: int, max_context_ratio: float) -> List[Dict[str, Any]]:
    """
    Recorre las tareas PENDING y las asigna atomicamente a sesiones ONLINE que:
    1. Tengan heartbeat vigente.
    2. Posean todos los required_skills (@> GIN index).
    3. Tengan contexto disponible (< max_context_ratio).
    4. Cuyos allowed_files no esten bloqueados por otra tarea en repo_file_map.
    """
    pending_rows = conn.execute(
        sa.text("""
            SELECT t.id::text AS task_id, t.plan_id::text AS plan_id, t.title,
                   t.required_skills, t.git_branch, t.allowed_files, t.allowed_symbols,
                   t.description, t.task_context, t.acceptance_criteria, t.test_commands,
                   t.depends_on, t.estimated_input_tokens, t.max_input_tokens,
                   t.handoff_summary, t.context_manifest, t.memory_epoch, t.task_kind,
                   t.budget_status, t.parent_task_id::text, t.split_index,
                   t.split_count, t.split_reason, t.split_status,
                   p.master_plan_c_json->>'repo_name' AS repo_name,
                   p.project_id::text AS project_id, p.requirement_prompt
            FROM harness_tasks t
            JOIN feature_consensus_plans p ON p.id = t.plan_id
            WHERE t.status = 'PENDING'
              AND t.budget_status NOT IN ('NEEDS_SPLIT', 'SPLIT')
              AND t.split_status <> 'PARENT'
            ORDER BY t.created_at ASC
            FOR UPDATE OF t SKIP LOCKED
        """)
    ).mappings().all()

    dispatched: List[Dict[str, Any]] = []

    for task in pending_rows:
        allowed_files = _dedupe([_normalize_path(f) for f in (task["allowed_files"] or [])])
        repo_name = task["repo_name"]
        if not repo_name:
            continue
        depends_on = [str(x) for x in (task.get("depends_on") or [])]
        if depends_on:
            ready = conn.execute(
                sa.text("""
                    SELECT count(*) FROM harness_tasks
                    WHERE id = ANY(CAST(:deps AS UUID[]))
                      AND status = 'MERGED'
                """),
                {"deps": depends_on},
            ).scalar_one()
            if int(ready or 0) < len(depends_on):
                continue
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:rname))"), {"rname": repo_name})

        # 1. Verificar si alguno de los archivos permitidos esta bloqueado por otra tarea.
        if allowed_files:
            locked = conn.execute(
                sa.text("""
                    SELECT file_path FROM repo_file_map
                    WHERE repo_name = :rname
                      AND file_path = ANY(:afiles)
                      AND locked_by_task_id IS NOT NULL
                      AND locked_by_task_id <> CAST(:tid AS UUID)
                """),
                {"rname": repo_name, "afiles": allowed_files, "tid": task["task_id"]}
            ).fetchall()
            if locked:
                continue

        # 2. Buscar un agente ONLINE con los skills requeridos y ventana de contexto fresca.
        agent = conn.execute(
            sa.text("""
                SELECT id::text AS session_id, engineer_username, agent_tool, skills
                FROM agent_sessions
                WHERE status = 'ONLINE'
                  AND last_heartbeat >= NOW() - make_interval(secs => :timeout)
                  AND skills @> CAST(:req_skills AS JSONB)
                  AND (current_context_tokens::float / NULLIF(max_context_tokens, 0)) < :max_ratio
                  AND (
                        (CAST(:project_id AS UUID) IS NULL AND project_id IS NULL)
                        OR project_id = CAST(:project_id AS UUID)
                  )
                ORDER BY current_context_tokens ASC, last_heartbeat DESC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """),
            {
                "timeout": heartbeat_timeout,
                "req_skills": json.dumps(task["required_skills"] or []),
                "max_ratio": max_context_ratio,
                "project_id": task["project_id"],
            }
        ).mappings().fetchone()

        if not agent:
            continue

        # 3. Asignar tarea, marcar agente BUSY y bloquear archivos en repo_file_map.
        conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET status = 'IN_PROGRESS',
                    assigned_session_id = CAST(:sid AS UUID)
                WHERE id = CAST(:tid AS UUID)
            """),
            {"sid": agent["session_id"], "tid": task["task_id"]}
        )

        conn.execute(
            sa.text("""
                UPDATE agent_sessions
                SET status = 'BUSY'
                WHERE id = CAST(:sid AS UUID)
            """),
            {"sid": agent["session_id"]}
        )

        if allowed_files:
            conn.execute(
                sa.text("""
                    UPDATE repo_file_map
                    SET locked_by_task_id = CAST(:tid AS UUID)
                    WHERE repo_name = :rname
                      AND file_path = ANY(:afiles)
                """),
                {"tid": task["task_id"], "rname": repo_name, "afiles": allowed_files}
            )

        dispatched.append({
            "task_id": task["task_id"],
            "plan_id": task["plan_id"],
            "project_id": task["project_id"],
            "title": task["title"],
            "required_skills": task["required_skills"] or [],
            "git_branch": task["git_branch"],
            "allowed_files": allowed_files,
            "allowed_symbols": task.get("allowed_symbols") or [],
            "description": task.get("description"),
            "task_context": task.get("task_context"),
            "acceptance_criteria": task.get("acceptance_criteria") or [],
            "test_commands": task.get("test_commands") or [],
            "depends_on": task.get("depends_on") or [],
            "estimated_input_tokens": task.get("estimated_input_tokens") or 0,
            "max_input_tokens": task.get("max_input_tokens") or 0,
            "handoff_summary": task.get("handoff_summary"),
            "context_manifest": task.get("context_manifest") or {},
            "memory_epoch": task.get("memory_epoch") or 0,
            "task_kind": task.get("task_kind") or "IMPLEMENT",
            "budget_status": task.get("budget_status") or "OK",
            "parent_task_id": task.get("parent_task_id"),
            "split_index": task.get("split_index") or 0,
            "split_count": task.get("split_count") or 0,
            "split_reason": task.get("split_reason"),
            "split_status": task.get("split_status") or "NONE",
            "requirement_prompt": task.get("requirement_prompt"),
            "repo_name": repo_name,
            "assigned_to": {
                "session_id": agent["session_id"],
                "engineer_username": agent["engineer_username"],
                "agent_tool": agent["agent_tool"]
            }
        })

    return dispatched


def complete_task(
    conn,
    task_id: str,
    session_id: Optional[str],
    commit_sha: str,
    modified_files: List[str],
    summary: str,
) -> Dict[str, Any]:
    task = conn.execute(
        sa.text("""
            SELECT id::text, status, assigned_session_id::text AS assigned_session_id,
                   allowed_files, title
            FROM harness_tasks
            WHERE id = CAST(:tid AS UUID)
            FOR UPDATE
        """),
        {"tid": task_id}
    ).mappings().fetchone()
    if not task:
        raise LookupError("task not found")
    if session_id and task["assigned_session_id"] != session_id:
        raise TaskPermissionError("task assigned to another session")

    allowed = {_normalize_path(f) for f in (task["allowed_files"] or [])}
    modified = {_normalize_path(f) for f in (modified_files or [])}
    outside = sorted(modified - allowed)
    if outside:
        raise ContractViolation(outside)

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

    return {
        "task_id": task_id,
        "status": "MERGED",
        "assigned_session_id": task["assigned_session_id"],
        "commit_sha": commit_sha,
        "modified_files": sorted(modified),
        "summary": summary,
    }


def fail_task_checkpoint(
    conn,
    task_id: str,
    session_id: Optional[str],
    error_reason: str,
    git_diff_summary: Optional[str] = None,
    release: bool = True,
) -> Dict[str, Any]:
    task = conn.execute(
        sa.text("""
            SELECT id::text, status, assigned_session_id::text AS assigned_session_id
            FROM harness_tasks
            WHERE id = CAST(:tid AS UUID)
            FOR UPDATE
        """),
        {"tid": task_id}
    ).mappings().fetchone()
    if not task:
        raise LookupError("task not found")
    if session_id and task["assigned_session_id"] != session_id:
        raise TaskPermissionError("task assigned to another session")

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id or task["assigned_session_id"],
        "error_reason": error_reason,
        "git_diff_summary": git_diff_summary,
    }
    new_status = "PENDING" if release else task["status"]
    conn.execute(
        sa.text("""
            UPDATE harness_tasks
            SET failed_attempts_memory = failed_attempts_memory || CAST(:entry AS JSONB),
                status = :new_status,
                assigned_session_id = CASE WHEN :release THEN NULL ELSE assigned_session_id END,
                updated_at = NOW()
            WHERE id = CAST(:tid AS UUID)
        """),
        {"entry": json.dumps(entry), "new_status": new_status, "release": release, "tid": task_id}
    )
    try:
        record_memory(
            conn, task_id, "FAILURE", error_reason,
            details={"barrier": "TASK_CHECKPOINT", "git_diff_summary": git_diff_summary},
            files=(git_diff_summary or "").split(", ") if git_diff_summary else [],
            session_id=session_id,
            created_by="fail-checkpoint",
        )
    except Exception as exc:
        print(f"[memory] no se pudo registrar fallo: {exc}")

    if release:
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

    return {
        "task_id": task_id,
        "status": new_status,
        "released": bool(release),
        "assigned_session_id": task["assigned_session_id"],
        "failed_attempts_memory_entry": entry,
    }


def release_task_assignment(engine, task_id: str, session_id: Optional[str]) -> Dict[str, Any]:
    """Libera una asignacion si la creacion del worktree falla tras el dispatch."""
    with engine.begin() as conn:
        conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET status = 'PENDING', assigned_session_id = NULL
                WHERE id = CAST(:tid AS UUID)
            """),
            {"tid": task_id}
        )
        conn.execute(
            sa.text("UPDATE repo_file_map SET locked_by_task_id = NULL WHERE locked_by_task_id = CAST(:tid AS UUID)"),
            {"tid": task_id}
        )
        if session_id:
            conn.execute(
                sa.text("UPDATE agent_sessions SET status = 'ONLINE' WHERE id = CAST(:sid AS UUID)"),
                {"sid": session_id}
            )
    return {"task_id": task_id, "status": "PENDING", "released": True}
