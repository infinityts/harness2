"""Prueba E2E Etapa 1: registro, WS, purga de contexto y watchdog."""
import asyncio, json, sys, time, urllib.request
import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://11.0.0.3:8444"
WS = BASE.replace("http", "ws", 1)

def http(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def show(title, obj):
    print(f"\n=== {title} ===\n" + json.dumps(obj, indent=2, default=str))

def session(sid):
    return next(s for s in http("GET", "/api/v1/sessions")["sessions"] if s["id"] == sid)

async def main():
    reg = http("POST", "/api/v1/sessions/register", {
        "engineer_username": "jcardona", "agent_tool": "opencode", "llm_provider": "local-qwen",
        "skills": ["python", "rust", "git"], "max_context_tokens": 32000})
    show("1. POST /api/v1/sessions/register", {**reg, "session_key": reg["session_key"][:25] + "...(truncado)"})
    sid = reg["session_id"]

    # Token invalido -> 4003
    try:
        async with websockets.connect(f"{WS}/ws/agent/{sid}?token=bad") as w:
            await w.recv()
    except websockets.ConnectionClosed as e:
        print(f"\n=== 2. WS con token invalido ===\ncerrado con codigo {e.rcvd.code if e.rcvd else e}")
    except Exception as e:
        print(f"\n=== 2. WS con token invalido ===\nrechazado: {e}")

    async with websockets.connect(f"{WS}{reg['ws_url']}") as w:
        await w.send(json.dumps({"current_context_tokens": 22000}))
        show("3. WS heartbeat 22000/32000", json.loads(await w.recv()))
        show("   sesion en BD", {k: session(sid)[k] for k in ("status", "current_context_tokens")})

        show("4. POST context-purged", http("POST", f"/api/v1/sessions/{sid}/context-purged"))
        show("   sesion en BD", {k: session(sid)[k] for k in ("status", "current_context_tokens")})

        await w.send(json.dumps({"current_context_tokens": 0}))
        show("   heartbeat post-purga", json.loads(await w.recv()))

        print("\n=== 5. Silencio de heartbeat 13s (socket abierto) ===")
        t0 = time.time()
        try:
            await asyncio.wait_for(w.recv(), timeout=20)
        except websockets.ConnectionClosed as e:
            print(f"watchdog cerro el socket a los {time.time()-t0:.1f}s: code={e.rcvd.code if e.rcvd else None} reason={e.rcvd.reason if e.rcvd else None}")
        except asyncio.TimeoutError:
            print("el socket NO fue cerrado")

    show("6. GET /api/v1/sessions (sesion de prueba)", session(sid))

asyncio.run(main())
