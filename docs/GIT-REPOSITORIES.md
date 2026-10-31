# Repositorios Git en Harness2

## Repos públicos

`repo_url` acepta HTTPS:

```text
https://github.com/org/repo.git
https://gitlab.com/org/repo.git
```

El contenedor clona el repo, crea el worktree base y lo indexa.

## Repos privados de GitHub

El contenedor necesita credenciales. Harness2 soporta un token de GitHub por
variable de entorno:

```bash
# /opt/harness-control-plane/.env
HARNESS_GIT_TOKEN=github_pat_xxxxxxxxxxxxxxxxx
```

Recomendaciones:

- usar un **fine-grained token**;
- permiso mínimo: **Contents: Read-only** sobre el repositorio;
- no usar un token con permisos de administración;
- no commitear `.env`;
- rotar el token si se expone.

Después de agregarlo:

```bash
cd /opt/harness-control-plane
podman-compose up -d harness-app
```

El token se inyecta como header HTTP de Git y **no** se escribe en la URL ni en
los argumentos del proceso.

También se acepta `GITHUB_TOKEN` como alias.

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

El directorio existía sin `.git` y el código anterior intentaba hacer `git add`
sin validar. La versión actual:

- detecta el directorio huérfano;
- inicializa Git y hace fetch/checkout del repo remoto;
- o devuelve un error claro si hay archivos que no puede mover.

### `could not read Username for 'https://github.com'`

El repo es privado y el contenedor no tiene token. Agregar
`HARNESS_GIT_TOKEN` en `.env` y reiniciar `harness-app`.

### `Repository not found`

Verificar:

- URL exacta del repo;
- rama `default_branch`;
- que el token tenga acceso al repo;
- que el token no haya expirado.

## Verificación

```bash
podman exec harness-app git ls-remote https://github.com/org/repo.git HEAD
```

Si ese comando falla, el problema es credenciales/URL, no Harness2.

## Límite

Harness2 solo acepta URLs `http(s)`. Para SSH habría que agregar una clave al
contenedor y habilitar explícitamente ese esquema; por ahora se recomienda el
token HTTPS de solo lectura.
