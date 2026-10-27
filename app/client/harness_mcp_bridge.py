#!/usr/bin/env python3
"""
Harness Universal MCP/CLI Bridge.

Modos:
  * MCP stdio (por defecto): initialize, tools/list, tools/call.
  * CLI: python harness_mcp_bridge.py --cli harness_get_active_contract_and_task '{}'

Variables:
  HARNESS_URL              (default http://11.0.0.3:8444)
  HARNESS_SESSION_ID
  HARNESS_SESSION_KEY
  HARNESS_HEARTBEAT_SECONDS (default 3)
  HARNESS_CURRENT_TOKENS    (default 0)
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HARNESS_URL = os.environ.get("HARNESS_URL", "http://11.0.0.3:8444").rstrip("/")
SESSION_ID = os.environ.get("HARNESS_SESSION_ID", "").strip()
SESSION_KEY = os.environ.get("HARNESS_SESSION_KEY", "").strip()
HEARTBEAT_SECONDS = float(os.environ.get("HARNESS_HEARTBEAT_SECONDS", "3"))
INITIAL_TOKENS = int(os.environ.get("HARNESS_CURRENT_TOKENS", "0"))

_parsed = urllib.parse.urlparse(HARNESS_URL)
_ws_scheme = "wss" if _parsed.scheme == "https" else "ws"
WS_URL = f"{_ws_scheme}://{_parsed.netloc}/ws/agent/{SESSION_ID}"

state_lock = threading.RLock()
bridge_state = {
    "session_id": SESSION_ID,
    "skills": [],
    "max_context_tokens": 32000,
    "system_contract_rules": [],
    "current_context_tokens": INITIAL_TOKENS,
    "status": "ONLINE",
    "task": None,
    "messages": [],
    "last_event": None,
    "connected": False,
    "purge_requested": False,
}

_ws = None
_ws_lock = threading.Lock()
_stop = threading.Event()


def log(message):
    print(f"[harness-bridge] {message}", file=sys.stderr, flush=True)


def api(method, path, body=None, timeout=60):
    url = HARNESS_URL + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SESSION_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return exc.code, payload
    except Exception as exc:
        return 0, {"error": str(exc)}


def _state(**updates):
    with state_lock:
        bridge_state.update(updates)


def _snapshot():
    with state_lock:
        return dict(bridge_state)


class WSClient:
    def __init__(self, url, headers):
        self.url = url
        self.headers = headers
        self.sock = None
        self.buf = b""
        self.send_lock = threading.Lock()

    def connect(self):
        parsed = urllib.parse.urlparse(self.url)
        secure = parsed.scheme == "wss"
        host = parsed.hostname
        port = parsed.port or (443 if secure else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        raw = socket.create_connection((host, port), timeout=10)
        if secure:
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
        )
        for name, value in self.headers.items():
            request += f"{name}: {value}\r\n"
        request += "\r\n"
        raw.sendall(request.encode("utf-8"))
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = raw.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed")
            data += chunk
        header, _, rest = data.partition(b"\r\n\r\n")
        status_line = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise ConnectionError("WebSocket handshake failed: " + status_line.decode(errors="ignore"))
        self.buf = rest
        self.sock = raw

    def _send_frame(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_text(self, text):
        with self.send_lock:
            self._send_frame(0x1, text.encode("utf-8"))

    def _read_exact(self, size):
        while len(self.buf) < size:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def recv_text(self):
        while True:
            b1, b2 = self._read_exact(2)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask_key = self._read_exact(4) if masked else None
            payload = self._read_exact(length)
            if mask_key:
                payload = bytes(byte ^ mask_key[i % 4] for i, byte in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("WebSocket close frame")
            if opcode == 0x9:
                with self.send_lock:
                    self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", errors="replace")

    def close(self):
        try:
            with self.send_lock:
                self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def _handle_message(message):
    event = message.get("event")
    with state_lock:
        bridge_state["last_event"] = message
        if event == "SESSION_SYNC":
            bridge_state["skills"] = message.get("skills") or []
            bridge_state["max_context_tokens"] = message.get("max_context_tokens") or 32000
            bridge_state["system_contract_rules"] = message.get("system_contract_rules") or []
        elif event == "CONTROL_RULES_UPDATED":
            bridge_state["skills"] = message.get("skills") or bridge_state["skills"]
            bridge_state["max_context_tokens"] = message.get("max_context_tokens") or bridge_state["max_context_tokens"]
            bridge_state["system_contract_rules"] = message.get("system_contract_rules") or bridge_state["system_contract_rules"]
        elif event == "TASK_ASSIGNED":
            bridge_state["task"] = message.get("task")
            bridge_state["status"] = "BUSY"
        elif event in ("UPSTREAM_TASK_MERGED", "AGENT_MESSAGE"):
            bridge_state["messages"].append(message)
            bridge_state["messages"] = bridge_state["messages"][-20:]
        elif event == "CLEAR_LLM_CONTEXT":
            bridge_state["task"] = None
            bridge_state["status"] = "ONLINE"
            bridge_state["current_context_tokens"] = 0
        if message.get("action") == "PURGE_AND_CHECKPOINT_CONTEXT":
            bridge_state["purge_requested"] = True


def ws_loop():
    global _ws
    while not _stop.is_set():
        client = None
        try:
            client = WSClient(WS_URL, {"Authorization": f"Bearer {SESSION_KEY}"})
            client.connect()
            with _ws_lock:
                _ws = client
            _state(connected=True)
            with state_lock:
                initial = {
                    "current_context_tokens": bridge_state["current_context_tokens"],
                    "status": bridge_state["status"],
                }
            client.send_text(json.dumps(initial))
            while not _stop.is_set():
                _handle_message(json.loads(client.recv_text()))
        except Exception as exc:
            log(f"websocket: {exc}")
            _state(connected=False)
        finally:
            with _ws_lock:
                _ws = None
            if client:
                client.close()
        if not _stop.is_set():
            time.sleep(2)


def _send_heartbeat_now():
    with _ws_lock:
        client = _ws
    if not client:
        return
    with state_lock:
        payload = {
            "current_context_tokens": bridge_state["current_context_tokens"],
            "status": bridge_state["status"],
        }
    try:
        client.send_text(json.dumps(payload))
    except Exception:
        pass


def heartbeat_loop():
    while not _stop.is_set():
        with _ws_lock:
            client = _ws
        if client:
            with state_lock:
                payload = {
                    "current_context_tokens": bridge_state["current_context_tokens"],
                    "status": bridge_state["status"],
                }
            try:
                client.send_text(json.dumps(payload))
            except Exception:
                pass
        time.sleep(HEARTBEAT_SECONDS)


def start_daemon():
    threading.Thread(target=ws_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()


def _session_fallback():
    status, data = api("GET", "/api/v1/sessions")
    if status == 200 and isinstance(data, dict):
        for item in data.get("sessions", []):
            if item.get("id") == SESSION_ID:
                return item
    return None


def tool_get_active_contract_and_task(args=None):
    status, data = api("GET", "/api/v1/tasks")
    if status != 200:
        raise RuntimeError(f"tasks HTTP {status}: {data}")
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    active = next((t for t in tasks if t.get("status") in ("IN_PROGRESS", "VALIDATING", "CONFLICT")), None)
    snap = _snapshot()
    rules = snap["system_contract_rules"]
    skills = snap["skills"]
    max_tokens = snap["max_context_tokens"]
    if not rules or not skills:
        mine = _session_fallback() or {}
        rules = rules or mine.get("system_contract_rules") or []
        skills = skills or mine.get("skills") or []
        max_tokens = mine.get("max_context_tokens") or max_tokens
    current = snap["current_context_tokens"]
    return {
        "session_id": SESSION_ID,
        "connected": snap["connected"],
        "status": snap["status"],
        "skills": skills,
        "max_context_tokens": max_tokens,
        "current_context_tokens": current,
        "context_pct": round((current / max_tokens) * 100, 1) if max_tokens else 0.0,
        "system_contract_rules": rules,
        "purge_requested": snap["purge_requested"],
        "task": active,
        "pending_tasks": [t for t in tasks if t.get("status") == "PENDING"],
        "messages": snap["messages"][-10:],
    }


def tool_request_context_pack(args):
    repo_name = args.get("repo_name")
    query = args.get("query")
    if not repo_name or not query:
        raise ValueError("repo_name y query son obligatorios")
    body = {
        "repo_name": repo_name,
        "query": query,
        "token_budget": int(args.get("token_budget", 2000)),
    }
    if args.get("allowed_files"):
        body["allowed_files"] = args["allowed_files"]
    status, data = api("POST", "/api/v1/context/pack", body)
    if status != 200:
        raise RuntimeError(f"context/pack HTTP {status}: {data}")
    used = int(data.get("estimated_tokens_used") or 0)
    with state_lock:
        bridge_state["current_context_tokens"] += used
    return data


def tool_validate_symbols(args):
    repo_name = args.get("repo_name")
    symbols = args.get("referenced_symbols") or []
    if not repo_name or not symbols:
        raise ValueError("repo_name y referenced_symbols son obligatorios")
    status, data = api("POST", "/api/v1/guardrails/validate-symbols", {
        "repo_name": repo_name,
        "referenced_symbols": symbols,
    })
    if status != 200:
        raise RuntimeError(f"validate-symbols HTTP {status}: {data}")
    return data


def tool_submit_task_changes(args):
    task_id = args.get("task_id")
    if not task_id:
        raise ValueError("task_id es obligatorio")
    status, data = api("POST", f"/api/v1/tasks/{task_id}/submit", {
        "file_changes": args.get("file_changes"),
        "unified_diff": args.get("unified_diff"),
        "commit_message": args.get("commit_message", "harness bridge submit"),
    }, timeout=180)
    purged = False
    if status == 200 and isinstance(data, dict) and data.get("status") in ("MERGED", "VALIDATING"):
        with state_lock:
            bridge_state["current_context_tokens"] = 0
            bridge_state["task"] = None
            bridge_state["status"] = "ONLINE"
            bridge_state["purge_requested"] = False
        _send_heartbeat_now()
        purge_status, _ = api("POST", f"/api/v1/sessions/{SESSION_ID}/context-purged")
        purged = purge_status == 200
    return {"http_status": status, "result": data, "context_purged": purged}


def tool_report_checkpoint_failure(args):
    task_id = args.get("task_id")
    if not task_id:
        raise ValueError("task_id es obligatorio")
    status, data = api("POST", f"/api/v1/tasks/{task_id}/fail-checkpoint", {
        "error_reason": args.get("error_reason", "context purge requested"),
        "git_diff_summary": args.get("git_diff_summary"),
        "release": bool(args.get("release", True)),
    })
    if status == 200:
        with state_lock:
            bridge_state["current_context_tokens"] = 0
            bridge_state["task"] = None
            bridge_state["status"] = "ONLINE"
            bridge_state["purge_requested"] = False
        _send_heartbeat_now()
    return {"http_status": status, "result": data}


def tool_send_peer_message(args):
    target = args.get("target_session_id")
    content = args.get("content")
    if not target or not content:
        raise ValueError("target_session_id y content son obligatorios")
    status, data = api("POST", "/api/v1/war-room/relay", {
        "target_session_id": target,
        "message_type": args.get("message_type", "note"),
        "content": content,
        "task_id": args.get("task_id"),
    })
    if status != 200:
        raise RuntimeError(f"relay HTTP {status}: {data}")
    return data


TOOL_FUNCS = {
    "harness_get_active_contract_and_task": tool_get_active_contract_and_task,
    "harness_request_context_pack": tool_request_context_pack,
    "harness_validate_symbols": tool_validate_symbols,
    "harness_submit_task_changes": tool_submit_task_changes,
    "harness_report_checkpoint_failure": tool_report_checkpoint_failure,
    "harness_send_peer_message": tool_send_peer_message,
}

TOOLS = [
    {
        "name": "harness_get_active_contract_and_task",
        "description": "Devuelve reglas de contrato, skills, contexto, mensajes y la tarea activa asignada por Harness.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "harness_request_context_pack",
        "description": "Pide un Context Pack AST quirurgico bajo un presupuesto de tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "query": {"type": "string"},
                "token_budget": {"type": "integer", "minimum": 300, "maximum": 16000},
                "allowed_files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repo_name", "query"],
        },
    },
    {
        "name": "harness_validate_symbols",
        "description": "Valida simbolos contra el AST indexado para evitar alucinaciones.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "referenced_symbols": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repo_name", "referenced_symbols"],
        },
    },
    {
        "name": "harness_submit_task_changes",
        "description": "Envia cambios reales al Git Guardian (4 barreras, merge y reindex).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "file_changes": {"type": "object", "additionalProperties": {"type": "string"}},
                "unified_diff": {"type": "string"},
                "commit_message": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "harness_report_checkpoint_failure",
        "description": "Registra un fallo/bloqueo y libera la tarea con memoria persistente.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "error_reason": {"type": "string"},
                "git_diff_summary": {"type": "string"},
                "release": {"type": "boolean"},
            },
            "required": ["task_id", "error_reason"],
        },
    },
    {
        "name": "harness_send_peer_message",
        "description": "Envia un mensaje a otro agente solo si existe un enlace activo en la War Room.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_session_id": {"type": "string"},
                "message_type": {"type": "string"},
                "content": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["target_session_id", "content"],
        },
    },
]


def mcp_send(message):
    print(json.dumps(message), flush=True)


def mcp_handle(request):
    method = request.get("method")
    req_id = request.get("id")
    if method == "initialize":
        mcp_send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "harness-mcp-bridge", "version": "1.0.0"},
            },
        })
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        func = TOOL_FUNCS.get(name)
        if not func:
            mcp_send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"unknown tool {name}"}})
            return
        try:
            output = func(args)
            mcp_send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(output)}], "isError": False},
            })
        except Exception as exc:
            mcp_send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}], "isError": True},
            })
    elif method == "ping":
        mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {}})
    else:
        if req_id is not None:
            mcp_send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}})


def mcp_loop():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception as exc:
            log(f"invalid json: {exc}")
            continue
        mcp_handle(request)


def run_cli(argv):
    if not argv:
        print("uso: --cli <tool> ['{\"arg\": ...}']", file=sys.stderr)
        return 2
    tool = argv[0]
    raw = argv[1] if len(argv) > 1 else "{}"
    func = TOOL_FUNCS.get(tool)
    if not func:
        print(f"tool desconocida: {tool}", file=sys.stderr)
        return 2
    args = json.loads(raw) if raw else {}
    print(json.dumps(func(args), indent=2), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Harness MCP/CLI bridge")
    parser.add_argument("--cli", nargs=argparse.REMAINDER, help="Ejecuta una tool y sale")
    opts = parser.parse_args()

    if not SESSION_ID or not SESSION_KEY:
        log("faltan HARNESS_SESSION_ID / HARNESS_SESSION_KEY")
        return 2

    start_daemon()
    if opts.cli:
        return run_cli(opts.cli)
    mcp_loop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _stop.set()
        sys.exit(0)
