# Etapa 7 — UX de Usuarios y Asignación de Proyectos

## Problema

El flujo anterior creaba al usuario **desde una membresía de proyecto**. Si el
Admin no advertía el selector, el usuario quedaba asignado al primer proyecto
(por ejemplo `harness-self`) y no existía una pantalla para crearlo sin proyecto
ni para revisar todas sus membresías.

## Nuevo flujo

La pestaña admin se llama **Usuarios y Accesos** y muestra tres pasos:

1. **Crear usuario** — alta independiente, sin proyectos.
2. **Asignar proyecto** — selección explícita de proyecto y skills.
3. **Compartir credenciales** — correo de bienvenida y correo de acceso al
   proyecto.

### Backend

- `POST /api/v1/admin/users`
  - Crea un `ENGINEER` sin membresías.
  - Acepta `password`, `invite_token` o ninguno (genera una temporal).
  - Aplica la política de contraseñas.
  - Envía `user_welcome` con el enlace al portal y la credencial/invitación.
  - No devuelve contraseñas; solo `generated` y `email_sent`.
  - Si el correo ya existe, responde `409` para evitar duplicados.
- `GET /api/v1/admin/users?search=...`
  - Lista todos los usuarios con rol, estado, membresías por proyecto, skills y
    sesiones activas.
- La asignación sigue usando `POST /api/v1/projects/{id}/members` con
  `user_id` y `allowed_skills`.
- La revocación sigue usando
  `DELETE /api/v1/projects/{id}/members/{user_id}` y cierra las sesiones del
  usuario en ese proyecto.

No hay cambios de esquema: se reutilizan `harness_users`,
`project_memberships`, `agent_sessions` y `email_dispatch_logs`.

### Frontend

- Tarjeta por usuario con:
  - correo, nombre, rol y estado;
  - proyectos aprobados/revocados y skills;
  - número de sesiones activas;
  - acciones **Asignar proyecto**, **Reset password** y **Revocar** por
    membresía.
- Modal **Crear Usuario** sin pedir proyecto.
- Modal **Asignar proyecto** con selector de proyecto y skills.
- Después de crear el usuario, la tarjeta queda visible con “Sin proyectos
  asignados” y el botón **Asignar proyecto**.

## Pruebas

```bash
podman cp tests/e2e_stage7_users.py harness-app:/tmp/e2e_stage7_users.py
podman exec harness-app python /tmp/e2e_stage7_users.py
```

Cubre:

1. Crear `ux-user@empresa.com` sin proyecto.
2. Confirmar que `GET /projects` del usuario devuelve lista vacía (no quedó en
   `harness-self`).
3. Confirmar correo `user_welcome`.
4. Asignar `users-e2e` explícitamente y verificar la membresía.
5. Revocar y confirmar que el usuario deja de ver el proyecto.
6. Generar credencial sin proyecto y confirmar que la API no la expone.
7. Verificar los marcadores de la nueva UI.

## Seguridad

- Las tarjetas de usuario y la API no exponen contraseñas.
- El correo de bienvenida contiene la credencial temporal por requisito
  operativo; debe enviarse por SMTP confiable.
- Si SMTP falla, el usuario se crea igualmente y el Admin puede usar
  **Reset password** cuando el correo esté configurado.
