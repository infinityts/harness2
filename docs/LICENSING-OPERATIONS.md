# Licenciamiento Harness2 — Operación

Documento consolidado de las etapas 10, 11, 12 y 13.

## Componentes

| Componente | Contenedor | Responsabilidad |
| --- | --- | --- |
| Control plane | `harness-app` | Licencia firmada, entitlement, cuotas, activación, UI y auditoría. |
| Base de datos | `harness-db` | `system_license_policy`, `system_license_state`, `license_activation_state`, `license_audit_events`. |
| Worker Hermes | `harness-hermes-worker` | Pool RAG por proyecto; no consume cupo de ingenieros. |
| Servidor de licencias | `harness-license-server` | Proyecto Docker independiente; emite leases firmados y revoca activaciones. |
| Operador de licencia | cuenta `LICENSE_ADMIN` | Segundo control local con MFA para mutar licencia/activación; no administra proyectos ni usuarios. |

El servidor de licencias **no** vive en el compose principal. Se levanta desde
`license-server/docker-compose.yml` y comparte la red externa `harness-net`.

## Modelo de licencia

1. **Licencia firmada Ed25519** (`license.json`) con:
   - `license_id`, `customer`, `installation_id`;
   - `plan_code`, `allowed_plans`, `limits`;
   - `issued_at`, `expires_at`, `grace_days`;
   - `features`.
2. **Binding** al `installation_id` de `system_installation`.
3. **Entitlement**: el Admin solo puede elegir planes y cuotas dentro de
   `allowed_plans` y `limits`.
4. **Activación online**: el vendor emite un **lease** Ed25519 de 7 días.
5. **Gracia offline**: si el servidor no responde, el lease vencido sigue
   operativo hasta `lease_expires_at + HARNESS_ACTIVATION_GRACE_DAYS`.
6. **Revocación**: el servidor puede revocar; el cliente pasa a `REVOKED` y
   bloquea escrituras sin usar la gracia.
7. **Auditoría hash-chain** de todos los eventos de licencia/activación.
8. **Separación de funciones**: solo una cuenta `LICENSE_ADMIN` con MFA puede
   mutar la licencia; un `ADMIN` normal queda en modo lectura.

## Gobierno de la cuenta de licencia

La cuenta `license-operator@harness.local` es local a Harness2 y no sustituye la
firma ni el servidor vendor-side. Se provisiona con:

```bash
podman exec harness-app python tools/provision_license_operator.py \
  > /root/harness-license-operator.credentials.json
chmod 600 /root/harness-license-operator.credentials.json
```

La salida contiene password y secreto TOTP una sola vez. La cuenta:

- tiene `password_change_allowed = false` y no se puede resetear desde el portal;
- exige TOTP en cada login;
- bloquea 15 minutos tras 5 intentos fallidos;
- rechaza reutilización del mismo código TOTP;
- no puede crear proyectos, usuarios ni sesiones;
- queda auditada con actor e IP.

Rotación/break-glass:

```bash
podman exec harness-app python tools/provision_license_operator.py --rotate \
  > /root/harness-license-operator.credentials.json
chmod 600 /root/harness-license-operator.credentials.json
```

El script rechaza crear un segundo `LICENSE_ADMIN`. Detalle completo en
`docs/STAGE13-LICENSE-ADMIN.md`.

## Planes

| Plan | Proyectos | Hermes | Ingenieros/proyecto | Sesiones globales | Usuarios |
| --- | ---: | ---: | ---: | ---: | ---: |
| `PERSONAL_1` | 1 | 1 | 1 | 1 | 1 |
| `TEAM_3` | 3 | 3 | 3 | 6 | 5 |
| `BUSINESS_5` | 5 | 5 | 5 | 15 | 15 |
| `ENTERPRISE_10` | 10 | 10 | 10 | 30 | 30 |
| `MAX_20` | 10 | 10 | 20 | 60 | 100 |

`ENTERPRISE_10` es el default. Los Admin no consumen cupo de usuarios.

## Arranque

```bash
# 1. Control plane (crea harness-net)
cd /opt/harness-control-plane
podman-compose up -d

# 2. Servidor de licencias, en su propio proyecto Docker
cd /opt/harness-control-plane/license-server
cp .env.example .env
# completar VENDOR_INSTALLATION_ID, ACTIVATION_TOKEN y ACTIVATION_ADMIN_TOKEN
podman-compose up -d --build
```

Orden importante: si se hace `podman-compose down` en el compose principal se
elimina `harness-net`; hay que volver a levantar Harness2 antes que el servidor
de licencias.

## Variables clave

Harness2:

```text
HARNESS_LICENSE_FILE=/var/lib/harness/license/license.json
HARNESS_LICENSE_PUBLIC_KEY_FILE=/var/lib/harness/license/public_key.pem
HARNESS_LICENSE_REQUIRED=true
HARNESS_ACTIVATION_URL=http://harness-license-server:8450
HARNESS_ACTIVATION_REQUIRED=true
HARNESS_ACTIVATION_GRACE_DAYS=7
HARNESS_ACTIVATION_RENEW_SECONDS=300
HARNESS_ACTIVATION_TOKEN=<secreto compartido>
```

Servidor:

```text
VENDOR_INSTALLATION_ID=<installation_id>
VENDOR_LICENSE_ID=prod-enterprise-10-2026
VENDOR_PLAN_CODE=ENTERPRISE_10
LEASE_TTL_SECONDS=604800
ACTIVATION_TOKEN=<secreto compartido>
ACTIVATION_ADMIN_TOKEN=<secreto admin>
LICENSE_PRIVATE_DIR=/opt/harness-control-plane/license/private
```

## Operaciones

### Estado

```bash
curl -s http://127.0.0.1:8444/health
curl -s http://127.0.0.1:8444/api/v1/admin/license \
  -H "Authorization: Bearer <portal_admin_token>"
```

### Renovar activación

Solo `LICENSE_ADMIN` (con TOTP) puede renovar. Desde Portal → **Licencia y Pool**
→ **Activar / Renovar**, o:

```bash
curl -X POST http://127.0.0.1:8444/api/v1/admin/license/activate \
  -H "Authorization: Bearer <license_operator_token>"
```

Un `ADMIN` normal recibe `403 LICENSE_ADMIN_REQUIRED`.

### Cambiar plan o cuotas

```bash
curl -X PUT http://127.0.0.1:8444/api/v1/admin/license \
  -H "Authorization: Bearer <license_operator_token>" \
  -H "Content-Type: application/json" \
  -d '{"plan_code":"ENTERPRISE_10","max_active_projects":10}'
```

El operador no puede salir de `allowed_plans` ni superar los techos firmados.

### Revocar / restaurar

```bash
curl -X POST http://172.28.0.40:8450/v1/revoke \
  -H "X-Activation-Admin: $ACTIVATION_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"license_id":"prod-enterprise-10-2026","installation_id":"<uuid>"}'

curl -X POST http://172.28.0.40:8450/v1/restore \
  -H "X-Activation-Admin: $ACTIVATION_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"license_id":"prod-enterprise-10-2026","installation_id":"<uuid>"}'
```

### Mantenimiento

```bash
curl -X POST http://172.28.0.40:8450/v1/maintenance \
  -H "X-Activation-Admin: $ACTIVATION_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true}'
```

El cliente pasa a `OFFLINE_GRACE` si tiene lease dentro de la gracia; si no,
`EXPIRED`.

### Regenerar licencia

```bash
podman run --rm -v "$PWD/license:/license:z" localhost/harness-app:latest \
  python /app/tools/license_tool.py keygen \
  --private /license/private/private_key.pem \
  --public /license/public/public_key.pem
```

La licencia de producción está en `license/public/license.json`; la privada
nunca se monta en Harness2.

## Diagnóstico

| Síntoma | Causa probable | Acción |
| --- | --- | --- |
| `LICENSE_DEGRADED` | licencia inválida/expirada | revisar `signed_license.error_message` y `/admin/license/audit`. |
| `LICENSE_ACTIVATION_REQUIRED` | sin lease o revocado | revisar servidor, token y `license_activation_state`. |
| `LICENSE_ADMIN_REQUIRED` | un `ADMIN` normal intentó mutar licencia | iniciar sesión como `LICENSE_ADMIN` con TOTP. |
| `PASSWORD_SEALED` | intento de cambiar/resetear la password del operador | usar `provision_license_operator.py --rotate` en el host. |
| `ACCOUNT_LOCKED` | 5 intentos MFA/password fallidos | esperar 15 minutos o rotar credenciales vendor-side. |
| `MFA_REQUIRED` / `MFA_INVALID` / `MFA_REPLAY` | login de operador sin TOTP, TOTP incorrecto o reutilizado | usar el código actual y no reutilizarlo. |
| `OFFLINE_GRACE` | servidor caído/mantenimiento | restaurar servidor o renovar antes de que termine la gracia. |
| `REVOKED` | revocación vendor | restaurar en el servidor y activar. |
| `ERROR stored lease signature is invalid` | manipulación en base | renovar contra el servidor; investigar auditoría. |
| Worker Hermes sin RAM | gateway/kernels residuales | verificar `podman-init`, idle-exit 30s y limpieza de kernels. |

## Documentos relacionados

- `docs/STAGE10-LICENSING-HERMES-POOL.md`
- `docs/STAGE11-SIGNED-LICENSE.md`
- `docs/STAGE12-HYBRID-ACTIVATION.md`
- `docs/STAGE13-LICENSE-ADMIN.md`
- `license-server/README.md`
- `docs/diagrams/stage11-signed-license-icons.html`
- `docs/diagrams/stage12-hybrid-activation-icons.html`

## RAG

Estos documentos se ingestan en el RAG del proyecto `harness-self` con
`source_uri` `repo://docs/...` y `repo://license-server/README.md`, usando el
Secret Scrubber, chunking y embeddings del servidor. La ingesta es idempotente
por checksum. La Etapa 13 agrega `repo://docs/STAGE13-LICENSE-ADMIN.md`.

## Límite

En on-premise con root, el cliente puede parchear la app. La combinación de
firma, binding, lease corto, gracia, revocación, cuenta `LICENSE_ADMIN` sellada
con MFA y auditoría eleva el costo y deja evidencia, pero no es DRM absoluto. Para
enforcement fuerte, el servidor de licencias debe estar fuera de la
infraestructura del cliente.
