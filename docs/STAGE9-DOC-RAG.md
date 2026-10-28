# Etapa 9 — RAG Documental Nativo y Worker `docs-curator`

## Migración

`app/alembic/versions/0011_native_document_rag.py`
(`revision=0011`, `down_revision=0010`).

No se modificó ninguna revisión ya aplicada. La migración añade:

- `agent_sessions.is_headless_worker` y el índice parcial
  `uq_agent_sessions_headless_project` (un worker activo por proyecto).
- `harness_documents` con aislamiento por `project_id`, `source_uri` único por
  proyecto, checksum SHA-256 y trazabilidad de la sesión que actualizó.
- `harness_document_chunks` con `Vector(768)` e índice HNSW
  (`vector_cosine_ops`).
- Seed oficial `rag-docs-curator` (348 tokens, categoría `documents`).

## Ingesta idempotente y Secret Scrubber

`app/src/doc_rag.py` es la única puerta de escritura:

1. Limpia secretos con patrones deterministas (JWT, URLs de base de datos,
   `api_key`/`secret`/`password`/`token`, `Bearer`, claves AWS) y reemplaza por
   `[REDACTED_SECRET]`. También limpia `title`, `source_uri` y `metadata_json`.
2. Calcula SHA-256 del contenido limpio. Si el checksum no cambió, devuelve
   `SKIPPED` y no re-vectoriza.
3. Trocea Markdown por encabezados `#`/`##`, estima tokens y genera embeddings
   deterministas de 768 dimensiones (misma función que el índice de código).
4. Reemplaza documento y chunks en una sola transacción.

Las tablas tienen FK en cascada por proyecto y FK opcional a
`agent_sessions.id` (`ON DELETE SET NULL`) para auditar al worker.

## Worker headless gobernado

`app/src/doc_worker.py` no crea un sistema de autenticación paralelo:

- Reutiliza `agent_sessions` con `agent_tool='headless-worker'`,
  `is_headless_worker=true`, `project_id` exclusivo y skills
  `["tech-writer", "doc-analyzer", "rag-docs-curator"]`.
- Un proyecto solo puede tener un worker activo; el índice parcial lo garantiza.
- Aparece en `/api/v1/war-room/topology`, emite `HEADLESS_WORKER_READY` y
  `DOCS_BOOTSTRAP_COMPLETED` en `war_room_events`, y puede revocarse con el Kill
  Switch existente (`POST /api/v1/sessions/{id}/revoke`).
- Un heartbeat interno mantiene `last_heartbeat` al día; el watchdog general no
  lo marca OFFLINE.
- Si el Admin revoca el worker, los disparos automáticos se omiten. Un bootstrap
  explícito del Admin puede crear un reemplazo.

Disparadores:

- Alta de proyecto (`project_created`).
- `TASK_MERGED` en `POST /api/v1/tasks/{task_id}/submit` (`task_merged:<id>`).
- Acción manual del Admin desde el Portal o
  `POST /api/v1/projects/{project_id}/documents/bootstrap`.

## Bootstrap documental

`bootstrap_project_docs` escanea:

- `README*` en la raíz.
- `docs/**/*.md`, `docs/**/*.txt` y `openapi*.json`.
- Genera el esqueleto base cuando falta:
  - `repo://README.md`
  - `adr://0001-initial-architecture.md`
  - `runbook://development-contracts.md`
- Genera `auto://architecture-overview.md` desde `repo_file_map` con módulos,
  símbolos principales y contratos operativos cuando no existe una arquitectura
  canónica.

Todo el flujo es idempotente: un segundo bootstrap no crea ni re-vectoriza nada
si el contenido no cambió.

## Context Pack híbrido

`POST /api/v1/context/pack` acepta `include_docs` (por defecto `true`):

- Hasta 28% del `token_budget` se reserva a `relevant_doc_chunks`.
- El resto alimenta `repo_quick_map` y `surgical_ast_chunks`.
- La respuesta incluye `documentation_tokens_used` y
  `estimated_tokens_used`.
- La búsqueda documental filtra siempre por `project_id` del proyecto asociado
  al `repo_name`.

## Endpoints

| Método | Ruta | Permiso |
| --- | --- | --- |
| `POST` | `/api/v1/projects/{id}/documents/bootstrap` | Admin |
| `GET` | `/api/v1/projects/{id}/documents` | Admin o miembro aprobado |
| `POST` | `/api/v1/projects/{id}/documents/search` | Admin o miembro aprobado |
| `POST` | `/api/v1/projects/{id}/documents` | Admin, o agente con `rag-docs-curator`/`tech-writer` |

Un agente solo puede operar sobre el `project_id` de su sesión. Cualquier intento
cruzado responde `403 Cross-Project Isolation Violation`.

## Tools MCP

`app/client/harness_mcp_bridge.py` pasa de 7 a 10 tools:

- `harness_rag_ingest`: ingesta/actualización idempotente por `source_uri`.
- `harness_rag_search`: búsqueda HNSW en los chunks del proyecto de la sesión.
- `harness_rag_list`: inventario de documentos del proyecto.

Las tools resuelven el proyecto desde `agent_sessions`; no aceptan un
`project_id` arbitrario del modelo.

## Portal

La pestaña **Base de Conocimiento** (`/portal`, vista `knowledge`) permite:

- seleccionar un proyecto;
- listar documentos con fuente, tipo, chunks y fecha;
- buscar semánticamente en la documentación;
- ejecutar `docs-curator` con un clic (solo Admin).

## Prueba E2E

```bash
podman cp tests/e2e_stage9_doc_rag.py harness-app:/tmp/e2e_stage9_doc_rag.py
podman exec harness-app python /tmp/e2e_stage9_doc_rag.py
```

Cubre migración/índices, bootstrap y worker visible en topología, idempotencia,
redacción de secretos, aislamiento cross-project, context pack híbrido, tools MCP
y Kill Switch del worker.
