# Etapa 7 — Identidad, Contraseñas y Notificaciones

## Migración

`app/alembic/versions/0008_legacy_super_admin_seed.py`
(`revision=0008`, `down_revision=0007`).

No se modifica `0005` porque ya está aplicada: una revisión aplicada no se vuelve
a ejecutar. `0008` inserta de forma idempotente el super administrador legacy y
su membresía en `harness-self`.

| Campo | Valor |
| --- | --- |
| email | `admin@local` |
| full_name | `Super Administrator` |
| role | `ADMIN` |
| status | `ACTIVE` |
| password_hash | PBKDF2-HMAC-SHA256 de `admin` (o `HARNESS_LEGACY_ADMIN_PASSWORD`) |

`ON CONFLICT (email) DO NOTHING` evita sobrescribir una cuenta existente o una
contraseña ya cambiada. El `downgrade` no elimina la cuenta para no destruir un
administrador activo.

> `admin/admin` es una credencial de primer acceso. Debe cambiarse desde
> **Mi clave** inmediatamente después del primer login.

## Política de contraseñas

Se aplica a cambios propios y a resets administrativos:

- mínimo 8 caracteres, máximo 128;
- al menos una mayúscula;
- al menos una minúscula;
- al menos un número;
- al menos un símbolo.

La contraseña legacy `admin` solo sirve para el primer login; no puede
reutilizarse al cambiarla porque no cumple la política.

## Endpoints

### `PUT /api/v1/auth/password`

Requiere JWT de portal.

```json
{
  "current_password": "PwdDev2026!",
  "new_password": "PwdNew2026!"
}
```

Verifica el hash actual, valida la política, impide reutilizar la misma
contraseña y actualiza `password_hash`. Envía un correo de confirmación sin
incluir la nueva contraseña y registra `password_changed` en
`email_dispatch_logs`.

### `PUT /api/v1/admin/users/{user_id}/reset-password`

Solo Admin.

```json
{
  "new_password": "PwdReset2026!",
  "generate_random": false
}
```

- Con `new_password` valida la política y actualiza el hash.
- Con `generate_random=true` o sin `new_password` genera 16 caracteres seguros.
- Envía al usuario la contraseña temporal y la recomendación de cambiarla.
- Registra `admin_password_reset`.
- **No devuelve la contraseña en la respuesta.** Solo `generated` y
  `email_sent`.

## Zero-Knowledge

- `password_hash` es la única representación persistida.
- `GET /api/v1/auth/me`, login, proyectos y membresías no devuelven hashes ni
  contraseñas.
- `GET /api/v1/admin/smtp-config` devuelve `password_set`, nunca la contraseña
  SMTP.
- `email_dispatch_logs` guarda destinatario, asunto, plantilla, estado y error;
  no guarda el cuerpo del correo ni contraseñas.
- Los correos de reset contienen la clave temporal por requisito operativo; el
  correo de confirmación de autogestión no la contiene.

## Portal

- Botón **Mi clave** en la barra superior para cualquier usuario con JWT de
  portal: contraseña actual, nueva y confirmación.
- En **Usuarios y Accesos**, cada usuario tiene **Reset password** y
  **Asignar proyecto**: contraseña manual o generación aleatoria, con resultado
  de envío SMTP.
- La auditoría de correos muestra `password_changed` y
  `admin_password_reset`.

## Pruebas

```bash
podman cp tests/e2e_stage7_passwords.py harness-app:/tmp/e2e_stage7_passwords.py
podman exec harness-app python /tmp/e2e_stage7_passwords.py
```

`tests/e2e_stage7_passwords.py`:

1. Verifica migración `0008` y login `admin@local` / `admin`.
2. Configura un mock SMTP local y restaura la configuración original.
3. Crea proyecto e ingeniero de prueba.
4. Rechaza contraseña débil y contraseña actual incorrecta.
5. Cambia la contraseña propia, verifica login viejo/nuevo y correo
   `password_changed`.
6. El Admin resetea la contraseña, verifica correo `admin_password_reset` y
   login con la nueva clave.
7. Genera una contraseña aleatoria sin exponerla en la API.
8. Comprueba que la BD solo contiene `pbkdf2_sha256$...`.
9. Limpia datos de prueba y restaura SMTP.

## Seguridad

- Cambiar `admin/admin` en el primer acceso.
- Configurar SMTP con TLS y una contraseña de aplicación antes de usar resets.
- Si SMTP falla, la contraseña igual cambia pero `email_sent` es `false`; el
  administrador debe reintentar el reset o corregir SMTP.
- El correo de reset contiene una credencial temporal; debe viajar por un canal
  confiable.
