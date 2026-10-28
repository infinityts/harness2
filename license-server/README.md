# Harness2 License Server

Servicio vendor-side de activación para Harness2. Emite **leases Ed25519** de
corta duración, permite revocación/restauración y mantiene un registro de
licencias/instalaciones.

> **Producción:** este servicio debe vivir fuera de la infraestructura del
> cliente. La clave privada Ed25519 nunca debe montarse en Harness2 ni
> distribuirse con el cliente.

## API

| Método | Ruta | Auth |
| --- | --- | --- |
| `GET` | `/health` | Pública |
| `POST` | `/v1/activate` | `X-Harness-Activation-Token` |
| `POST` | `/v1/revoke` | `X-Activation-Admin` |
| `POST` | `/v1/restore` | `X-Activation-Admin` |
| `POST` | `/v1/maintenance` | `X-Activation-Admin` |

`/v1/activate` recibe:

```json
{ "license_id": "prod-enterprise-10-2026", "installation_id": "<uuid>" }
```

y devuelve un lease firmado:

```json
{
  "lease": {
    "payload": {
      "lease_id": "uuid",
      "license_id": "...",
      "installation_id": "...",
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

## Configuración

Copiar `.env.example` a `.env` y completar:

```text
VENDOR_INSTALLATION_ID=<installation_id de Harness2>
ACTIVATION_TOKEN=<secreto compartido con Harness2>
ACTIVATION_ADMIN_TOKEN=<secreto solo vendor>
LICENSE_PRIVATE_DIR=../license/private
```

La clave privada se genera con:

```bash
python /app/tools/license_tool.py keygen \
  --private license/private/private_key.pem \
  --public license/public/public_key.pem
```

El servidor necesita el directorio de la clave privada, no la pública.

## Ejecución local

La red `harness-net` debe existir (la crea el compose principal de Harness2).

```bash
podman-compose -f license-server/docker-compose.yml up -d --build
podman logs -f harness-license-server
```

El contenedor se llama `harness-license-server` y queda en
`172.28.0.40:8450`, accesible desde `harness-app` como
`http://harness-license-server:8450`.

## Publicación futura

Checklist antes de publicar la imagen:

- No incluir `license-server/.env`.
- No incluir `license-server/data/activation.db`.
- No incluir claves privadas.
- Fijar la imagen base por digest.
- Añadir TLS/autenticación fuerte si se expone a Internet.
- Servir detrás de un proxy con rate limiting.
- Rotar `ACTIVATION_TOKEN` y `ACTIVATION_ADMIN_TOKEN`.
- Documentar el contrato de lease y la política de revocación.

## Seguridad

- La base SQLite es solo el registro de activaciones; no guarda claves.
- La clave privada se monta read-only.
- La revocación es inmediata en el cliente.
- Si el servidor no responde, el cliente usa la gracia offline configurada
  (`HARNESS_ACTIVATION_GRACE_DAYS`).
- El servidor de referencia en el mismo host es para laboratorio; no sustituye
  un control plane vendor-side real.
