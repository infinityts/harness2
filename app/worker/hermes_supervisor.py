"""Supervisor del pool Hermes por proyecto para Harness2.

Un solo contenedor, N sesiones logicas (una por proyecto activo), pool acotado
de jobs RAG. Cada sesion tiene su propio bridge WebSocket y su propio contexto;
los jobs se ejecutan con Hermes CLI en un workspace aislado.
"""
import concurrent.futures
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

HARNESS_URL = os.environ.get("HARNESS_URL", "http://harness-app:8443").rstrip("/")
WORKER_KEY = os.environ.get("HARNESS_WORKER_KEY", "").strip()
ADMIN_KEY = os.environ.get("HARNESS_ADMIN_API_KEY", "").strip()
HERMES_BIN = os.environ.get("HERMES_BIN", "/opt/hermes/hermes")
HERMES_PYTHON = os.environ.get("HERMES_PYTHON", "/opt/hermes/.venv/bin/python")
MAX_PARALLEL = max(1, int(os.environ.get("MAX_PARALLEL_JOBS", "3")))
POOL_LIMIT = MAX_PARALLEL
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))
SYNC_SECONDS = float(os.environ.get("SYNC_SECONDS", "10"))
HERMES_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "240"))
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/opt/harness-worker/workspaces"))
BRIDGE_PATH = Path(os.environ.get("BRIDGE_PATH", "/opt/harness-worker/harness_mcp_bridge.py"))
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"

BRIDGES = {}
BRIDGE_LOCK = Lock()
STOP = False


def log(message):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {message}", flush=True)


def _request(path, method="GET", body=None, headers=None, timeout=90):
    hdrs = {"Content-Type": "application/json"}
    if WORKER_KEY:
        hdrs["X-Harness-Worker-Key"] = WORKER_KEY
    elif ADMIN_KEY:
        hdrs["X-Harness-Admin-Key"] = ADMIN_KEY
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(HARNESS_URL + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {raw[:1500]}") from exc


def _session_request(path, method="GET", body=None, session_key="", timeout=90):
    hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {session_key}"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(HARNESS_URL + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {raw[:1500]}") from exc


def _session_dir(session_id):
    path = WORKSPACE_ROOT / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_status(session_id, status):
    path = _session_dir(session_id) / "status"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(status, encoding="utf-8")
    tmp.replace(path)


def stop_bridge(session_id):
    with BRIDGE_LOCK:
        entry = BRIDGES.pop(session_id, None)
    if not entry:
        return
    proc = entry.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def ensure_bridge(session):
    session_id = session["session_id"]
    session_key = session.get("session_key") or ""
    if not session_id or not session_key:
        return
    with BRIDGE_LOCK:
        entry = BRIDGES.get(session_id)
        if entry and entry["proc"].poll() is None and entry.get("session_key") == session_key:
            return
        if entry:
            try:
                entry["proc"].terminate()
                entry["proc"].wait(timeout=3)
            except Exception:
                try:
                    entry["proc"].kill()
                except Exception:
                    pass
            BRIDGES.pop(session_id, None)
        session_dir = _session_dir(session_id)
        status_file = session_dir / "status"
        status_file.write_text("ONLINE", encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "HARNESS_URL": HARNESS_URL,
            "HARNESS_SESSION_ID": session_id,
            "HARNESS_SESSION_KEY": session_key,
            "HARNESS_HEARTBEAT_SECONDS": "5",
            "HARNESS_STATUS_FILE": str(status_file),
            "HARNESS_PROJECT_SLUG": session.get("project_slug") or "",
            "HARNESS_CURRENT_TOKENS": "0",
            "PYTHONUNBUFFERED": "1",
        })
        if not BRIDGE_PATH.exists():
            log(f"bridge no encontrado: {BRIDGE_PATH}")
            return
        proc = subprocess.Popen(
            [sys.executable, str(BRIDGE_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        BRIDGES[session_id] = {"proc": proc, "session_key": session_key, "status_file": status_file}
        log(f"bridge iniciado session={session_id[:8]} project={session.get('project_slug')}")


def sync_sessions():
    global POOL_LIMIT
    data = _request("/api/v1/internal/hermes/pool/sync", "POST", {})
    if not data:
        return {}
    try:
        policy_limit = int(data.get("max_parallel_rag_jobs") or MAX_PARALLEL)
        POOL_LIMIT = max(1, min(MAX_PARALLEL, policy_limit))
    except Exception:
        POOL_LIMIT = MAX_PARALLEL
    sessions = {s["session_id"]: s for s in data.get("sessions", [])}
    for session in sessions.values():
        ensure_bridge(session)
    with BRIDGE_LOCK:
        stale = [sid for sid in BRIDGES if sid not in sessions]
    for sid in stale:
        stop_bridge(sid)
    return sessions


def _repo_map_text(repo_map):
    lines = []
    for row in (repo_map or [])[:60]:
        symbols = []
        for symbol in (row.get("exported_symbols") or [])[:6]:
            if isinstance(symbol, dict):
                symbols.append(str(symbol.get("name") or ""))
            else:
                symbols.append(str(symbol))
        suffix = f" [simbolos: {', '.join(s for s in symbols if s)}]" if symbols else ""
        lines.append(f"- {row.get('file_path')}: {row.get('quick_summary')}{suffix}")
    return "\n".join(lines) or "- (sin mapa de archivos indexado)"


def build_prompt(job, project, repo_map):
    job_type = job.get("job_type")
    payload = job.get("payload") or {}
    extra = ""
    if job_type == "RAG_UPDATE_ARCHITECTURE" and payload.get("task_id"):
        extra = f"\nSe acaba de fusionar la tarea {payload['task_id']}; actualiza la arquitectura para reflejarla."
    return f"""Eres el curador documental del proyecto "{project.get('display_name')}" ({project.get('project_slug')}) en Harness2.
Trabajo: {job_type}.{extra}

Genera una vision general de arquitectura en Markdown para el RAG documental del proyecto.
Usa estas secciones:
# Arquitectura actual de {project.get('display_name')}
## Resumen
## Modulos y responsabilidades
## Flujos principales
## Contratos operativos
## Riesgos y deuda tecnica
## Documentacion faltante

Contexto del repositorio:
{_repo_map_text(repo_map)}

Reglas:
- No inventes archivos, modulos ni funciones que no aparezcan en el contexto.
- No incluyas secretos, tokens, passwords ni URLs con credenciales.
- Se conciso: maximo 1000 palabras.
- Devuelve SOLO el documento Markdown, sin explicaciones ni bloques de codigo envolventes.
"""


def call_hermes(prompt, workspace, usage_path):
    cmd = [
        HERMES_PYTHON, HERMES_BIN,
        "--in", str(workspace),
        "--ignore-rules",
        "-z", prompt,
        "--usage-file", str(usage_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=HERMES_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes exit {proc.returncode}: {(proc.stderr or proc.stdout)[-1500:]}")
    content = (proc.stdout or "").strip()
    if not content:
        raise RuntimeError("hermes devolvio contenido vacio")
    usage = {}
    try:
        usage = json.loads(Path(usage_path).read_text(encoding="utf-8"))
    except Exception:
        pass
    return content, usage


def run_job(data):
    job = data["job"]
    session = data["session"]
    project = data["project"]
    repo_map = data.get("repo_map") or []
    job_id = job["id"]
    session_id = session["session_id"]
    session_key = session["session_key"]
    project_id = job["project_id"]
    ensure_bridge(session)
    write_status(session_id, "BUSY")
    session_dir = _session_dir(session_id)
    workspace = session_dir / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    usage_path = session_dir / "usage.json"
    log(f"job {job_id[:8]} {job['job_type']} project={project['project_slug']}")
    stop_heartbeat = threading.Event()

    def heartbeat_loop():
        while not stop_heartbeat.wait(60):
            try:
                _request(
                    f"/api/v1/internal/hermes/jobs/{job_id}/heartbeat",
                    "POST",
                    {"worker_id": WORKER_ID},
                    timeout=15,
                )
            except Exception as exc:
                log(f"heartbeat job {job_id[:8]} fallo: {exc}")

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    try:
        if job["job_type"] == "RAG_INGEST_SOURCE":
            payload = job.get("payload") or {}
            if not payload.get("content") or not payload.get("source_uri"):
                raise ValueError("RAG_INGEST_SOURCE requiere source_uri y content")
            ingested = _session_request(
                f"/api/v1/projects/{project_id}/documents",
                "POST",
                {
                    "source_uri": payload["source_uri"],
                    "title": payload.get("title") or payload["source_uri"],
                    "doc_type": payload.get("doc_type", "OTHER"),
                    "content": payload["content"],
                    "metadata": payload.get("metadata") or {},
                },
                session_key,
            )
            result = {"hermes": False, "document": ingested}
        else:
            prompt = build_prompt(job, project, repo_map)
            content, usage = call_hermes(prompt, workspace, usage_path)
            ingested = _session_request(
                f"/api/v1/projects/{project_id}/documents",
                "POST",
                {
                    "source_uri": "auto://hermes-architecture.md",
                    "title": f"Arquitectura curada por Hermes - {project.get('display_name')}",
                    "doc_type": "ARCHITECTURE",
                    "content": content,
                    "metadata": {
                        "source": "hermes",
                        "job_id": job_id,
                        "job_type": job["job_type"],
                        "model": usage.get("model"),
                    },
                },
                session_key,
            )
            bootstrap = _request(
                f"/api/v1/internal/projects/{project_id}/rag/bootstrap",
                "POST",
                {"session_id": session_id, "trigger": f"hermes_{job['job_type'].lower()}"},
                timeout=300,
            )
            result = {
                "hermes": True,
                "usage": usage,
                "document": ingested,
                "bootstrap": bootstrap,
            }
        _request(
            f"/api/v1/internal/hermes/jobs/{job_id}/complete",
            "POST",
            {"worker_id": WORKER_ID, "result": result},
        )
        log(f"job {job_id[:8]} completado")
    except Exception as exc:
        log(f"job {job_id[:8]} fallo: {exc}")
        fallback = None
        if job["job_type"] != "RAG_INGEST_SOURCE":
            try:
                fallback = _request(
                    f"/api/v1/internal/projects/{project_id}/rag/bootstrap",
                    "POST",
                    {"session_id": session_id, "trigger": f"fallback_{job['job_type'].lower()}"},
                    timeout=300,
                )
            except Exception as fallback_exc:
                log(f"fallback bootstrap fallo: {fallback_exc}")
        if fallback is not None:
            try:
                _request(
                    f"/api/v1/internal/hermes/jobs/{job_id}/complete",
                    "POST",
                    {"worker_id": WORKER_ID, "result": {
                        "hermes": False,
                        "fallback": True,
                        "error": str(exc)[:1500],
                        "bootstrap": fallback,
                    }},
                )
                log(f"job {job_id[:8]} completado con fallback deterministico")
            except Exception as complete_exc:
                log(f"no se pudo completar fallback: {complete_exc}")
        else:
            try:
                _request(
                    f"/api/v1/internal/hermes/jobs/{job_id}/fail",
                    "POST",
                    {"worker_id": WORKER_ID, "error_message": str(exc)[:3500]},
                )
            except Exception as fail_exc:
                log(f"no se pudo reportar fallo: {fail_exc}")
    finally:
        stop_heartbeat.set()
        write_status(session_id, "ONLINE")


def _handle_stop(signum, frame):
    global STOP
    STOP = True


def main():
    global STOP
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    log(f"supervisor iniciado worker={WORKER_ID} harness={HARNESS_URL} max_parallel={MAX_PARALLEL}")
    for _ in range(60):
        try:
            _request("/health", "GET", None, timeout=5)
            break
        except Exception:
            time.sleep(2)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL)
    futures = {}
    last_sync = 0
    try:
        while not STOP:
            now = time.time()
            if now - last_sync >= SYNC_SECONDS:
                try:
                    sessions = sync_sessions()
                    log(f"pool sync: {len(sessions)} sesiones activas, limite_jobs={POOL_LIMIT}")
                except Exception as exc:
                    log(f"pool sync fallo: {exc}")
                last_sync = now
            for job_id, future in list(futures.items()):
                if future.done():
                    futures.pop(job_id, None)
                    try:
                        future.result()
                    except Exception as exc:
                        log(f"future job {job_id[:8]} fallo: {exc}")
            while len(futures) < POOL_LIMIT and not STOP:
                try:
                    claimed = _request("/api/v1/internal/hermes/jobs/claim", "POST", {"worker_id": WORKER_ID})
                except Exception as exc:
                    log(f"claim fallo: {exc}")
                    break
                if not claimed or not claimed.get("job"):
                    break
                job = claimed["job"]
                futures[job["id"]] = executor.submit(run_job, claimed)
            time.sleep(POLL_SECONDS)
    finally:
        for sid in list(BRIDGES.keys()):
            stop_bridge(sid)
        executor.shutdown(wait=False, cancel_futures=True)
        log("supervisor detenido")


if __name__ == "__main__":
    main()
