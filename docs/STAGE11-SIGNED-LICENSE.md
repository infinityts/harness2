# Etapa 11 — Licencia Firmada y Auditoría Anti-Tamper

## Migración

`app/alembic/versions/0013_signed_license_audit.py`
(`revision=0013`, `down_revision=0012`).

Crea:

- `system_installation` singleton con `installation_id` UUID.
- `system_license_state` singleton con el estado de la licencia verificada.
- `license_audit_events` con cadena de hashes (`prev_hash` / `event_hash`).

No modifica `system_license_policy`: esa tabla sigue siendo la **caché de la
política activa**, no la fuente de verdad.

## Fuente de verdad

La licencia es un archivo JSON firmado con Ed25519:

```json
{
  "payload": {
    "license_id": "prod-enterprise-10-2026",
    "customer": "Harness2 On-Premise",
    "installation_id": "<uuid de esta instalación>",
    "plan_code": "ENTERPRISE_10",
    "allowed_plans": ["ENTERPRISE_10"],
    "limits": {
      "max_active_projects": 10,
      "max_hermes_sessions": 10,
      "max_engineers_per_project": 10,
      "max_total_engineer_sessions": 30,
      "max_registered_users": 30,
      "max_parallel_rag_jobs": 3
    },
    "features": ["rag", "hermes_pool", "license_audit"],
    "issued_at": "2026-10-28T00:00:00+00:00",
    "expires_at": "2027-10-28T00:00:00+00:00",
    "grace_days": 30,
    "test": false
  },
  "signature": "base64-ed25519"
}
```

- La clave privada **solo** la tiene el vendor.
- La app lleva la clave pública en `public_key.pem`.
- La firma se calcula sobre JSON canónico (`sort_keys`, sin espacios).
- Una licencia válida para otra instalación no sirve: `installation_id` debe
  coincidir con `system_installation.installation_id`.

## Estados

| Estado | Operativo | Significado |
|---|---|---|
| `VALID` | Sí | Firma, instalación y fecha correctas. |
| `GRACE` | Sí | Expiró, pero sigue dentro de `grace_days`. |
| `EXPIRED` | No | Expiró y terminó la gracia. |
| `INVALID` | No | Firma, formato o instalación inválidos. |
| `MISSING` | No | No hay archivo de licencia. |
| `DEVELOPMENT` | Sí | Solo si `HARNESS_LICENSE_REQUIRED=false`; no usar en producción. |

En estado no operativo:

- Se bloquean altas de proyecto, activación de slot, sesiones nuevas, usuarios,
  bootstrap RAG, ingesta RAG y jobs Hermes.
- La lectura, el portal y la exportación siguen disponibles.
- `PUT /admin/license` responde `503 LICENSE_DEGRADED`.

## Entitlements

`PUT /api/v1/admin/license` ya no puede elevar el plan ni las cuotas:

1. `plan_code` debe estar en `allowed_plans` de la licencia.
2. Cada cuota solicitada debe ser `<= limits` firmado.
3. Un downgrade sigue sujeto a las reglas de la Etapa 10.

Para un cliente normal, la licencia autoriza solo su plan. Para pruebas se usa
una licencia de evaluación con `allowed_plans` múltiples, pero **no se instala en
producción**.

## Auditoría hash-chain

Cada evento guarda:

```text
event_type, actor, payload, prev_hash, event_hash, created_at
```

`event_hash = SHA-256(prev_hash + canonical_json(event_body))`.

- `GET /api/v1/admin/license/audit` devuelve eventos y `chain.valid`.
- Si se altera o borra un evento, la cadena se rompe y se detecta.
- La app no puede firmar checkpoints (no tiene clave privada); el vendor puede
  firmar un export de auditoría fuera de la instalación.

## Endpoints

| Método | Ruta | Permiso |
| --- | --- | --- |
| `GET` | `/api/v1/admin/license` | Admin |
| `PUT` | `/api/v1/admin/license` | Admin, dentro del entitlement |
| `POST` | `/api/v1/admin/license/reload` | Admin |
| `GET` | `/api/v1/admin/license/audit` | Admin |

`/health` incluye `license_status`.

## Variables de entorno

```text
HARNESS_LICENSE_FILE=/var/lib/harness/license/license.json
HARNESS_LICENSE_PUBLIC_KEY_FILE=/var/lib/harness/license/public_key.pem
HARNESS_LICENSE_REQUIRED=true
HARNESS_LICENSE_RELOAD_SECONDS=300
```

El directorio `license/public` se monta en `/var/lib/harness/license`.
La clave privada vive en `license/private` y **no se monta** en la app.

## Generación de licencias

```bash
# Crear claves (una vez)
podman run --rm -v "$PWD/license:/license:z" localhost/harness-app:latest \
  python /app/tools/license_tool.py keygen \
  --private /license/private/private_key.pem \
  --public /license/public/public_key.pem

# Generar producción + set de pruebas
podman run --rm -e PYTHONPATH=/app \
  -v "$PWD/license:/license:z" \
  -v "$PWD/app/tools/generate_deployment_licenses.py:/tmp/gen.py:ro" \
  localhost/harness-app:latest \
  python /tmp/gen.py --installation-id "$(podman exec harness-db psql -U harness_admin -d harness_core -tAc 'select installation_id from system_installation where id=1')"
```

El set de pruebas se guarda en `license/test/` (fuera del mount de la app).
Solo `license.production.json` se copia a `license/public/license.json`.

## Pruebas

La prueba de planes necesita la licencia multi-plan de evaluación. Se ejecuta en
un contenedor temporal con dos mounts: el directorio público (el mismo que ve la
app) y el directorio de test (solo lectura). El directorio público debe ser
escribible por el contenedor de prueba durante el test; en producción debe
quedar en `755` y no montar `license/test`.

```bash
podman run --rm --network harness-net --env-file .env \
  -e HARNESS_BASE_URL=http://harness-app:8443 \
  -v "$PWD/license/public:/var/lib/harness/license:z" \
  -v "$PWD/license/test:/var/lib/harness/license-test:ro,z" \
  -v "$PWD/tests:/app/tests:ro,z" \
  --entrypoint python localhost/harness-app:latest \
  /app/tests/e2e_stage11_signed_license.py
```

Cubre:

- licencia de producción válida y binding a `installation_id`;
- Admin no puede elegir planes ni cuotas fuera del entitlement;
- licencia multi-plan de pruebas (PERSONAL_1, TEAM_3, MAX_20);
- firma manipulada → `INVALID` + degradado;
- instalación distinta → `INVALID`;
- expirada → `EXPIRED`; en gracia → `GRACE`;
- restauración de producción y cadena de auditoría;
- detección de tamper directo en `license_audit_events`;
- UI de licencia firmada.

La prueba Stage 10 usa el mismo set de prueba; se ejecuta dentro de
`harness-app` con `license/test` copiado temporalmente a
`/var/lib/harness/license-test`, y se elimina al terminar.

## Diagrama

Diagrama interactivo Archify:

- `docs/diagrams/stage11-signed-license-icons.html`
- Spec: `docs/diagrams/stage11-signed-license.architecture.json`

## Límite real del on-premise

Esto **no es DRM absoluto**. Un cliente con root puede:

- parchear la verificación en el código;
- cambiar la base;
- borrar logs;
- bloquear la renovación.

Lo que se consigue:

1. No puede fabricar una licencia válida sin la clave privada.
2. No puede reutilizar la licencia en otra instalación.
3. La manipulación de la política o de la auditoría es detectable.
4. La expiración y la gracia limitan el uso indefinido.
5. El contrato y la auditoría cubren el resto.

Para enforcement más fuerte: activación online, TPM/dongle o plano de control
híbrido.
