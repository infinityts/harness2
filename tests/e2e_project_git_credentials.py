"""E2E credenciales Git por proyecto (token cifrado)."""
import asyncio
import json
import os
import pathlib
import shutil
import sys
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

for _candidate in ("/app", str(pathlib.Path(__file__).resolve().parents[1] / "app")):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from src.indexer import _git_auth_env, _git_credential

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
PROJECT = "stage19-git-token"
REPOS = pathlib.Path("/var/lib/harness/repos")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, timeout=180):
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


async def api(method, path, body=None, bearer=None, timeout=180):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def cleanup():
    db_exec("DELETE FROM harness_projects WHERE project_slug = :slug", slug=PROJECT)
    db_exec("DELETE FROM repo_file_map WHERE repo_name = :slug", slug=PROJECT)
    shutil.rmtree(REPOS / PROJECT, ignore_errors=True)


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


async def main():
    print("== CREDENCIALES GIT POR PROYECTO ==")
    try:
        cleanup()
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]

        print("\n--- 1. Crear proyecto publico ---")
        st, created = await api("POST", "/api/v1/projects", {
            "project_slug": PROJECT,
            "display_name": "Stage19 Git Token",
            "description": "Proyecto para token Git",
            "repo_url": "https://github.com/octocat/Hello-World.git",
            "default_branch": "master",
        }, bearer=admin)
        assert st == 200, created
        project = created["project"]
        assert project["git_auth_type"] == "none"
        assert project["git_token_set"] is False
        print("project:", project["id"], project["git_auth_type"])

        print("\n--- 2. Guardar token cifrado ---")
        dummy = "github_pat_STAGE19_1234"
        st, saved = await api("PUT", f"/api/v1/projects/{project['id']}/git-credential", {
            "git_token": dummy,
        }, bearer=admin)
        assert st == 200, saved
        assert saved["git_auth_type"] == "token" and saved["git_token_hint"] == "1234"
        st, listed = await api("GET", "/api/v1/projects", bearer=admin)
        assert st == 200, listed
        item = next(p for p in listed["projects"] if p["id"] == project["id"])
        assert item["git_auth_type"] == "token"
        assert item["git_token_set"] is True
        assert item["git_token_hint"] == "1234"
        assert "git_token_vault" not in item
        row = db_rows("""
            SELECT git_auth_type, git_token_vault, git_username, git_token_hint
            FROM harness_projects WHERE id = CAST(:pid AS UUID)
        """, pid=project["id"])[0]
        assert row["git_auth_type"] == "token"
        assert row["git_token_vault"] and row["git_token_vault"] != dummy
        assert row["git_username"] == "x-access-token"
        print("token hint:", item["git_token_hint"], "vault cifrado:", len(row["git_token_vault"]))

        print("\n--- 3. Header Git por proyecto ---")
        with _git_credential("secret-token", "https://github.com/infinityts/desktop-organizer"):
            env = _git_auth_env()
            assert env["GIT_CONFIG_COUNT"] == "1"
            assert "github.com" in env["GIT_CONFIG_KEY_0"]
            assert "Authorization: Basic" in env["GIT_CONFIG_VALUE_0"]
            assert "secret-token" not in env["GIT_CONFIG_VALUE_0"]
        print("header:", env["GIT_CONFIG_KEY_0"])

        print("\n--- 4. Quitar token ---")
        st, removed = await api("DELETE", f"/api/v1/projects/{project['id']}/git-credential", bearer=admin)
        assert st == 200, removed
        assert removed["git_auth_type"] == "none" and removed["git_token_set"] is False
        row = db_rows("""
            SELECT git_auth_type, git_token_vault, git_token_hint
            FROM harness_projects WHERE id = CAST(:pid AS UUID)
        """, pid=project["id"])[0]
        assert row["git_auth_type"] == "none" and row["git_token_vault"] is None

        print("\n--- 5. UI ---")
        req = urllib.request.Request(BASE + "/portal")
        with urllib.request.urlopen(req, timeout=30) as resp:
            ui = resp.read().decode()
        for marker in ("pGitToken", "Credencial Git", "git-credential"):
            assert marker.lower() in ui.lower(), marker

        print("\nE2E GIT CREDENTIALS: OK")
    finally:
        try:
            cleanup()
        except Exception as exc:
            print("cleanup:", exc)


if __name__ == "__main__":
    asyncio.run(main())
