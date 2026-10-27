# Etapa 7A — Gobierno IAM y Proyectos (Migración 0005)

## Alcance

Esta entrega implementa **solo la Parte A** de la Etapa 7: el modelo de datos de
gobierno (usuarios, proyectos, membresías), el aislamiento sesión→proyecto y los
seeds iniciales. La Parte B (portal web, login por correo, endpoints de
invitación/aprobación y aislamiento estricto en runtime) no venía incluida en la
especificación recibida.

## Migración

`app/alembic/versions/0005_iam_and_projects_governance.py` (`revision=0005`,
`down_revision=0004`).

### Tablas

| Tabla | Propósito |
|---|---|
| `harness_users` | Ingenieros/administradores: email único, full_name, password_hash PBKDF2, role, status, invite_token, default_skills JSONB. |
| `harness_projects` | Proyectos del portal: project_slug = repo_name, display_name, description, repo_url, default_branch, created_by_user_id. |
| `project_memberships` | Quién entra a qué proyecto, skills aprobados, estado y aprobador. Unique `(project_id, user_id)`. |

### Aislamiento

| Tabla | Columna | Nullable |
|---|---|---|
| `agent_sessions` | `user_id` FK `harness_users` ON DELETE CASCADE | Sí (retrocompatibilidad; obligatorio en el flujo del portal) |
| `agent_sessions` | `project_id` FK `harness_projects` ON DELETE CASCADE | Sí (1 sesión = 1 proyecto en el flujo nuevo) |
| `feature_consensus_plans` | `project_id` FK `harness_projects` ON DELETE CASCADE | Sí |

Índices: `idx_agent_sessions_user_id`, `idx_agent_sessions_project_id`,
`idx_feature_plans_project_id`, `idx_project_memberships_user`,
`idx_project_memberships_project`.

### Constraints verificados

```text
harness_users_email_key
harness_projects_project_slug_key
uq_project_user_membership
fk_agent_sessions_user_id
fk_agent_sessions_project_id
fk_feature_plans_project_id
```

## Seguridad de contraseñas

`app/src/security.py` implementa PBKDF2-HMAC-SHA256 con salt aleatoria, sin
dependencias nativas:

```text
pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>
```

`verify_password()` compara en tiempo constante con `hmac.compare_digest`.

## Seeds

El propio migration inserta de forma idempotente:

1. **Administrador** con `HARNESS_INITIAL_ADMIN_EMAIL` y
   `HARNESS_INITIAL_ADMIN_PASSWORD` (defaults `admin@harness.local` /
   `AdminHarness2026!`).
2. **Proyecto** `harness-self` (`display_name=Harness Self`, `default_branch=main`).
3. **Membership APPROVED** del administrador en `harness-self`.

> Las credenciales viven en `.env` (ignorado por git). Se recomienda cambiarlas
> al conectar el portal de la Parte B.

## Variables de entorno

```text
HARNESS_INITIAL_ADMIN_EMAIL=admin@harness.local
HARNESS_INITIAL_ADMIN_PASSWORD=AdminHarness2026!
PBKDF2_ITERATIONS=260000            # opcional
```

## Verificación (E2E 7A)

`tests/e2e_stage7.py` comprueba:

- `alembic_version = 0005` y las tres tablas nuevas.
- Columnas de aislamiento y los seis constraints.
- Admin `ACTIVE/ADMIN`, hash PBKDF2 y `verify_password` correcto/incorrecto.
- Proyecto `harness-self` con `created_by_user_id` del admin.
- Membership `APPROVED`, `allowed_skills=[]`, aprobada por el admin.
- Health 200 y `harness-self` indexado.

Regresiones: Stage 1, Stage 5 y Stage 6 siguen pasando con el esquema nuevo.

## Estado final

```text
users 1 | projects 1 | memberships 1
sessions 0 | tasks 0 | plans 0
events 171 | files 7
alembic 0005 (head)
```

Commit: `ca68e8d` (`feat: stage 7a IAM/projects governance migration and seeds`).

## Pendiente para la Parte B

- Login por correo, activación por invitación y sesiones de portal.
- Endpoints admin de usuarios/proyectos/membresías.
- Registro de sesiones con `user_id/project_id` y validación de membresía.
- Filtros estrictos por proyecto en planes/tareas/context packs.
- Portal responsive Admin/Ingeniero.
