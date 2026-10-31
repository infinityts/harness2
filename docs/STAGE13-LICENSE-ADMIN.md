# Etapa 13 — LICENSE_ADMIN sellado con MFA

## Objetivo

Separar la administración de la licencia del resto de la administración de
Harness2. Un `ADMIN` normal conserva visibilidad y auditoría, pero no puede
cambiar el plan, las cuotas, la activación ni el lease.

La cuenta de licencia es **local a Harness2**; no sustituye la firma Ed25519 ni
el servidor vendor-side de activación. Es un control de gobernanza para evitar
que un administrador normal, o una sesión comprometida, modifique la licencia
por accidente o por abuso interno.

## Modelo

| Rol | Leer licencia | Mutar licencia | Administrar usuarios/proyectos |
| --- | :---: | :---: | :---: |
| `ADMIN` | sí | no | sí |
| `LICENSE_ADMIN` | sí | sí | no |
| `ENGINEER` | no | no | no |

`LICENSE_ADMIN`:

- no puede crear proyectos, usuarios ni sesiones;
- no puede ser miembro de un proyecto;
- no aparece en la lista de usuarios del Admin normal;
- no puede cambiar su propia contraseña ni ser reseteado desde el portal;
- solo puede operar dentro de `allowed_plans` y de los techos firmados.

## Cuenta

- Email por defecto: `license-operator@harness.local`.
- Rol: `LICENSE_ADMIN`.
- Estado: `ACTIVE`.
- `password_change_allowed = false`.
- `mfa_enabled = false` hasta completar el enrollment del primer login.
- El secreto TOTP se genera en el primer login, se cifra en el vault
  (`mfa_totp_secret_vault`) y se muestra **una sola vez** para configurar la app.
- Password aleatoria de 40 caracteres, PBKDF2-SHA256 con salt.
- La contraseña se muestra **una sola vez** al provisionar.

## MFA

- TOTP RFC 6238, HMAC-SHA1, 30 s, 6 dígitos, ventana ±1.
- **Primer login**: el portal pide configurar MFA, muestra la clave/URI una sola
  vez y exige confirmar un código antes de habilitar la cuenta.
- **Logins posteriores**: MFA obligatorio en cada login de `LICENSE_ADMIN`.
- Anti-replay: se guarda `mfa_last_used_step`; un código usado no se acepta de
  nuevo aunque siga dentro de la ventana.
- Bloqueo: 5 intentos fallidos de password/TOTP bloquean la cuenta 15 minutos.
- Cada login, enrollment, fallo, bloqueo y reset bloqueado se registra en la
  auditoría de licencia con actor e IP.

## Endpoints

| Método | Ruta | Guard |
| --- | --- | --- |
| `GET` | `/api/v1/admin/license` | `ADMIN` o `LICENSE_ADMIN` |
| `GET` | `/api/v1/admin/license/audit` | `ADMIN` o `LICENSE_ADMIN` |
| `PUT` | `/api/v1/admin/license` | solo `LICENSE_ADMIN` |
| `POST` | `/api/v1/admin/license/reload` | solo `LICENSE_ADMIN` |
| `POST` | `/api/v1/admin/license/activate` | solo `LICENSE_ADMIN` |

La `X-Harness-Admin-Key` sigue sirviendo para automatización general, pero **no**
puede mutar la licencia. Es deliberado: la clave de automatización no debe
saltarse la separación de funciones.

## Migración

`0015_license_admin_mfa.py` agrega a `harness_users`:

- `mfa_enabled`
- `mfa_totp_secret_vault`
- `mfa_last_used_step`
- `password_change_allowed`
- `failed_login_attempts`
- `locked_until`

## Provisión

Dentro de `harness-app`:

```bash
python tools/provision_license_operator.py \
  > /root/harness-license-operator.credentials.json
chmod 600 /root/harness-license-operator.credentials.json
```

El JSON contiene `email`, `password` y `mfa_enrollment_required=true`. En el
primer login el portal muestra la clave/URI TOTP una sola vez y exige confirmar
un código antes de habilitar MFA.

Para rotar password y **resetear el enrollment MFA**:

```bash
python tools/provision_license_operator.py --rotate \
  > /root/harness-license-operator.credentials.json
chmod 600 /root/harness-license-operator.credentials.json
```

La rotación es vendor-side/break-glass, auditada y fuera del portal. No existe
un endpoint de reset para esta cuenta. Después de rotar, el siguiente login
vuelve a pedir la configuración de MFA.

## Login

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "license-operator@harness.local",
  "password": "<password>"
}
```

Primer login: responde `mfa_enrollment_required=true` con `enrollment_token`,
`secret` y `otpauth_uri`. El portal muestra la configuración y confirma con:

```http
POST /api/v1/auth/mfa/enroll/confirm

{
  "enrollment_token": "<token temporal>",
  "totp_code": "123456"
}
```

Logins posteriores:

```json
{
  "email": "license-operator@harness.local",
  "password": "<password>",
  "totp_code": "123456"
}
```

Respuestas relevantes:

- `mfa_enrollment_required`: primer login; configurar MFA.
- `401 MFA_REQUIRED`: falta TOTP.
- `401 MFA_INVALID`: TOTP incorrecto.
- `401 MFA_REPLAY`: TOTP ya usado.
- `423 ACCOUNT_LOCKED`: bloqueo temporal.
- `403 LICENSE_ADMIN_REQUIRED`: un `ADMIN` normal intentó mutar la licencia.
- `403 PASSWORD_SEALED`: intento de cambio/reset de la contraseña sellada.

## UI

- El login muestra el campo **Código MFA**.
- `LICENSE_ADMIN` entra directo a **Licencia y Pool**, sin navegación de
  proyectos/usuarios/SMTP.
- `ADMIN` ve la licencia en modo lectura; los controles de plan, cuotas y
  activación están deshabilitados.
- El panel muestra si el usuario es operador o modo lectura y recuerda que los
  cambios quedan auditados con actor e IP.

## Recuperación

Si se pierde el TOTP o la password:

1. Acceso root al host de Harness2.
2. Ejecutar `provision_license_operator.py --rotate` dentro de `harness-app`.
3. Guardar el nuevo JSON con permisos `600`.
4. Verificar login MFA y revisar `license_audit_events`.

No se debe crear un segundo `LICENSE_ADMIN`: el script lo rechaza.

## Pruebas

- `tests/e2e_stage13_license_admin.py`: migración, cuenta sellada, MFA
  obligatorio, anti-replay, bloqueo, separación Admin/operador, password sellada,
  auditoría actor/IP y UI.
- Regresiones: `e2e_stage10`, `e2e_stage11`, `e2e_stage12` actualizadas para
  usar el token del operador en mutaciones de licencia.

## Límite

En on-premise con root, el cliente puede modificar la base o parchear la app. La
cuenta sellada + MFA + auditoría eleva el costo y deja evidencia, pero la
autoridad final sigue siendo la licencia firmada y el servidor vendor-side.
