"""RAG documental nativo de Harness2.

PostgreSQL + pgvector es la fuente de verdad. La ingesta es idempotente por
checksum, limpia secretos antes de persistir y mantiene aislamiento por proyecto.
"""
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sqlalchemy as sa

from src.indexer import REPOS_BASE_DIR, compute_embedding_768

DOC_TYPES = ("README", "ADR", "ARCHITECTURE", "API_SPEC", "RUNBOOK", "REQUIREMENTS", "CHANGELOG", "OTHER")
MAX_DOC_BYTES = 2_000_000
MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP = 150
MAX_CHUNKS = 200

_SECRET_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?"),
    re.compile(r"(?:postgres(?:ql)?|mysql|redis)://[^\s\"']+", re.IGNORECASE),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|authorization)\b"
        r"\s*[:=]\s*[\"']?[^\s\"']{4,}[\"']?"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def scrub_secrets(text: str) -> str:
    cleaned = str(text or "")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
    return cleaned


def _scrub_metadata(value):
    if isinstance(value, str):
        return scrub_secrets(value)
    if isinstance(value, list):
        return [_scrub_metadata(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _scrub_metadata(v) for k, v in value.items()}
    return value


def content_checksum(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def _split_long_text(text: str, limit: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    pieces: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            candidates = [
                text.rfind("\n\n", start, end),
                text.rfind("\n", start, end),
                text.rfind(" ", start, end),
            ]
            cut = max(candidates)
            if cut > start + limit // 2:
                end = cut
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return pieces


def chunk_markdown(text: str) -> List[Dict[str, Any]]:
    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise ValueError("El documento no tiene contenido")
    heading_re = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
    matches = list(heading_re.finditer(content))
    sections: List[Tuple[Optional[str], int, str]] = []
    if not matches:
        sections.append((None, 1, content))
    else:
        if matches[0].start() > 0:
            sections.append((None, 1, content[:matches[0].start()].strip()))
        for idx, match in enumerate(matches):
            body_start = match.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
            heading = match.group(2).strip()
            body = content[body_start:body_end].strip()
            sections.append((heading, len(match.group(1)), body))

    chunks: List[Dict[str, Any]] = []
    chunk_index = 0
    for heading, level, body in sections:
        if not body.strip():
            # Una seccion que solo contiene un titulo no aporta contexto recuperable.
            continue
        header = f"{'#' * level} {heading}\n" if heading else ""
        full = (header + body).strip()
        if not full:
            continue
        for piece in _split_long_text(full):
            chunks.append({
                "chunk_index": chunk_index,
                "section_heading": heading or "Document",
                "chunk_text": piece,
                "token_estimate": estimate_tokens(piece),
            })
            chunk_index += 1
            if chunk_index > MAX_CHUNKS:
                raise ValueError(f"El documento supera {MAX_CHUNKS} chunks")
    if not chunks:
        raise ValueError("No se pudieron generar chunks del documento")
    return chunks


def _valid_doc_type(doc_type: str) -> str:
    normalized = str(doc_type or "OTHER").strip().upper()
    if normalized not in DOC_TYPES:
        raise ValueError(f"doc_type invalido: {doc_type}")
    return normalized


def ingest_document(
    conn,
    project_id: str,
    source_uri: str,
    title: str,
    doc_type: str,
    raw_content: str,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    replace: bool = True,
) -> Dict[str, Any]:
    source_uri = scrub_secrets(str(source_uri or "")).strip()
    title = scrub_secrets(str(title or source_uri or "Documento")).strip()[:255]
    if not source_uri or len(source_uri) > 500:
        raise ValueError("source_uri invalido")
    doc_type = _valid_doc_type(doc_type)
    if len(raw_content or "") > MAX_DOC_BYTES:
        raise ValueError("El documento supera el limite de 2MB")

    cleaned = scrub_secrets(raw_content)
    if not cleaned.strip():
        raise ValueError("El documento queda vacio despues del scrubbing")
    checksum = content_checksum(cleaned)
    metadata_json = _scrub_metadata(metadata or {})

    existing = conn.execute(
        sa.text("""
            SELECT id::text, content_checksum
            FROM harness_documents
            WHERE project_id = CAST(:pid AS UUID) AND source_uri = :source
            FOR UPDATE
        """),
        {"pid": project_id, "source": source_uri}
    ).mappings().fetchone()

    if existing and existing["content_checksum"] == checksum:
        conn.execute(
            sa.text("""
                UPDATE harness_documents
                SET title = :title, doc_type = :doc_type, metadata_json = CAST(:meta AS JSONB),
                    updated_by_session_id = CAST(:sid AS UUID), updated_at = NOW()
                WHERE id = CAST(:id AS UUID)
            """),
            {
                "title": title, "doc_type": doc_type,
                "meta": json.dumps(metadata_json, ensure_ascii=False),
                "sid": session_id, "id": existing["id"],
            }
        )
        return {
            "status": "SKIPPED",
            "document_id": existing["id"],
            "source_uri": source_uri,
            "content_checksum": checksum,
            "chunks": 0,
        }

    chunks = chunk_markdown(cleaned)
    for chunk in chunks:
        chunk["embedding"] = compute_embedding_768(chunk["chunk_text"])

    if existing:
        document_id = existing["id"]
        conn.execute(
            sa.text("""
                UPDATE harness_documents
                SET title = :title, doc_type = :doc_type, content_checksum = :checksum,
                    raw_content = :content, metadata_json = CAST(:meta AS JSONB),
                    updated_by_session_id = CAST(:sid AS UUID), updated_at = NOW()
                WHERE id = CAST(:id AS UUID)
            """),
            {
                "title": title, "doc_type": doc_type, "checksum": checksum,
                "content": cleaned, "meta": json.dumps(metadata_json, ensure_ascii=False),
                "sid": session_id, "id": document_id,
            }
        )
        conn.execute(
            sa.text("DELETE FROM harness_document_chunks WHERE document_id = CAST(:id AS UUID)"),
            {"id": document_id}
        )
        status = "UPDATED"
    else:
        document_id = str(uuid.uuid4())
        conn.execute(
            sa.text("""
                INSERT INTO harness_documents
                    (id, project_id, source_uri, title, doc_type, content_checksum,
                     raw_content, metadata_json, updated_by_session_id, created_at, updated_at)
                VALUES
                    (CAST(:id AS UUID), CAST(:pid AS UUID), :source, :title, :doc_type,
                     :checksum, :content, CAST(:meta AS JSONB), CAST(:sid AS UUID), NOW(), NOW())
            """),
            {
                "id": document_id, "pid": project_id, "source": source_uri,
                "title": title, "doc_type": doc_type, "checksum": checksum,
                "content": cleaned, "meta": json.dumps(metadata_json, ensure_ascii=False),
                "sid": session_id,
            }
        )
        status = "CREATED"

    for chunk in chunks:
        vec = "[" + ",".join(str(x) for x in chunk["embedding"]) + "]"
        conn.execute(
            sa.text("""
                INSERT INTO harness_document_chunks
                    (document_id, project_id, chunk_index, section_heading,
                     chunk_text, token_estimate, embedding)
                VALUES
                    (CAST(:did AS UUID), CAST(:pid AS UUID), :idx, :heading,
                     :text, :tokens, CAST(:vec AS vector))
            """),
            {
                "did": document_id, "pid": project_id, "idx": chunk["chunk_index"],
                "heading": chunk["section_heading"], "text": chunk["chunk_text"],
                "tokens": chunk["token_estimate"], "vec": vec,
            }
        )

    return {
        "status": status,
        "document_id": document_id,
        "source_uri": source_uri,
        "content_checksum": checksum,
        "chunks": len(chunks),
        "tokens": sum(c["token_estimate"] for c in chunks),
    }


def list_documents(conn, project_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        sa.text("""
            SELECT d.id::text, d.project_id::text, d.source_uri, d.title, d.doc_type,
                   d.content_checksum, d.metadata_json, d.updated_by_session_id::text,
                   d.created_at, d.updated_at,
                   COALESCE(count(c.id), 0) AS chunks,
                   COALESCE(sum(c.token_estimate), 0) AS tokens
            FROM harness_documents d
            LEFT JOIN harness_document_chunks c ON c.document_id = d.id
            WHERE d.project_id = CAST(:pid AS UUID)
            GROUP BY d.id
            ORDER BY d.doc_type, d.source_uri
        """),
        {"pid": project_id}
    ).mappings().all()
    return [dict(row) for row in rows]


def search_documents(
    conn,
    project_id: str,
    query: str,
    limit: int = 5,
    qvec: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    vector = qvec or compute_embedding_768(query)
    qvec_str = "[" + ",".join(str(x) for x in vector) + "]"
    rows = conn.execute(
        sa.text("""
            SELECT c.id, c.document_id::text, c.project_id::text,
                   d.source_uri, d.title, d.doc_type,
                   c.chunk_index, c.section_heading, c.chunk_text,
                   c.token_estimate,
                   ROUND((1 - (c.embedding <=> CAST(:qvec AS vector)))::numeric, 4)::float AS similarity
            FROM harness_document_chunks c
            JOIN harness_documents d ON d.id = c.document_id
            WHERE c.project_id = CAST(:pid AS UUID)
              AND d.project_id = CAST(:pid AS UUID)
            ORDER BY c.embedding <=> CAST(:qvec AS vector) ASC
            LIMIT :limit
        """),
        {"qvec": qvec_str, "pid": project_id, "limit": max(1, min(limit, 50))}
    ).mappings().all()
    return [dict(row) for row in rows]


def _infer_doc_type(path: Path, source_uri: str) -> str:
    low = source_uri.lower()
    name = path.name.lower()
    if name.startswith("readme"):
        return "README"
    if "/adr/" in low or name.startswith("adr-") or "adr" in low:
        return "ADR"
    if "architecture" in low or "arquitectura" in low:
        return "ARCHITECTURE"
    if "openapi" in low or "swagger" in low or "/api" in low:
        return "API_SPEC"
    if "runbook" in low or "ops/" in low:
        return "RUNBOOK"
    if "requirement" in low or "requisito" in low:
        return "REQUIREMENTS"
    if "changelog" in low or "changes" in low:
        return "CHANGELOG"
    return "OTHER"


def _title_from_content(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:255]
    return fallback[:255]


def _collect_repo_docs(repo_path: Path) -> List[Tuple[Path, str]]:
    found: List[Tuple[Path, str]] = []
    seen = set()
    if not repo_path.exists():
        return found
    root_files = [p for p in repo_path.iterdir() if p.is_file()]
    for path in sorted(root_files):
        if path.name.lower().startswith("readme"):
            source = "repo://README.md"
            if source not in seen:
                found.append((path, source))
                seen.add(source)
    candidates: List[Path] = []
    docs_dir = repo_path / "docs"
    if docs_dir.exists():
        candidates.extend(docs_dir.rglob("*"))
    candidates.extend(repo_path.glob("openapi*.json"))
    candidates.extend(repo_path.glob("**/openapi*.json"))
    for path in sorted(candidates):
        if not path.is_file():
            continue
        if any(part in (".git", "node_modules", "worktrees", "__pycache__") for part in path.parts):
            continue
        rel = path.relative_to(repo_path).as_posix()
        source = f"repo://{rel}"
        if source in seen:
            continue
        suffix = path.suffix.lower()
        if suffix not in (".md", ".txt", ".json") and not path.name.lower().startswith("openapi"):
            continue
        if path.stat().st_size > MAX_DOC_BYTES:
            continue
        found.append((path, source))
        seen.add(source)
    return found


def _synthetic_readme(project_name: str, description: Optional[str]) -> str:
    return f"""# {project_name}

{description or 'Documentación base del proyecto.'}

## Estado

Proyecto inicializado en Harness2. La documentación se mantiene como fuente de
verdad en el repositorio y se sincroniza al RAG documental.

## Contratos

- Aislamiento estricto por proyecto.
- Cambios de comportamiento requieren actualización documental.
- No se almacenan secretos en documentos.
"""


def _synthetic_adr(project_name: str) -> str:
    return f"""# ADR 0001: Arquitectura inicial de {project_name}

## Estado

Propuesto.

## Contexto

El proyecto se inicia con Harness2 como plano de control y requiere una base
documental canónica antes de crecer.

## Decisión

Mantener documentación versionada en el repositorio, indexada en el RAG por
proyecto, con fuente canónica y checksum.

## Consecuencias

- Cada cambio relevante debe actualizar su documento.
- El agente recibe documentación bajo demanda, no todos los manuales.
- Los secretos se eliminan antes de indexar.
"""


def _synthetic_runbook(project_name: str) -> str:
    return f"""# Contratos de desarrollo de {project_name}

1. No ejecutar DDL manual: usar Alembic.
2. Cada cambio de comportamiento actualiza documentación y pruebas.
3. No incluir secretos, tokens ni credenciales en documentos.
4. Los agentes trabajan con una sesión y un proyecto; no cruzar proyectos.
5. Registrar evidencia de pruebas y verificación antes de cerrar una tarea.
"""


def _architecture_overview(conn, project_id: str, repo_name: str, project_name: str) -> str:
    rows = conn.execute(
        sa.text("""
            SELECT file_path, quick_summary, exported_symbols
            FROM repo_file_map WHERE repo_name = :rname
            ORDER BY file_path ASC LIMIT 120
        """),
        {"rname": repo_name}
    ).mappings().all()
    lines = [f"# Arquitectura actual de {project_name}", "", "## Módulos indexados", ""]
    for row in rows:
        lines.append(f"- `{row['file_path']}`: {row['quick_summary']}")
    lines.extend(["", "## Símbolos principales", ""])
    symbol_count = 0
    for row in rows:
        for symbol in (row["exported_symbols"] or []):
            if symbol_count >= 80:
                break
            name = symbol.get("name") if isinstance(symbol, dict) else str(symbol)
            kind = symbol.get("type") if isinstance(symbol, dict) else "symbol"
            lines.append(f"- `{name}` ({kind}) en `{row['file_path']}`")
            symbol_count += 1
        if symbol_count >= 80:
            break
    lines.extend([
        "", "## Contratos operativos", "",
        "- Aislamiento estricto por proyecto.",
        "- Documentación e índice de código son artefactos separados.",
        "- El RAG recibe solo documentos canónicos y sin secretos.",
        "- Los cambios de comportamiento actualizan esta visión general.",
    ])
    return "\n".join(lines)


def bootstrap_project_docs(
    conn,
    project_id: str,
    repo_name: str,
    session_id: Optional[str],
    trigger: str = "manual",
) -> Dict[str, Any]:
    project = conn.execute(
        sa.text("""
            SELECT project_slug, display_name, description
            FROM harness_projects WHERE id = CAST(:pid AS UUID)
        """),
        {"pid": project_id}
    ).mappings().fetchone()
    if not project:
        raise ValueError("Project not found")

    base = REPOS_BASE_DIR.resolve()
    repo_path = (base / repo_name).resolve()
    if repo_path.parent != base:
        raise ValueError("repo fuera del directorio permitido")

    summary: Dict[str, Any] = {
        "project_id": project_id,
        "repo_name": repo_name,
        "trigger": trigger,
        "session_id": session_id,
        "scanned": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "generated": [],
        "errors": [],
        "documents": [],
    }
    known_sources = set()

    for path, source_uri in _collect_repo_docs(repo_path):
        summary["scanned"] += 1
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            title = _title_from_content(content, path.name)
            result = ingest_document(
                conn, project_id, source_uri, title,
                _infer_doc_type(path, source_uri), content,
                {"trigger": trigger, "path": path.relative_to(repo_path).as_posix(), "generated": False},
                session_id,
            )
            known_sources.add(source_uri)
            summary[result["status"].lower()] += 1
            summary["documents"].append(result)
        except Exception as exc:
            summary["errors"].append({"source": source_uri, "error": str(exc)})

    project_name = project["display_name"] or repo_name
    synthetic = [
        ("repo://README.md", "README", project_name, _synthetic_readme(project_name, project["description"])),
        ("adr://0001-initial-architecture.md", "ADR", f"ADR 0001: Arquitectura inicial de {project_name}", _synthetic_adr(project_name)),
        ("runbook://development-contracts.md", "RUNBOOK", f"Contratos de desarrollo de {project_name}", _synthetic_runbook(project_name)),
    ]
    for source_uri, doc_type, title, content in synthetic:
        if source_uri in known_sources:
            continue
        try:
            result = ingest_document(
                conn, project_id, source_uri, title, doc_type, content,
                {"trigger": trigger, "generated": True}, session_id,
            )
            known_sources.add(source_uri)
            summary[result["status"].lower()] += 1
            summary["generated"].append(source_uri)
            summary["documents"].append(result)
        except Exception as exc:
            summary["errors"].append({"source": source_uri, "error": str(exc)})

    try:
        content = _architecture_overview(conn, project_id, repo_name, project_name)
        result = ingest_document(
            conn, project_id, "auto://architecture-overview.md",
            f"Arquitectura actual de {project_name}", "ARCHITECTURE", content,
            {"trigger": trigger, "generated": True, "source": "repo_file_map"},
            session_id,
        )
        summary[result["status"].lower()] += 1
        summary["generated"].append("auto://architecture-overview.md")
        summary["documents"].append(result)
    except Exception as exc:
        summary["errors"].append({"source": "auto://architecture-overview.md", "error": str(exc)})

    summary["documents_count"] = len(list_documents(conn, project_id))
    return summary
