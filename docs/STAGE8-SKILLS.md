# Etapa 8 — Catálogo Estándar de Skills y Skill Studio

## Migración

`app/alembic/versions/0009_skills_catalog_standard.py`
(`revision=0009`, `down_revision=0008`).

No se usó `0006`: el head real era `0008`. Modificar una revisión aplicada no se
ejecuta en una base existente.

### `harness_skills`

Campos principales:

- `slug` único, `display_name`, `category`;
- `trigger_description` (catálogo ligero);
- `objective`, `workflow_steps` JSONB, `guardrails_forbidden` JSONB,
  `output_criteria`;
- `compiled_skill_md` precompilado y `compiled_tokens`;
- `created_by_user_id`, `owner_user_id`, `is_official`, `status`, `visibility`,
  `version`, `created_at`, `updated_at`.

Categorías: `backend`, `frontend`, `database`, `documents`, `writing`, `qa`,
`devops`, `security`, `general`.

Seeds oficiales (solo lectura para ingenieros):

| Slug | Categoría | Tokens |
| --- | --- | --- |
| `python` | backend | 263 |
| `fastapi` | backend | 241 |
| `ui-responsive` | frontend | 259 |
| `db-alembic` | database | 245 |
| `doc-analyzer` | documents | 251 |
| `tech-writer` | writing | 255 |
| `frontend-design` | frontend | 475 |

`frontend-design` está adaptada de
[`anthropics/skills/frontend-design`](https://www.skills.sh/anthropics/skills/frontend-design):
identidad visual propia, tipografía deliberada, crítica contra clichés de IA,
responsive, foco visible y reduced-motion.

### Permisos y asignaciones

- `user_skill_capabilities`: puede crear, clonar, editar sus skills y otorgar.
- `skill_permissions`: ACL por skill/usuario (`use`, `edit`, `clone`, `grant`).
- `project_skills`: skills habilitadas en un proyecto.
- `membership_skills`: skills concretas que puede usar un usuario en un
  proyecto; reemplaza el uso runtime de `allowed_skills` JSONB (que se mantiene
  por compatibilidad y se sincroniza).
- Backfill de `allowed_skills` históricos a las nuevas tablas.

## Revelación progresiva

La base de datos es la fuente de verdad. **No hay un `main` que cargue todos los
SKILL.md en el prompt**.

Capas:

1. **Catálogo**: slug, nombre, categoría y `trigger_description`.
2. **Tarea activa**: solo los manuales de `required_skills` se adjuntan en
   `TASK_ASSIGNED` y en `GET /api/v1/tasks`.
3. **Bajo demanda**: herramienta MCP
   `harness_get_skill_manual(skill_slug)`.

`SESSION_SYNC` sigue enviando solo nombres de skills. El manual compilado se
limita a **500 tokens estimados** (seeds entre 241 y 263).

Si se desea Git, se puede exportar de forma opcional:

```text
skills/
  index.json
  doc-analyzer/SKILL.md
  python/SKILL.md
```

Pero el runtime consulta PostgreSQL, no recorre el árbol de archivos.

## Compilador

`app/src/skill_compiler.py`:

- `compile_standard_skill_md(...)` genera frontmatter + Objetivo + Pasos +
  Guardrails + Criterio de salida.
- Valida 3–5 pasos, 1–8 guardrails, categoría y presupuesto de tokens.
- `draft_skill(...)` genera un borrador determinista por categoría.
- Si `SKILL_DRAFT_PROVIDER=ollama|openai`, intenta un borrador con IA y cae al
  determinista si falla. Sin proveedor, el wizard funciona igual.

## API

- `GET /api/v1/skills` — catálogo ligero, filtros `category`, `search`, `status`.
- `GET /api/v1/skills/{slug}` — detalle + `compiled_skill_md` si está autorizado.
- `GET /api/v1/skills/{slug}/manual` — manual bajo demanda para una sesión de
  agente; valida asignación por proyecto/usuario.
- `POST /api/v1/skills/preview` — compila sin guardar.
- `POST /api/v1/skills/draft-from-idea` — borrador desde una frase.
- `POST /api/v1/skills`, `PUT /api/v1/skills/{slug}` — CRUD con permisos.
- `POST /api/v1/skills/{slug}/clone` — copia privada editable.
- `GET/PUT/DELETE /api/v1/skills/{slug}/permissions` — ACL.
- `GET/PUT /api/v1/admin/users/{id}/skill-capabilities` — capabilities.
- `GET/PUT /api/v1/projects/{id}/skills` — skills del proyecto.
- `GET/PUT /api/v1/projects/{id}/members/{uid}/skills` — skills del usuario en
  el proyecto.

Reglas:

- Oficial: solo el Admin edita; ingenieros solo lectura y clonación si tienen
  capability/ACL.
- Engineer: usa solo skills asignadas; crea/clona si capability; edita las suyas
  si capability/ACL.
- Admin: bypass de permisos.

## Portal

- Nueva pestaña **Skill Studio** para admin e ingenieros.
- Buscador + filtro por área.
- Tarjetas con tokens, oficial/privada, solo lectura, editar/clonar/permisos.
- Wizard de 4 bloques: objetivo, pasos, prohibiciones, entrega; autocompletado
  desde idea y vista previa en vivo del `SKILL.md`.
- **Asignar proyecto** ahora tiene:
  - buscador de proyectos;
  - lista de radios (no un `<select>` gigante);
  - selector de skills del catálogo con búsqueda, categoría y badge de solo
    lectura.
- **Permisos skills** por usuario: crear, clonar, editar propias, otorgar.
- Buscador de proyectos en la pestaña Proyectos.

## Pruebas

```bash
podman cp tests/e2e_stage8_skills.py harness-app:/tmp/e2e_stage8_skills.py
podman exec harness-app python /tmp/e2e_stage8_skills.py
```

Cubre migración 0009, seeds, 403 sin capability, draft determinista, preview,
creación privada, edición, clonación, asignación a proyecto/usuario, sesión con
skills, `skill_manuals` en la tarea, manual MCP autorizado/denegado, UI y
cleanup.

## Límites y seguridad

- Manual compilado ≤ 500 tokens estimados.
- El catálogo no incluye manuales completos.
- Las skills oficiales no son editables por ingenieros.
- El agente solo recibe manuales de skills asignadas a su proyecto/usuario.
- No se almacenan secretos en las skills.
