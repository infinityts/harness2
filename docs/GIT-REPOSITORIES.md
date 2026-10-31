# Repositorios Git en Harness2

Cada proyecto se asocia a un repositorio Git. El repositorio puede ser:

- público por HTTPS;
- privado por HTTPS con un token por proyecto;
- local, creado por Harness2 cuando no se indica `repo_url`.

## Repos públicos

`repo_url` acepta HTTPS:

```text
https://github.com/org/repo.git
https://gitlab.com/org/repo.git
```

Harness2 clona, crea el worktree base y lo indexa.

## Repos privados: token por proyecto

La opción recomendada es guardar un token **por proyecto**. No se comparte entre
proyectos y se guarda cifrado en el vault de Harness2.

### Desde el portal

Al crear el proyecto:

```text
project_slug: desktop-organizer
repo_url: https://github.com/infinityts/desktop-organizer.git
default_branch: main
Token de Git: github_pat_...
```

El campo **Token de Git** solo es necesario para repos privados.

Para un proyecto ya creado:

1. abre **Proyectos**;
2. pulsa **Credencial Git** en la tarjeta del proyecto;
3. pega el token;
4. guarda.

El portal solo muestra el hint de los últimos 4 caracteres. El token nunca se
devuelve al navegador.

### API

```http
PUT /api/v1/projects/{project_id}/git-credential

{
  "git_token": "github_pat_...",
  "git_username": "x-access-token"
}
```

Quitar:

```http
DELETE /api/v1/projects/{project_id}/git-credential
```

Crear proyecto con token:

```http
POST /api/v1/projects

{
  "project_slug": "desktop-organizer",
  "display_name": "Desktop Organizer",
  "repo_url": "https://github.com/infinityts/desktop-organizer.git",
  "default_branch": "main",
  "git_token": "github_pat_..."
}
```

### Token de GitHub

Crear un **fine-grained token**:

```text
Settings → Developer settings → Personal access tokens → Fine-grained tokens
Repository access: Only select repositories
Repository: infinityts/desktop-organizer
Permissions: Contents → Read-only
```

No usar tokens con permisos de administración.

## Fallback global

Para repositorios compartidos se mantiene el token global:

```bash
# /opt/harness-control-plane/.env
HARNESS_GIT_TOKEN=github_pat_xxxxxxxxxxxxxxxxx
```

El token del proyecto tiene prioridad sobre el global.

Después de cambiar `.env`:

```bash
cd /opt/harness-control-plane
podman rm -f harness-hermes-worker harness-app
podman-compose up -d harness-app hermes-worker
```

También se acepta `GITHUB_TOKEN` como alias.

## Seguridad

- El token se cifra con `seal_secret` y se guarda en
  `harness_projects.git_token_vault`.
- No se devuelve en `GET /projects`.
- Solo se muestra `git_token_hint` (últimos 4 caracteres).
- Se inyecta como header HTTP de Git; no se escribe en la URL ni en `argv`.
- Se usa el host del `repo_url` para aplicar el header.
- Rotar el token si se expone.
- No commitear `.env`.

## Directorio huérfano

Si un intento anterior creó:

```text
/var/lib/harness/repos/<slug>/
```

sin `.git`, Harness2 intenta recuperarlo automáticamente si solo contiene:

- `.harness.lock`
- `README.md`

Si hay otros archivos, devuelve un error claro pidiendo moverlos o eliminar el
directorio antes de reintentar.

## Errores frecuentes

### `git add: fatal: not a git repository`

El directorio existía sin `.git`. La versión actual lo detecta, inicializa Git
y hace fetch/checkout, o devuelve un error claro si hay archivos que no puede
mover.

### `could not read Username for 'https://github.com'`

El repo es privado y el proyecto no tiene token. Configura **Credencial Git** o
`HARNESS_GIT_TOKEN`.

### `Repository not found`

Verificar:

- URL exacta del repo;
- rama `default_branch`;
- que el token tenga acceso al repo;
- que el token no haya expirado.

## Verificación

Para comprobar el token de un proyecto, la forma más segura es reintentar la
creación o usar el endpoint de reindexado después de guardar la credencial.

También se puede probar el token global:

```bash
podman exec harness-app git ls-remote https://github.com/org/repo.git HEAD
```

Si ese comando falla, el problema es credenciales/URL, no Harness2.

## Límite

Harness2 solo acepta URLs `http(s)`. Para SSH habría que agregar una clave al
contenedor y habilitar ese esquema; por ahora se recomienda token HTTPS de solo
lectura.
