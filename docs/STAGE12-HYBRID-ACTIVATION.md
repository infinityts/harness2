# Etapa 12 — Activación Online Híbrida y Lease Firmado

## Migración

`app/alembic/versions/0014_license_activation.py`
(`revision=0014`, `down_revision=0013`).

Añade `license_activation_state` singleton:

- `status`: `NOT_CONFIGURED`, `ACTIVE`, `OFFLINE_GRACE`, `EXPIRED`,
  `REVOKED`, `ERROR`, `TEST_BYPASS`.
- `server_url`, `license_id`, `installation_id`.
- `lease_id`, `lease_expires_at`, `lease_grace_until`.
- `last_success_at`, `last_attempt_at`, `last_error`.
- `lease_payload`, `lease_signature`.

## Arquitectura

```text
Harness2 (on-prem)                    Vendor control plane
  LicenseManager                        Activation Server
   ├─ licencia firmada Ed25519           ├─ private key Ed25519
   ├─ installation_id                    ├─ registry license/installation
   └─ ActivationManager ─── activate ───►├─ lease TTL 7 días
          ▲                              └─ revoke / restore
          │ lease firmado
          └─ verifica firma + binding + límites
```

El **Servidor de Activación debe vivir fuera del control del cliente** en
producción. En el laboratorio se ejecuta como contenedor `harness-license-server`
en la misma red; eso sirve para demostrar el flujo, no para considerarlo
anti-tamper absoluto.

## Lease

El servidor devuelve:

```json
{
  "lease": {
    "payload": {
      "lease_id": "uuid",
      "license_id": "prod-enterprise-10-2026",
      "installation_id": "uuid",
      "plan_code": "ENTERPRISE_10",
      "limits": { "...": 0 },
      "issued_at": "...",
      "expires_at": "...",
      "nonce": "...",
      "seq": 1
    },
    "signature": "base64-ed25519"
  },
  "server_time": "..."
}
```

El cliente verifica:

- firma Ed25519 con la clave pública del vendor;
- `installation_id` y `license_id`;
- que el lease no esté expirado;
- que `plan_code` esté permitido por la licencia firmada;
- que cada límite sea `<=` el techo firmado.

Luego guarda el lease y su firma en la base. En cada arranque/revalidación
verifica también el lease almacenado; si la base fue manipulada, pasa a
`ERROR` y no opera.

## TTL y gracia offline

- `LEASE_TTL_SECONDS=604800` (7 días) por defecto.
- `HARNESS_ACTIVATION_GRACE_DAYS=7`: después de expirar el lease, el cliente
  puede operar offline hasta `lease_expires_at + 7 días`.
- Renovación periódica: `HARNESS_ACTIVATION_RENEW_SECONDS=300`.
- Si el servidor responde `403 LICENSE_REVOKED`, el cliente pasa a `REVOKED`
  inmediatamente, sin gracia.
- Si el servidor no responde o está en mantenimiento, usa la gracia.
- Si la gracia termina, `EXPIRED` y se bloquean nuevas operaciones.

## Endpoints

Cliente Harness2:

| Método | Ruta | Permiso |
| --- | --- | --- |
| `GET` | `/api/v1/admin/license` | Admin |
| `POST` | `/api/v1/admin/license/activate` | Admin |
| `POST` | `/api/v1/admin/license/reload` | Admin |
| `GET` | `/api/v1/admin/license/audit` | Admin |

Servidor vendor:

| Método | Ruta | Auth |
| --- | --- | --- |
| `GET` | `/health` | Pública |
| `POST` | `/v1/activate` | `X-Harness-Activation-Token` |
| `POST` | `/v1/revoke` | `X-Activation-Admin` |
| `POST` | `/v1/restore` | `X-Activation-Admin` |
| `POST` | `/v1/maintenance` | `X-Activation-Admin` |

## Variables de entorno

Cliente:

```text
HARNESS_INSTALLATION_ID=<uuid>
HARNESS_ACTIVATION_URL=http://harness-license-server:8450
HARNESS_ACTIVATION_REQUIRED=true
HARNESS_ACTIVATION_TOKEN=<shared secret>
HARNESS_ACTIVATION_GRACE_DAYS=7
HARNESS_ACTIVATION_RENEW_SECONDS=300
```

Servidor:

```text
LICENSE_SERVER_PRIVATE_KEY=/license/private/private_key.pem
LICENSE_SERVER_DB=/data/activation.db
VENDOR_LICENSE_ID=prod-enterprise-10-2026
VENDOR_INSTALLATION_ID=<uuid>
VENDOR_PLAN_CODE=ENTERPRISE_10
LEASE_TTL_SECONDS=604800
ACTIVATION_TOKEN=<shared secret>
ACTIVATION_ADMIN_TOKEN=<admin secret>
```

## UI

Portal → **Licencia y Pool** muestra:

- estado de la licencia firmada;
- estado de activación (`ACTIVE`, `OFFLINE_GRACE`, `REVOKED`, `EXPIRED`);
- servidor, lease, gracia offline y última activación;
- botón **Activar / Renovar**;
- auditoría de licencia.

## Pruebas

`tests/e2e_stage12_activation.py` se ejecuta en un contenedor temporal con
acceso a `harness-net` y al `.env`:

```bash
podman run --rm --network harness-net --env-file .env \
  -e HARNESS_BASE_URL=http://harness-app:8443 \
  -v "$PWD/tests:/app/tests:ro,z" \
  --entrypoint python localhost/harness-app:latest \
  /app/tests/e2e_stage12_activation.py
```

Cubre:

- activación inicial y lease válido;
- binding de instalación en el servidor;
- revocación → `REVOKED` y bloqueo `503`;
- restauración → `ACTIVE`;
- mantenimiento + lease vencido dentro de gracia → `OFFLINE_GRACE`;
- gracia agotada → `EXPIRED` y bloqueo;
- lease almacenado con firma manipulada → `ERROR`;
- auditoría con eventos de activación;
- UI de activación.

## Diagrama

- `docs/diagrams/stage12-hybrid-activation-icons.html`
- Spec: `docs/diagrams/stage12-hybrid-activation.sequence.json`

## Límite real

El servidor de activación de referencia corre en el mismo host en el
laboratorio. Para que el enforcement sea fuerte en producción, debe estar
fuera de la infraestructura del cliente, con la clave privada solo en el
vendor. Aun así, un cliente con root puede parchear la app; la combinación de
firma, binding, lease corto, gracia offline y auditoría eleva el costo y deja
evidencia.
