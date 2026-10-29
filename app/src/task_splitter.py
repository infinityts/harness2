"""Splitter de tareas por simbolos y presupuesto de tokens.

Convierte una tarea NEEDS_SPLIT en varias tareas hijas autocontenidas, cada una
con su contrato, simbolos permitidos, presupuesto y dependencia secuencial.
"""
import json
import uuid
from typing import Any, Dict, List, Optional

import sqlalchemy as sa

from src.task_memory import record_memory
from src.token_budget import get_profile
from src.tokenizer_registry import count_for_profile


class TaskSplitError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _normalize_path(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _load_task(conn, task_id: str):
    return conn.execute(
        sa.text("""
            SELECT t.id::text AS task_id, t.plan_id::text AS plan_id, t.title,
                   t.status, t.required_skills, t.git_branch, t.allowed_files,
                   t.allowed_symbols, t.description, t.task_context,
                   t.acceptance_criteria, t.test_commands, t.depends_on,
                   t.estimated_input_tokens, t.max_input_tokens, t.context_manifest,
                   t.memory_epoch, t.task_kind, t.budget_status, t.split_status,
                   p.master_plan_c_json->>'repo_name' AS repo_name,
                   p.project_id::text AS project_id, p.requirement_prompt
            FROM harness_tasks t
            JOIN feature_consensus_plans p ON p.id = t.plan_id
            WHERE t.id = CAST(:tid AS UUID)
            FOR UPDATE OF t
        """),
        {"tid": task_id},
    ).mappings().first()


def _symbol_snippets(conn, repo_name: str, symbols: List[str]) -> Dict[str, str]:
    files = list(dict.fromkeys(s.split("::", 1)[0] for s in symbols if "::" in s))
    names = list(dict.fromkeys(s.split("::", 1)[1] for s in symbols if "::" in s))
    if not files or not names:
        return {}
    rows = conn.execute(
        sa.text("""
            SELECT m.file_path, v.symbol_name, v.code_snippet
            FROM code_ast_vectors v
            JOIN repo_file_map m ON m.id = v.file_id
            WHERE m.repo_name = :rname
              AND m.file_path = ANY(:files)
              AND v.symbol_name = ANY(:names)
        """),
        {"rname": repo_name, "files": files, "names": names},
    ).mappings().all()
    return {
        f"{_normalize_path(row['file_path'])}::{row['symbol_name']}": row["code_snippet"]
        for row in rows
    }


def _overhead_tokens(profile: Dict[str, Any], task) -> int:
    text = json.dumps({
        "title": task["title"],
        "description": task["description"],
        "task_context": task["task_context"],
        "acceptance_criteria": task["acceptance_criteria"],
        "test_commands": task["test_commands"],
        "requirement_prompt": task["requirement_prompt"],
    }, ensure_ascii=False, default=str)
    return count_for_profile(profile, text) + 1200 + 1500


def _group_symbols(
    profile: Dict[str, Any],
    symbols: List[str],
    snippets: Dict[str, str],
    available_tokens: int,
) -> List[List[str]]:
    grouped: List[List[str]] = []
    current: List[str] = []
    current_tokens = 0
    for symbol in symbols:
        snippet = snippets.get(symbol) or symbol
        tokens = max(1, count_for_profile(profile, snippet))
        if tokens > available_tokens:
            raise TaskSplitError(
                "SYMBOL_TOO_LARGE",
                f"El simbolo {symbol} consume {tokens} tokens y excede el presupuesto {available_tokens}",
                {"symbol": symbol, "symbol_tokens": tokens, "available_tokens": available_tokens},
            )
        if current and current_tokens + tokens > available_tokens:
            grouped.append(current)
            current = []
            current_tokens = 0
        current.append(symbol)
        current_tokens += tokens
    if current:
        grouped.append(current)
    return grouped


def split_task(
    engine: sa.Engine,
    task_id: str,
    max_input_tokens: Optional[int] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    with engine.begin() as conn:
        task = _load_task(conn, task_id)
        if not task:
            raise LookupError("task not found")
        if task["status"] != "PENDING":
            raise TaskSplitError(
                "TASK_NOT_PENDING",
                f"La tarea esta en estado {task['status']}; solo se divide una tarea PENDING",
            )
        symbols = list(task["allowed_symbols"] or [])
        if len(symbols) < 2:
            raise TaskSplitError(
                "TASK_NOT_SPLITTABLE",
                "La tarea necesita al menos 2 simbolos para dividirse por presupuesto",
                {"symbols": symbols},
            )
        manifest = task["context_manifest"] or {}
        profile_name = manifest.get("model_profile") or "local-32k"
        profile = get_profile(conn, profile_name)
        budget = int(max_input_tokens or task["max_input_tokens"] or profile["max_input_tokens"])
        budget = max(300, min(budget, int(profile["max_input_tokens"])))
        overhead = _overhead_tokens(profile, task)
        available = budget - overhead
        if available < 200:
            raise TaskSplitError(
                "OVERHEAD_TOO_LARGE",
                f"El contrato consume {overhead} tokens y no deja espacio en el presupuesto {budget}",
                {"overhead_tokens": overhead, "budget": budget},
            )
        snippets = _symbol_snippets(conn, task["repo_name"], symbols)
        groups = _group_symbols(profile, symbols, snippets, available)
        if len(groups) < 2:
            raise TaskSplitError(
                "TASK_FITS",
                "La tarea ya cabe en el presupuesto; no requiere split",
                {"estimated_tokens": overhead + sum(count_for_profile(profile, snippets.get(s, s)) for s in symbols)},
            )

        parent_deps = list(task["depends_on"] or [])
        child_ids = [str(uuid.uuid4()) for _ in groups]
        split_count = len(groups)
        children = []
        parent_branch = str(task["git_branch"] or f"harness/task-{task_id[:8]}")
        for idx, group in enumerate(groups, start=1):
            child_id = child_ids[idx - 1]
            files = list(dict.fromkeys(s.split("::", 1)[0] for s in group))
            dependency = parent_deps if idx == 1 else [child_ids[idx - 2]]
            group_tokens = sum(
                max(1, count_for_profile(profile, snippets.get(s, s))) for s in group
            )
            estimated = overhead + group_tokens
            branch = f"{parent_branch}-slice-{idx}"[:150]
            child_manifest = {
                **manifest,
                "split": {
                    "parent_task_id": task_id,
                    "split_index": idx,
                    "split_count": split_count,
                    "split_reason": "BUDGET_EXCEEDED",
                    "symbols": group,
                },
                "estimated_input_tokens": estimated,
                "max_input_tokens": budget,
            }
            conn.execute(
                sa.text("""
                    INSERT INTO harness_tasks (
                        id, plan_id, title, required_skills, git_branch,
                        allowed_files, allowed_symbols, allow_dependency_changes,
                        description, task_context, acceptance_criteria, test_commands,
                        depends_on, estimated_input_tokens, max_input_tokens,
                        context_manifest, memory_epoch, task_kind, budget_status,
                        parent_task_id, split_index, split_count, split_reason, split_status,
                        status, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:pid AS UUID), :title,
                        CAST(:skills AS JSONB), :branch, CAST(:afiles AS JSONB),
                        CAST(:symbols AS JSONB), :allow_dep,
                        :description, :task_context, CAST(:acceptance AS JSONB),
                        CAST(:tests AS JSONB), CAST(:depends_on AS JSONB),
                        :estimated, :max_input, CAST(:manifest AS JSONB), 0,
                        :task_kind, :budget_status,
                        CAST(:parent_id AS UUID), :split_index, :split_count,
                        'BUDGET_EXCEEDED', 'CHILD', 'PENDING', clock_timestamp(), NOW()
                    )
                """),
                {
                    "id": child_id,
                    "pid": task["plan_id"],
                    "title": f"{task['title']} [slice {idx}/{split_count}]"[:200],
                    "skills": json.dumps(task["required_skills"] or []),
                    "branch": branch,
                    "afiles": json.dumps(files),
                    "symbols": json.dumps(group),
                    "allow_dep": False,
                    "description": (
                        f"{task['description'] or task['title']}\n\n"
                        f"Slice {idx}/{split_count}: {', '.join(group)}."
                    ),
                    "task_context": task["task_context"],
                    "acceptance": json.dumps(task["acceptance_criteria"] or []),
                    "tests": json.dumps(task["test_commands"] or []),
                    "depends_on": json.dumps(dependency),
                    "estimated": estimated,
                    "max_input": budget,
                    "manifest": json.dumps(child_manifest),
                    "task_kind": task["task_kind"] or "IMPLEMENT",
                    "budget_status": "OK" if estimated <= budget else "NEEDS_SPLIT",
                    "parent_id": task_id,
                    "split_index": idx,
                    "split_count": split_count,
                },
            )
            children.append({
                "task_id": child_id,
                "title": f"{task['title']} [slice {idx}/{split_count}]"[:200],
                "allowed_files": files,
                "allowed_symbols": group,
                "depends_on": dependency,
                "estimated_input_tokens": estimated,
                "max_input_tokens": budget,
                "budget_status": "OK" if estimated <= budget else "NEEDS_SPLIT",
                "split_index": idx,
                "split_count": split_count,
            })

        conn.execute(
            sa.text("""
                UPDATE harness_tasks
                SET status = 'SPLIT',
                    split_status = 'PARENT',
                    split_reason = 'BUDGET_EXCEEDED',
                    split_count = :split_count,
                    budget_status = 'SPLIT',
                    updated_at = NOW()
                WHERE id = CAST(:tid AS UUID)
            """),
            {"split_count": split_count, "tid": task_id},
        )
        record_memory(
            conn, task_id, "SPLIT",
            f"Tarea dividida en {split_count} slices por presupuesto de tokens",
            details={
                "children": child_ids,
                "overhead_tokens": overhead,
                "available_tokens": available,
                "max_input_tokens": budget,
                "model_profile": profile["name"],
                "tokenizer_status": profile.get("tokenizer_status"),
            },
            created_by=created_by,
        )
        for child in children:
            record_memory(
                conn, child["task_id"], "SPLIT_INHERIT",
                f"Hereda contrato de la tarea padre {task_id}",
                details={"parent_task_id": task_id, "symbols": child["allowed_symbols"]},
                created_by=created_by,
            )

    return {
        "parent_task_id": task_id,
        "status": "SPLIT",
        "split_count": split_count,
        "overhead_tokens": overhead,
        "available_tokens": available,
        "max_input_tokens": budget,
        "model_profile": profile["name"],
        "tokenizer_status": profile.get("tokenizer_status"),
        "children": children,
    }


def get_split_tree(engine: sa.Engine, task_id: str) -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("""
                WITH RECURSIVE tree AS (
                    SELECT id::text, parent_task_id::text, title, status, split_status,
                           split_index, split_count, split_reason, budget_status,
                           allowed_symbols, estimated_input_tokens, max_input_tokens,
                           created_at
                    FROM harness_tasks
                    WHERE id = CAST(:tid AS UUID)
                    UNION ALL
                    SELECT t.id::text, t.parent_task_id::text, t.title, t.status,
                           t.split_status, t.split_index, t.split_count, t.split_reason,
                           t.budget_status, t.allowed_symbols, t.estimated_input_tokens,
                           t.max_input_tokens, t.created_at
                    FROM harness_tasks t
                    JOIN tree ON t.parent_task_id = CAST(tree.id AS UUID)
                )
                SELECT * FROM tree
                ORDER BY split_index ASC, created_at ASC
            """),
            {"tid": task_id},
        ).mappings().all()
    return {"root_task_id": task_id, "nodes": [dict(row) for row in rows]}
