# Etapa 10 — Licencias por Plan y Pool Hermes por Proyecto

## Migración

`app/alembic/versions/0012_license_hermes_pool.py`
(`revision=0012`, `down_revision=0011`).

No se modificó `0011` ni se recrearon las tablas RAG de la Etapa 9.

Añade:

- `system_license_policy` singleton (`id=1`) con plan y cuotas.
- `harness_projects.is_active_slot`.
- `agent_sessions.session_kind` (`INTERACTIVE` / `SYSTEM_WORKER`) y
  `worker_role`.
- `hermes_worker_jobs` con prioridad, intentos, lease, idempotencia y estados.
- Índice único de un worker Hermes por proyecto.
- Backfill: `is_headless_worker=true` → `SYSTEM_WORKER` / `DOCS_CURATOR`.

## Planes

Catálogo en `app/src/licensing.py`:

| Plan | Proyectos activos | Hermes vivos | Ingenieros/proyecto | Sesiones globales | Usuarios |
|---|---:|---:|---:|---:|---:|
| `PERSONAL_1` | 1 | 1 | 1 | 1 | 1 |
| `TEAM_3` | 3 | 3 | 3 | 6 | 5 |
| `BUSINESS_5` | 5 | 5 | 5 | 15 | 15 |
| `ENTERPRISE_10` | 10 | 10 | 10 | 30 | 30 |
| `MAX_20` | 10 | 10 | 20 | 60 | 100 |

- `ENTERPRISE_10` es el default.
- Los Admin no consumen cupo de `max_registered_users`; solo los `ENGINEER`.
- El cupo por proyecto cuenta **usuarios distintos** con sesión INTERACTIVE
  ONLINE/BUSY. El cupo global cuenta **sesiones**.
- Un downgrade se rechaza con `409` si los proyectos activos o los workers
  Hermes vivos exceden el nuevo plan. Los usuarios existentes se mantienen
  (grandfathering); solo se bloquean nuevas altas.

Mensajes canónicos:

```text
403 License Quota Reached: active projects (X/Y) for plan <plan>
403 License Quota Reached: engineers in project (X/Y) for plan <plan>
403 License Quota Reached: total engineer sessions (X/Y) for plan <plan>
403 License Quota Reached: registered users (X/Y) for plan <plan>
```

## Enforcement atómico

Los endpoints de alta/activación bloquean la fila singleton con
`SELECT ... FOR UPDATE` antes de contar y escribir. Esto evita que dos sesiones
concurrentes superen la cuota.

Puntos de control:

- `POST /api/v1/projects` antes de clonar/indexar.
- `POST /api/v1/projects/{id}/toggle-slot`.
- `POST /api/v1/portal/sessions`.
- `WS /ws/agent/{session_id}` al reconectar una sesión interactiva.
- `POST /api/v1/admin/users` y registro de nuevos ingenieros.

## Pool Hermes

Un solo contenedor `harness-hermes-worker` ejecuta un supervisor y un pool
acotado. No hay un contenedor por proyecto.

- Una sesión `SYSTEM_WORKER` con `agent_tool='hermes'` por proyecto activo.
- Las sesiones del pool no consumen cupo de ingenieros.
- El supervisor usa `max_parallel_rag_jobs` (default 3) como semáforo.
- Cada sesión tiene su propio bridge WebSocket, workspace, session key y
  contexto. El contenedor es capacidad compartida; la identidad no se comparte.
- Al terminar un job: `current_context_tokens=0`, estado `ONLINE`.
- El `docs-curator` determinista de la Etapa 9 se mantiene como fallback de
  gobernanza y no cuenta contra `max_hermes_sessions`.

### Cola

`hermes_worker_jobs`:

```text
RAG_BOOTSTRAP
RAG_INGEST_SOURCE
RAG_REFRESH_PROJECT
RAG_UPDATE_ARCHITECTURE
```

- Claim con `FOR UPDATE SKIP LOCKED`.
- Lease de 300 s y heartbeat cada 60 s.
- Hasta 3 intentos.
- Idempotencia por `(project_id, idempotency_key)`.
- Jobs de proyectos inactivos se cancelan y no se reclaman.

### Flujo de un job

1. El supervisor reclama el job y obtiene la session key del proyecto.
2. Escribe `BUSY` en el archivo de estado del bridge.
3. Ejecuta Hermes CLI (`-z`) con el mapa de `repo_file_map`.
4. Hermes genera `auto://hermes-architecture.md`.
5. El supervisor la ingesta con la session key del proyecto; el servidor aplica
   Secret Scrubber, chunking y embeddings.
6. Llama al bootstrap determinista interno para README/docs/ADR/runbook y
   `auto://architecture-overview.md`.
7. Reporta `COMPLETED`, resetea tokens y vuelve a `ONLINE`.
8. Si Hermes falla, hace fallback determinista y marca `fallback=true` en el
   resultado del job.

## Endpoints

| Método | Ruta | Permiso |
| --- | --- | --- |
| `GET` | `/api/v1/admin/license` | Admin |
| `PUT` | `/api/v1/admin/license` | Admin |
| `POST` | `/api/v1/projects/{id}/toggle-slot` | Admin |
| `POST` | `/api/v1/projects/{id}/documents/bootstrap` | Admin |
| `POST` | `/api/v1/internal/hermes/pool/sync` | Worker key |
| `POST` | `/api/v1/internal/hermes/jobs/claim` | Worker key |
| `POST` | `/api/v1/internal/hermes/jobs/{id}/heartbeat` | Worker key |
| `POST` | `/api/v1/internal/hermes/jobs/{id}/complete` | Worker key |
| `POST` | `/api/v1/internal/hermes/jobs/{id}/fail` | Worker key |
| `POST` | `/api/v1/internal/projects/{id}/rag/bootstrap` | Worker key |

La Worker key se configura con `HARNESS_WORKER_KEY`; si no está, se acepta la
Admin API key como fallback operativo.

## Portal

- Nueva vista **Licencia y Pool** para Admin: plan, cuotas editables, uso en
  tiempo real, workers Hermes y jobs recientes.
- Las tarjetas de proyecto muestran `Slot activo/inactivo` y el estado del
  worker Hermes.
- Botón para activar/desactivar el slot con enforcement de cuota.
- Base de Conocimiento muestra el estado del worker Hermes junto a los
  documentos.

## Contenedor

`docker-compose.yml` añade:

```text
harness-hermes-worker
  build: worker/Dockerfile
  image: localhost/harness-hermes-worker
  volume: ./worker/home:/opt/data
  volume: ./worker/workspaces:/opt/harness-worker/workspaces
  network: harness-net (172.28.0.30)
  cpus: 2
  mem: 2g
```

`worker/home/.env` debe contener las credenciales del proveedor (no se versiona).
`worker/home/config.yaml` define el modelo por defecto.

## Pruebas

```bash
podman cp tests/e2e_stage10_licensing_and_hermes_pool.py harness-app:/tmp/
podman exec harness-app python /tmp/e2e_stage10_licensing_and_hermes_pool.py
```

Cubre:

- `PERSONAL_1` (1 proyecto, 1 ingeniero) vs `TEAM_3` (3/3) y `MAX_20` (20).
- Toggle de slot.
- 3 workers Hermes online y RAG bootstrap real.
- Secret Scrubber, idempotencia y aislamiento cross-project.
- Context Pack híbrido.
- 10 tools MCP por stdio.
- UI del Portal.

Regresiones: Etapas 5, 6, 7, 7B, 8 y 9.

## Contexto

Esta etapa **no** implementa todavía el Context Governor completo para sesiones
interactivas. El worker Hermes usa contexto fresco por job y resetea tokens al
terminar. La compactación/checkpoint automático de sesiones de ingeniero queda
para la Etapa 11.
