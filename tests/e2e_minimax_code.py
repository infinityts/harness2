"""E2E integracion MiniMax Code: sesion, perfil y MCP bundle."""
import asyncio
import json
import os
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
EMAIL = "minimax-e2e@example.com"
PASSWORD = "MiniMaxE2E2026!"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw


async def api(method, path, body=None, bearer=None, timeout=60):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def http_text(path, timeout=60):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


async def api_text(path, timeout=60):
    return await asyncio.to_thread(http_text, path, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def cleanup():
    user = db_rows("SELECT id::text FROM harness_users WHERE email = :email", email=EMAIL)
    if user:
        uid = user[0]["id"]
        db_exec("DELETE FROM agent_sessions WHERE user_id = CAST(:uid AS UUID)", uid=uid)
        db_exec("DELETE FROM project_memberships WHERE user_id = CAST(:uid AS UUID)", uid=uid)
        db_exec("DELETE FROM harness_users WHERE id = CAST(:uid AS UUID)", uid=uid)


async def main():
    print("== MINIMAX CODE - INTEGRACION ==")
    admin = None
    try:
        cleanup()
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]

        print("\n--- 1. Perfil MiniMax ---")
        st, profiles = await api("GET", "/api/v1/model-profiles", bearer=admin)
        assert st == 200, profiles
        minimax = next((p for p in profiles["profiles"] if p["name"] == "minimax-m3.1-flash"), None)
        assert minimax, profiles
        assert minimax["context_window_tokens"] == 1000000
        assert minimax["max_input_tokens"] == 128000
        assert minimax["max_output_tokens"] == 32000
        print("perfil:", minimax["name"], minimax["context_window_tokens"], minimax["max_input_tokens"])

        print("\n--- 2. Usuario y proyecto ---")
        st, user = await api("POST", "/api/v1/admin/users", {
            "email": EMAIL, "full_name": "MiniMax E2E", "password": PASSWORD,
        }, bearer=admin)
        assert st == 200, user
        st, projects = await api("GET", "/api/v1/projects", bearer=admin)
        project = next(p for p in projects["projects"] if p["project_slug"] == "harness-self")
        st, member = await api("POST", f"/api/v1/projects/{project['id']}/members", {
            "user_id": user["user"]["id"], "allowed_skills": ["python"],
        }, bearer=admin)
        assert st == 200, member
        st, eng_login = await api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
        assert st == 200, eng_login
        engineer = eng_login["access_token"]

        print("\n--- 3. Sesion MiniMax Code ---")
        st, session = await api("POST", "/api/v1/portal/sessions", {
            "project_id": project["id"],
            "agent_tool": "minimaxcode",
            "llm_provider": "minimax",
            "max_context_tokens": 32000,
            "model_profile": "minimax-m3.1-flash",
        }, bearer=engineer)
        assert st == 200, session
        assert session["agent_tool"] == "minimaxcode"
        assert session["model_profile"] == "minimax-m3.1-flash"
        assert session["max_context_tokens"] == 1000000
        print("session:", session["session_id"], session["max_context_tokens"])

        print("\n--- 4. Config bundle MCP ---")
        st, bundle = await api("GET", f"/api/v1/portal/sessions/{session['session_id']}/config-bundle", bearer=engineer)
        assert st == 200, bundle
        mcp = bundle.get("minimaxcode_mcp_json") or ""
        setup = bundle.get("minimaxcode_setup") or ""
        assert "harness_mcp_bridge.py" in mcp
        assert "HARNESS_SESSION_ID" in mcp
        assert "mcpServers" in mcp
        assert "mcode" in setup and "@minimax-ai/code" in setup
        assert bundle["env"]["HARNESS_SESSION_ID"] == session["session_id"]
        print("bundle keys:", sorted(k for k in bundle if "minimax" in k))

        print("\n--- 5. UI ---")
        st, ui = await api_text("/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("MiniMax Code", "minimaxcode_mcp_json", "minimax-m3.1-flash"):
            assert marker.lower() in ui.lower(), f"UI sin marcador: {marker}"

        print("\nE2E MINIMAX CODE: OK")
    finally:
        try:
            cleanup()
        except Exception as exc:
            print("cleanup:", exc)


if __name__ == "__main__":
    asyncio.run(main())
