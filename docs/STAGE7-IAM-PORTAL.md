# Etapa 7 — IAM, Proyectos, Sesiones 1:1 y Portal Responsive

Esta etapa agrega gobierno de usuarios por correo, gestión automática de proyectos,
aislamiento estricto por proyecto y una interfaz web unificada en `/portal` y
`/war-room`.

## Migraciones

| Revisión | Contenido |
| --- | --- |
| `0005` | `harness_users`, `harness_projects`, `project_memberships`; columnas `agent_sessions.user_id/project_id` y `feature_consensus_plans.project_id`; seed del admin y de `harness-self`. |
| `0006` | `agent_sessions.session_key_vault` para poder regenerar el config-bundle de una sesión sin guardar la clave en claro. |
| `0007` | `system_smtp_config` y `email_dispatch_logs` para correo transaccional y auditoría. Ver [`STAGE7-SMTP.md`](./STAGE7-SMTP.md). |
| `0008` | Seed idempotente de `admin@local` / `Super Administrator` para primer acceso. Ver [`STAGE7-PASSWORDS.md`](./STAGE7-PASSWORDS.md). |

La clave de sesión se cifra con SHA-256-CTR + HMAC-SHA256 usando
`HARNESS_VAULT_SECRET` (o `HARNESS_JWT_SECRET` si no se define). Solo se descifra
cuando el dueño de la sesión o un administrador pide el config-bundle.

## Autenticación de portal

- `POST /api/v1/auth/login`: email + contraseña, valida PBKDF2-HMAC-SHA256,
  `status = ACTIVE` y emite JWT de portal (`sub`, `email`, `role`, `typ=portal`,
  `exp`, `jti`).
- `POST /api/v1/auth/register-invite`: activa una cuenta `INVITED` con
  `invite_token`; si el correo no existe, crea un `ENGINEER` en `PENDING` a la
  espera de aprobación administrativa.
- `GET /api/v1/auth/me`: perfil, proyectos con membresía `APPROVED` y sesiones
  activas por proyecto.

El header `X-Harness-Admin-Key` sigue disponible para scripts y automatización.
Un JWT de portal con rol `ADMIN` también es aceptado en los endpoints
administrativos y en el WebSocket del War Room.

## Proyectos y membresías

- `POST /api/v1/projects` (ADMIN): crea el registro y el repositorio. Sin
  `repo_url` ejecuta `git init -b <branch>`, crea `README.md`, hace el commit
  inicial y llama a `index_repository()` en el acto. Con `repo_url`, clona.
- `GET /api/v1/projects`: ADMIN ve todos los proyectos, miembros y sesiones;
  ENGINEER solo los proyectos con membresía `APPROVED`.
- `POST /api/v1/projects/{id}/members` (ADMIN): invita o asigna por email o
  `user_id`, define `allowed_skills`, crea el usuario si no existe y devuelve
  `initial_password` o `invite_token` cuando corresponde.
- `DELETE /api/v1/projects/{id}/members/{user_id}` (ADMIN): revoca la membresía y
  marca `REVOKED` las sesiones abiertas de ese usuario en ese proyecto.

## Sesiones 1:1 y aislamiento

`POST /api/v1/portal/sessions` exige `project_id`, herramienta, proveedor LLM y
`max_context_tokens`. Antes de crear la sesión:

1. Verifica `project_memberships.status = 'APPROVED'`; si no, responde
   `403 Project Access Not Approved by Administrator`.
2. Rota la sesión anterior del mismo usuario + proyecto + herramienta.
3. Firma un JWT de sesión con `sub=session_id`, `user_id`, `project_id`,
   `project_slug`, `jti` y `exp`.

Los endpoints de agente comparan el proyecto de la sesión con el recurso:

- `POST /api/v1/context/pack`
- `POST /api/v1/guardrails/validate-symbols`
- `GET /api/v1/repos/{repo_name}/map`
- `POST /api/v1/tasks/{task_id}/submit`
- `POST /api/v1/war-room/relay`

Cualquier intento cruzado responde
`403 Cross-Project Isolation Violation`. El dispatcher solo considera sesiones
`ONLINE` cuyo `project_id` coincide con el plan de la tarea.

`GET /api/v1/portal/sessions/{id}/config-bundle` devuelve, solo al dueño o a un
ADMIN, los bloques `cursor_mcp_json`, `claudecode_mcp_json`, `opencode_json`,
`hermes_cli_env` y el comando `curl` de descarga de `harness_mcp_bridge.py`,
pre-rellenados con `HARNESS_URL`, `HARNESS_SESSION_ID`, `HARNESS_SESSION_KEY` y
`HARNESS_PROJECT_SLUG`.

## Portal Responsive

`GET /portal`, `GET /war-room` y `GET /` sirven la misma aplicación:

- **Login** por correo/contraseña y pestaña alternativa para `X-Harness-Admin-Key`.
- **ADMIN**
  - Proyectos: alta con inicialización Git + indexación automática.
  - Ingenieros y Accesos: invitación por correo, selección de proyectos y skills,
    revocación inmediata.
  - Sala de Control: filtro global o por proyecto, grafo SVG en vivo, barras de
    contexto, enlaces, inyección de skills/reglas, purge, kill switch,
    Dual Plan + Dispatch y consola de eventos.
- **ENGINEER**
  - Solo sus proyectos aprobados, una tarjeta por proyecto.
  - Una sesión independiente por proyecto/herramienta.
  - Configuración MCP/CLI con botones de copiado para Cursor, Claude Code,
    OpenCode, Hermes y descarga del bridge.

### Breakpoints

- `@media (min-width: 1200px)`: layout de 3 columnas (sidebar + canvas/tareas +
  inspector/consola).
- `@media (max-width: 1024px)`: drawer táctil, navegación inferior, tarjetas
  apiladas y `grid-template-columns: 1fr`.
- `@media (max-width: 600px)`: una columna, grafo `viewBox` escalable, tablas con
  scroll horizontal suave y áreas táctiles mínimas de 44px.

## Pruebas

```bash
podman cp tests/e2e_stage7.py harness-app:/tmp/e2e_stage7.py
podman exec harness-app python /tmp/e2e_stage7.py
```

La prueba valida migración 0005/0006/0007, login de admin, creación de `proj-alpha` y
`proj-beta` con commit inicial e índice, alta de `dev1@empresa.com`, aislamiento
403, config-bundle, segunda sesión por proyecto, dispatch 1:1 y los breakpoints
del portal. `tests/e2e_stage7_smtp.py` cubre configuración SMTP, cifrado,
correo de prueba, fallo auditado y correo de invitación.

## Operación y seguridad

- Rotar `HARNESS_INITIAL_ADMIN_PASSWORD` y la contraseña del admin sembrado antes
  de exponer el portal fuera de la red de confianza.
- Definir `HARNESS_VAULT_SECRET` propio; cambiarlo invalida los bundles emitidos.
- `.env` no se versiona. No imprimir JWT ni `session_key` en logs.
- El portal guarda el JWT en `sessionStorage`; la Admin API Key no se persiste al
  recargar.
