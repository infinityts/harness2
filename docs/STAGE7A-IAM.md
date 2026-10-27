# Etapa 7A — Gobierno IAM y Proyectos (Migración 0005)

## Alcance

Esta entrega implementa la **Parte A** de la Etapa 7: el modelo de datos de
gobierno (usuarios, proyectos, membresías), el aislamiento sesión→proyecto y los
seeds iniciales. Las Partes B (auth de portal, endpoints admin, sesiones 1:1 y
aislamiento runtime) y C (portal responsive `/portal` + `/war-room`) se documentan
en [`STAGE7-IAM-PORTAL.md`](./STAGE7-IAM-PORTAL.md).

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

## Verificación (E2E 7A/B/C)

`tests/e2e_stage7.py` comprueba la migración 0005/0006, las tablas IAM, login de
admin, alta automática de `proj-alpha`/`proj-beta`, membresías selectivas,
sesiones 1:1, config-bundle, aislamiento cross-project, dispatch por proyecto y
los breakpoints del portal responsive.

Regresiones: Stage 1, 3, 4, 5 y 6 siguen pasando con el esquema nuevo.

## Estado final

```text
users 2 admins + cuentas creadas por el Admin | projects 1
memberships 2 + membresías aprobadas
tasks 0 | plans 0 | files 7
alembic 0010 (head)
```

La revisión `0007` agrega SMTP y auditoría de correos; ver
[`STAGE7-SMTP.md`](./STAGE7-SMTP.md). La `0008` agrega el seed legacy
`admin@local`; ver [`STAGE7-PASSWORDS.md`](./STAGE7-PASSWORDS.md). La `0009`
agrega el catálogo estándar de skills y la `0010` la skill oficial
`frontend-design`; ver [`STAGE8-SKILLS.md`](./STAGE8-SKILLS.md).

Commit Parte A: `ca68e8d` (`feat: stage 7a IAM/projects governance migration and seeds`).

## Partes B y C implementadas

- Login por correo, activación por invitación y sesiones de portal.
- Endpoints admin de usuarios/proyectos/membresías.
- Registro de sesiones con `user_id/project_id` y validación de membresía.
- Filtros estrictos por proyecto en planes/tareas/context packs.
- Portal responsive Admin/Ingeniero en `/portal` y `/war-room`.

Ver [`STAGE7-IAM-PORTAL.md`](./STAGE7-IAM-PORTAL.md).
