"""Envio de correo transaccional sin dependencias externas.

La configuracion vive en `system_smtp_config` (fila unica id=1). Si la fila no
existe se usan variables de entorno como fallback. La contrasena puede guardarse
cifrada por la capa de portal con el prefijo `vault:`.
"""
import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Iterable, Optional

import sqlalchemy as sa

from src.security import open_secret


def _decode_password(value: Optional[str]) -> str:
    if not value:
        return ""
    if value.startswith("vault:"):
        return open_secret(value[6:])
    return value


def get_smtp_config(conn) -> Dict[str, Any]:
    row = conn.execute(
        sa.text("""
            SELECT host, port, username, password, from_email, use_tls, use_ssl
            FROM system_smtp_config
            WHERE id = 1
        """)
    ).mappings().fetchone()
    if not row:
        return {
            "host": os.getenv("SMTP_HOST", "localhost"),
            "port": int(os.getenv("SMTP_PORT", "25")),
            "username": os.getenv("SMTP_USER", ""),
            "password": os.getenv("SMTP_PASSWORD", ""),
            "from_email": os.getenv("SMTP_FROM", "harness@controlplane.local"),
            "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
            "use_ssl": os.getenv("SMTP_USE_SSL", "false").lower() == "true",
        }
    config = dict(row)
    config["password"] = _decode_password(config.get("password"))
    return config


def _log_dispatch(engine, recipient: str, subject: str, template_name: str,
                  status: str, error_msg: Optional[str]) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO email_dispatch_logs
                    (recipient, subject, template_name, status, error_message)
                VALUES
                    (:rec, :subj, :tpl, :st, :err)
            """),
            {
                "rec": recipient,
                "subj": subject,
                "tpl": template_name,
                "st": status,
                "err": error_msg,
            },
        )


def send_transactional_email(
    engine,
    recipient: str,
    subject: str,
    html_body: str,
    template_name: str,
) -> bool:
    """Envia un correo HTML y registra el resultado en email_dispatch_logs.

    Nunca propaga el error SMTP: la invitacion/accion de negocio no debe fallar
    porque el servidor de correo este caido. El fallo queda auditado.
    """
    try:
        with engine.connect() as conn:
            cfg = get_smtp_config(conn)
    except Exception as exc:
        try:
            _log_dispatch(engine, recipient, subject, template_name, "FAILED", f"config: {exc}")
        except Exception:
            pass
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_email"]
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    status = "SENT"
    error_msg = None
    server = None
    try:
        if cfg.get("use_ssl"):
            server = smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=10)
            if cfg.get("use_tls"):
                server.starttls()
        if cfg.get("username") and cfg.get("password"):
            server.login(cfg["username"], cfg["password"])
        server.sendmail(cfg["from_email"], [recipient], msg.as_string())
    except Exception as exc:
        status = "FAILED"
        error_msg = str(exc)[:4000]
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

    try:
        _log_dispatch(engine, recipient, subject, template_name, status, error_msg)
    except Exception:
        return False
    return status == "SENT"


def render_project_invite(
    portal_url: str,
    recipient: str,
    projects: Iterable[Dict[str, Any]],
    initial_password: Optional[str] = None,
    invite_token: Optional[str] = None,
) -> str:
    project_rows = []
    for project in projects:
        skills = project.get("allowed_skills") or []
        skills_html = "".join(
            f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;'
            f'border-radius:999px;background:#e0f2fe;color:#075985;font-size:12px;">{html.escape(str(s))}</span>'
            for s in skills
        ) or '<span style="color:#64748b;font-size:12px;">Sin skills asignadas</span>'
        project_rows.append(
            '<tr>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">'
            f'<b>{html.escape(str(project.get("display_name") or project.get("project_slug") or ""))}</b><br>'
            f'<span style="color:#64748b;font-family:monospace;font-size:12px;">'
            f'{html.escape(str(project.get("project_slug") or ""))}</span>'
            '</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{skills_html}</td>'
            '</tr>'
        )

    credential_block = ""
    if initial_password:
        credential_block += (
            '<div style="margin:14px 0;padding:12px 14px;border:1px solid #bae6fd;'
            'border-radius:10px;background:#f0f9ff;">'
            '<div style="font-size:12px;color:#0369a1;font-weight:700;">Contrasena inicial</div>'
            f'<div style="font-family:monospace;font-size:16px;color:#0c4a6e;">{html.escape(initial_password)}</div>'
            '<div style="font-size:12px;color:#475569;margin-top:4px;">Cambiala al iniciar sesion.</div>'
            '</div>'
        )
    if invite_token:
        credential_block += (
            '<div style="margin:14px 0;padding:12px 14px;border:1px solid #ddd6fe;'
            'border-radius:10px;background:#f5f3ff;">'
            '<div style="font-size:12px;color:#6d28d9;font-weight:700;">Token de invitacion</div>'
            f'<div style="font-family:monospace;font-size:13px;color:#4c1d95;word-break:break-all;">{html.escape(invite_token)}</div>'
            '<div style="font-size:12px;color:#475569;margin-top:4px;">Usalo en el registro del portal.</div>'
            '</div>'
        )

    return f"""<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
  <div style="max-width:620px;margin:0 auto;padding:24px 12px;">
    <div style="background:#0b1220;color:#e2e8f0;border-radius:14px 14px 0 0;padding:22px 24px;">
      <div style="color:#38bdf8;font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;">Harness Control Plane</div>
      <h1 style="margin:8px 0 0;font-size:22px;line-height:1.2;">Acceso aprobado a un proyecto</h1>
    </div>
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 14px 14px;padding:24px;">
      <p style="margin:0 0 12px;">Hola,</p>
      <p style="margin:0 0 14px;">Tu cuenta <b>{html.escape(recipient)}</b> fue aprobada para trabajar en el siguiente proyecto:</p>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">
        <thead><tr style="background:#f8fafc;"><th align="left" style="padding:9px 12px;font-size:11px;color:#64748b;text-transform:uppercase;">Proyecto</th><th align="left" style="padding:9px 12px;font-size:11px;color:#64748b;text-transform:uppercase;">Skills aprobadas</th></tr></thead>
        <tbody>{''.join(project_rows)}</tbody>
      </table>
      {credential_block}
      <p style="margin:18px 0 8px;">Ingresa al portal para iniciar tu sesion de agente:</p>
      <p style="margin:0 0 18px;"><a href="{html.escape(portal_url)}" style="display:inline-block;background:#0284c7;color:#ffffff;text-decoration:none;padding:11px 18px;border-radius:9px;font-weight:700;">{html.escape(portal_url)}</a></p>
      <p style="margin:0;color:#64748b;font-size:12px;">Recuerda: cada proyecto tiene su propia sesion y su propia session_key. No compartas tus credenciales.</p>
    </div>
  </div>
</body>
</html>"""


def _layout(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
  <div style="max-width:620px;margin:0 auto;padding:24px 12px;">
    <div style="background:#0b1220;color:#e2e8f0;border-radius:14px 14px 0 0;padding:22px 24px;">
      <div style="color:#38bdf8;font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;">Harness Control Plane</div>
      <h1 style="margin:8px 0 0;font-size:22px;line-height:1.2;">{html.escape(title)}</h1>
    </div>
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 14px 14px;padding:24px;">
      {body_html}
    </div>
  </div>
</body>
</html>"""


def render_password_changed(portal_url: str, recipient: str) -> str:
    body = f"""
      <p style="margin:0 0 12px;">Hola,</p>
      <p style="margin:0 0 12px;">La contrasena de la cuenta <b>{html.escape(recipient)}</b> fue actualizada correctamente.</p>
      <p style="margin:0 0 12px;">Si no realizaste este cambio, contacta de inmediato al administrador del Control Plane.</p>
      <p style="margin:18px 0 0;"><a href="{html.escape(portal_url)}" style="color:#0284c7;">{html.escape(portal_url)}</a></p>
    """
    return _layout("Contrasena actualizada", body)


def render_admin_password_reset(portal_url: str, recipient: str, temporary_password: str) -> str:
    body = f"""
      <p style="margin:0 0 12px;">Hola,</p>
      <p style="margin:0 0 12px;">El administrador restablecio la contrasena de la cuenta <b>{html.escape(recipient)}</b>.</p>
      <div style="margin:14px 0;padding:12px 14px;border:1px solid #bae6fd;border-radius:10px;background:#f0f9ff;">
        <div style="font-size:12px;color:#0369a1;font-weight:700;">Contrasena temporal</div>
        <div style="font-family:monospace;font-size:16px;color:#0c4a6e;">{html.escape(temporary_password)}</div>
      </div>
      <p style="margin:0 0 12px;">Inicia sesion con esta contrasena provisional y cambiala en tu primer acceso.</p>
      <p style="margin:18px 0 0;"><a href="{html.escape(portal_url)}" style="color:#0284c7;">{html.escape(portal_url)}</a></p>
    """
    return _layout("Contrasena restablecida", body)


def render_user_welcome(
    portal_url: str,
    recipient: str,
    temporary_password: Optional[str] = None,
    invite_token: Optional[str] = None,
) -> str:
    credential_block = ""
    if temporary_password:
        credential_block += (
            '<div style="margin:14px 0;padding:12px 14px;border:1px solid #bae6fd;'
            'border-radius:10px;background:#f0f9ff;">'
            '<div style="font-size:12px;color:#0369a1;font-weight:700;">Contrasena inicial</div>'
            f'<div style="font-family:monospace;font-size:16px;color:#0c4a6e;">{html.escape(temporary_password)}</div>'
            '<div style="font-size:12px;color:#475569;margin-top:4px;">Cambiala en tu primer acceso.</div>'
            '</div>'
        )
    if invite_token:
        credential_block += (
            '<div style="margin:14px 0;padding:12px 14px;border:1px solid #ddd6fe;'
            'border-radius:10px;background:#f5f3ff;">'
            '<div style="font-size:12px;color:#6d28d9;font-weight:700;">Token de invitacion</div>'
            f'<div style="font-family:monospace;font-size:13px;color:#4c1d95;word-break:break-all;">{html.escape(invite_token)}</div>'
            '<div style="font-size:12px;color:#475569;margin-top:4px;">Usalo en el registro del portal.</div>'
            '</div>'
        )
    body = f"""
      <p style="margin:0 0 12px;">Hola,</p>
      <p style="margin:0 0 12px;">El administrador creo una cuenta de Harness Control Plane para <b>{html.escape(recipient)}</b>.</p>
      {credential_block}
      <p style="margin:0 0 12px;">Tu cuenta aun no tiene proyectos asignados. Cuando el administrador te asigne uno, recibiras otro correo con el detalle.</p>
      <p style="margin:18px 0 0;"><a href="{html.escape(portal_url)}" style="color:#0284c7;">{html.escape(portal_url)}</a></p>
    """
    return _layout("Tu cuenta fue creada", body)
