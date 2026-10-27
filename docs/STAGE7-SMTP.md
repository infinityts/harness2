# Etapa 7 — Soporte SMTP y Notificaciones

## Migración

`app/alembic/versions/0007_smtp_config_and_email_logs.py` (`revision=0007`,
`down_revision=0006`).

Se creó una revisión nueva en lugar de modificar `0005`: `0005` ya está aplicada
en el servidor y Alembic no vuelve a ejecutar una revisión aplicada. La revisión
`0007` mantiene la cadena y se aplica con `alembic upgrade head`.

### `system_smtp_config`

| Columna | Tipo | Notas |
| --- | --- | --- |
| `id` | Integer PK | `CHECK (id = 1)`; fila única global. |
| `host` | String(255) | Default `smtp.gmail.com`. |
| `port` | Integer | Default `587`. |
| `username` | String(255) | Nullable. |
| `password` | String(255) | Nullable; se guarda cifrada con prefijo `vault:`. |
| `from_email` | String(255) | Default `harness-bot@controlplane.local`. |
| `use_tls` | Boolean | Default `true`. |
| `use_ssl` | Boolean | Default `false`. |
| `updated_at` | DateTime(tz) | `NOW()` en cada actualización. |

La migración inserta idempotentemente la fila `id=1`.

### `email_dispatch_logs`

| Columna | Tipo | Notas |
| --- | --- | --- |
| `id` | UUID PK | `uuid_generate_v4()`. |
| `recipient` | String(255) | Destinatario. |
| `subject` | String(255) | Asunto. |
| `template_name` | String(100) | `smtp_test`, `project_invite`, etc. |
| `status` | String(30) | `SENT` o `FAILED`. |
| `error_message` | Text | Detalle del fallo SMTP. |
| `sent_at` | DateTime(tz) | `NOW()`. |

Índices: `idx_email_dispatch_logs_sent_at`, `idx_email_dispatch_logs_status`.

## `app/src/mailer.py`

Usa únicamente `smtplib` y `email` de la biblioteca estándar.

- `get_smtp_config(conn)` lee la fila única y descifra `password`.
- Si no hay fila, usa `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
  `SMTP_FROM`, `SMTP_USE_TLS` y `SMTP_USE_SSL`.
- `send_transactional_email(engine, recipient, subject, html_body, template_name)`
  envía HTML (`MIMEMultipart("alternative")`) con timeout de 10s, soporta
  TLS/SSL y autenticación opcional.
- Nunca propaga errores SMTP a la acción de negocio: registra `SENT`/`FAILED` en
  `email_dispatch_logs` y devuelve `True/False`.
- `render_project_invite(...)` genera la plantilla HTML de invitación con enlace
  al portal, proyectos aprobados, skills y credenciales iniciales si existen.

## Endpoints (solo Admin)

- `GET /api/v1/admin/smtp-config`: devuelve host, puerto, usuario, `from_email`,
  TLS/SSL y `password_set`. Nunca devuelve la contraseña.
- `PUT /api/v1/admin/smtp-config`: guarda la configuración en caliente. Si
  `password` se omite (`null`) conserva la actual; si es `""` la borra; si tiene
  valor la cifra. Acepta `test_recipient` para enviar un correo de prueba tras
  guardar.
- `POST /api/v1/admin/smtp-config/test`: envía un correo de prueba con la
  configuración actual. Por defecto usa el email del admin autenticado.
- `GET /api/v1/admin/email-logs?limit=50`: auditoría de los últimos envíos.

`use_tls` y `use_ssl` son mutuamente excluyentes.

## Notificación automática

`POST /api/v1/projects/{id}/members` agenda un `project_invite` en
`BackgroundTasks` después de confirmar la membresía. El correo incluye:

- enlace `HARNESS_PUBLIC_URL/portal`;
- proyectos aprobados y skills;
- contraseña inicial si el servidor la generó;
- invite token si se usó ese flujo.

Un fallo SMTP no revierte la membresía; queda auditado como `FAILED` con el
error correspondiente.

## Portal

La pestaña admin **SMTP y Correos** permite:

- editar host, puerto, usuario, contraseña, remitente, TLS/SSL;
- guardar, guardar y enviar test, o enviar solo test;
- ver la auditoría de correos con estado y error.

## Pruebas

```bash
podman cp tests/e2e_stage7_smtp.py harness-app:/tmp/e2e_stage7_smtp.py
podman exec harness-app python /tmp/e2e_stage7_smtp.py
```

`tests/e2e_stage7_smtp.py`:

1. Verifica migración `0007` y tablas.
2. Levanta un servidor SMTP mock local (stdlib) en `127.0.0.1`.
3. Guarda configuración, comprueba cifrado `vault:` y envía un test.
4. Verifica `SENT` en `email_dispatch_logs`.
5. Fuerza un fallo contra un puerto cerrado y verifica `FAILED`.
6. Crea un proyecto e invita a un ingeniero; confirma el log `project_invite`.
7. Decodifica el MIME y valida que el correo contiene proyecto y enlace al portal.
8. Restaura la configuración SMTP original y limpia los datos de prueba.

## Seguridad

- La contraseña SMTP no se expone en `GET` ni en logs.
- Se cifra con `HARNESS_VAULT_SECRET` (o `HARNESS_JWT_SECRET` como fallback).
- El correo de invitación contiene credenciales; debe enviarse solo a correos
  aprobados por el Admin y por un canal confiable.
- Rotar la contraseña de aplicación SMTP si se sospecha exposición.
