import os
import re
import ast
import math
import json
import hashlib
import ipaddress
import socket
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import urllib.request

import sqlalchemy as sa

from src.repo_lock import repo_lock

REPOS_BASE_DIR = Path(os.getenv("HARNESS_REPOS_DIR", "/var/lib/harness/repos"))
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
ALLOWED_GIT_HOSTS = {
    h.strip().lower()
    for h in os.getenv("ALLOWED_GIT_HOSTS", "github.com,gitlab.com,bitbucket.org").split(",")
    if h.strip()
}

IGNORED_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target", ".idea", ".vscode"}
SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".sql", ".md"}

# El API no tiene autenticacion todavia: repo_name se usa como ruta de disco y repo_url
# lo consume `git clone`, asi que ambos se validan antes de tocar el filesystem.
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REPO_URL_RE = re.compile(r"^https?://[^\s]+$")


def validate_repo_name(repo_name: str) -> str:
    if not REPO_NAME_RE.fullmatch(repo_name or ""):
        raise ValueError("repo_name invalido: solo letras, numeros, punto, guion y guion_bajo")
    return repo_name


def _is_public_ip(ip_value: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_value.split("%", 1)[0])
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )


def validate_repo_url(repo_url: Optional[str]) -> Optional[str]:
    if not repo_url:
        return None
    if not REPO_URL_RE.fullmatch(repo_url):
        raise ValueError("repo_url invalida: solo se permiten URLs http(s)")
    parsed = urlparse(repo_url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("repo_url sin host")
    # Los hosts del allowlist se permiten sin resolver (github/gitlab/bitbucket).
    if host in ALLOWED_GIT_HOSTS:
        return repo_url
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("repo_url apunta a un host interno no permitido")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"repo_url no resuelve: {host}") from exc
    ips = {info[4][0] for info in infos}
    if not ips or any(not _is_public_ip(ip) for ip in ips):
        raise ValueError("repo_url apunta a una IP privada/loopback/link-local y no esta en ALLOWED_GIT_HOSTS")
    return repo_url


def _run_git(args: List[str], cwd: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("git no esta instalado en el contenedor") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[:300]
        raise RuntimeError(f"git fallo ({' '.join(args[:2])}): {detail}") from exc


def compute_embedding_768(text: str) -> List[float]:
    """
    Genera un vector de 768 dimensiones.
    Si EMBEDDING_API_URL esta configurado, usa el modelo externo.
    Si no, usa Feature Hashing determinista (L2 normalized) sobre tokens e identificadores AST
    para mantener consumo de RAM cercano a 0 MB dentro de los 4GB del contenedor.
    """
    if EMBEDDING_API_URL:
        try:
            payload = json.dumps({"model": EMBEDDING_MODEL, "input": text}).encode("utf-8")
            req = urllib.request.Request(
                EMBEDDING_API_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                vec = data["data"][0]["embedding"]
                if len(vec) == 768:
                    return vec
        except Exception:
            pass

    # Fallback determinista de alta velocidad: Signed Feature Hashing de 768 dims (Unigramas + Bigramas de codigo)
    vec = [0.0] * 768
    raw_tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[0-9]+", text.lower())
    if not raw_tokens:
        raw_tokens = ["empty_node"]

    features = list(raw_tokens)
    for i in range(len(raw_tokens) - 1):
        features.append(f"{raw_tokens[i]}_{raw_tokens[i + 1]}")

    for feat in features:
        h = hashlib.sha256(feat.encode("utf-8")).digest()
        idx = int.from_bytes(h[:2], "big") % 768
        sign = 1.0 if (h[2] % 2 == 0) else -1.0
        weight = 1.5 if "_" in feat else 1.0
        vec[idx] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [round(v / norm, 6) for v in vec]
    else:
        vec[0] = 1.0
    return vec


def parse_python_ast(source: str) -> Dict[str, Any]:
    """Extrae imports y simbolos exactos (funciones, clases, metodos) usando el AST nativo de Python."""
    imports = []
    symbols = []
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}" if mod else alias.name)

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                sig = f"{prefix} {node.name}({', '.join(args)})"
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l)
                snippet = "\n".join(lines[start_l - 1:end_l])
                symbols.append({
                    "name": node.name,
                    "type": "function",
                    "signature": sig,
                    "start_line": start_l,
                    "end_line": end_l,
                    "snippet": snippet[:4000]
                })
            elif isinstance(node, ast.ClassDef):
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l)
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                sig = f"class {node.name} [methods: {', '.join(methods)}]"
                snippet = "\n".join(lines[start_l - 1:end_l])
                symbols.append({
                    "name": node.name,
                    "type": "class",
                    "signature": sig,
                    "start_line": start_l,
                    "end_line": end_l,
                    "snippet": snippet[:4000]
                })
                # Metodos: se indexan como simbolos propios para que el guardrail anti-alucinacion
                # pueda validar llamadas tipo `Clase.metodo` o `metodo` con firma y lineas exactas.
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        margs = [a.arg for a in m.args.args]
                        mprefix = "async def" if isinstance(m, ast.AsyncFunctionDef) else "def"
                        msig = f"{mprefix} {node.name}.{m.name}({', '.join(margs)})"
                        mstart = m.lineno
                        mend = getattr(m, "end_lineno", mstart)
                        msnip = "\n".join(lines[mstart - 1:mend])
                        symbols.append({
                            "name": m.name,
                            "qualified_name": f"{node.name}.{m.name}",
                            "type": "method",
                            "signature": msig,
                            "start_line": mstart,
                            "end_line": mend,
                            "snippet": msnip[:4000]
                        })
    except SyntaxError:
        pass
    return {"imports": sorted(list(set(imports))), "symbols": symbols}


def parse_generic_code(source: str, ext: str) -> Dict[str, Any]:
    """Extractor estructural para TS/JS/Rust/Go/SQL."""
    imports = []
    symbols = []
    lines = source.splitlines()

    import_re = re.compile(r"^\s*(?:import\s+.*from\s+['\"]([^'\"]+)['\"]|use\s+([^;]+);)", re.MULTILINE)
    for m in import_re.finditer(source):
        imp = m.group(1) or m.group(2)
        if imp:
            imports.append(imp.strip())

    patterns = [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|fn|func)\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)")),
        ("class_or_struct", re.compile(r"^\s*(?:export\s+)?(?:class|struct|interface|type)\s+([a-zA-Z0-9_]+)")),
        ("const_fn", re.compile(r"^\s*(?:export\s+)?const\s+([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"))
    ]

    for idx, line in enumerate(lines):
        for sym_type, pat in patterns:
            m = pat.match(line)
            if m:
                name = m.group(1)
                start_l = idx + 1
                end_l = min(len(lines), start_l + 35)
                snippet = "\n".join(lines[start_l - 1:end_l])
                symbols.append({
                    "name": name,
                    "type": sym_type,
                    "signature": line.strip()[:200],
                    "start_line": start_l,
                    "end_line": end_l,
                    "snippet": snippet[:4000]
                })
    return {"imports": sorted(list(set(imports))), "symbols": symbols}


def ensure_project_repo(repo_name: str, repo_url: Optional[str] = None, branch: str = "main") -> Path:
    """Crea/clona el repo del proyecto y garantiza que exista un HEAD (README inicial)."""
    with repo_lock(repo_name):
        return _ensure_project_repo_locked(repo_name, repo_url, branch)


def _ensure_project_repo_locked(repo_name: str, repo_url: Optional[str] = None, branch: str = "main") -> Path:
    repo_name = validate_repo_name(repo_name)
    repo_url = validate_repo_url(repo_url)

    REPOS_BASE_DIR.mkdir(parents=True, exist_ok=True)
    base = REPOS_BASE_DIR.resolve()
    repo_path = (base / repo_name).resolve()
    if repo_path.parent != base:
        raise ValueError("repo_name fuera del directorio permitido")

    if repo_url:
        if not repo_path.exists():
            _run_git(["git", "clone", "-b", branch, repo_url, str(repo_path)])
        else:
            _run_git(["git", "fetch", "--all"], cwd=str(repo_path), check=False)
            _run_git(["git", "checkout", branch], cwd=str(repo_path), check=False)
    else:
        if not repo_path.exists():
            repo_path.mkdir(parents=True, exist_ok=True)
        if not (repo_path / ".git").exists():
            _run_git(["git", "init", "-b", branch], cwd=str(repo_path))
        else:
            _run_git(["git", "checkout", branch], cwd=str(repo_path), check=False)

    head = _run_git(["git", "rev-parse", "--verify", "HEAD"], cwd=str(repo_path), check=False)
    if head.returncode != 0:
        readme = repo_path / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {repo_name}\n\nProyecto gestionado por Harness Control Plane.\n",
                encoding="utf-8",
            )
        _run_git(["git", "add", "README.md"], cwd=str(repo_path))
        _run_git(
            ["git", "-c", "user.name=Harness Control Plane", "-c", "user.email=harness@controlplane.local",
             "commit", "-m", "initial commit"],
            cwd=str(repo_path),
        )
    return repo_path


def ensure_git_repo(repo_name: str, repo_url: Optional[str], branch: str) -> Path:
    repo_name = validate_repo_name(repo_name)
    repo_url = validate_repo_url(repo_url)

    REPOS_BASE_DIR.mkdir(parents=True, exist_ok=True)
    base = REPOS_BASE_DIR.resolve()
    repo_path = (base / repo_name).resolve()
    if repo_path.parent != base:
        raise ValueError("repo_name fuera del directorio permitido")

    if not repo_path.exists():
        if repo_url:
            _run_git(["git", "clone", "-b", branch, repo_url, str(repo_path)])
        else:
            # Inicializar repo local si no se paso URL para pruebas o trabajo directo
            repo_path.mkdir(parents=True, exist_ok=True)
            _run_git(["git", "init", "-b", branch], cwd=str(repo_path))
    else:
        if repo_url:
            _run_git(["git", "fetch", "--all"], cwd=str(repo_path), check=False)
            _run_git(["git", "checkout", branch], cwd=str(repo_path), check=False)

    return repo_path


def get_head_commit_sha(repo_path: Path) -> str:
    try:
        res = _run_git(["git", "rev-parse", "HEAD"], cwd=str(repo_path))
        return res.stdout.strip()
    except Exception:
        return "0" * 40


def index_repository(engine: sa.Engine, repo_name: str, repo_url: Optional[str] = None, branch: str = "main") -> Dict[str, Any]:
    # Lock distribuido: flock entre procesos + RLock intra-proceso.
    with repo_lock(repo_name):
        return _index_repository_locked(engine, repo_name, repo_url, branch)


def _index_repository_locked(engine: sa.Engine, repo_name: str, repo_url: Optional[str] = None, branch: str = "main") -> Dict[str, Any]:
    repo_path = ensure_git_repo(repo_name, repo_url, branch)
    commit_sha = get_head_commit_sha(repo_path)

    files_indexed = 0
    vectors_created = 0
    seen_files = set()

    with engine.begin() as conn:
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:rname))"), {"rname": repo_name})
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                full_path = Path(root) / fname
                rel_path = str(full_path.relative_to(repo_path)).replace("\\", "/")

                try:
                    source = full_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                seen_files.add(rel_path)

                parsed = parse_python_ast(source) if ext == ".py" else parse_generic_code(source, ext)
                symbols = parsed["symbols"]
                imports = parsed["imports"]

                exported_meta = [
                    {"name": s["name"], "qualified_name": s.get("qualified_name"), "type": s["type"], "signature": s["signature"], "lines": [s["start_line"], s["end_line"]]}
                    for s in symbols
                ]
                sym_names = ", ".join(s["name"] for s in symbols[:12]) if symbols else "module-level script"
                quick_summary = f"[{ext[1:].upper()}] {rel_path} ({len(source.splitlines())} lines) | Defines: {sym_names}"

                # Upsert en repo_file_map respetando locked_by_task_id si ya existia
                row = conn.execute(
                    sa.text("""
                        INSERT INTO repo_file_map (
                            repo_name, file_path, commit_sha, quick_summary, exported_symbols, imports
                        ) VALUES (
                            :rname, :fpath, :sha, :summary, CAST(:syms AS JSONB), CAST(:imps AS JSONB)
                        )
                        ON CONFLICT (repo_name, file_path) DO UPDATE SET
                            commit_sha = EXCLUDED.commit_sha,
                            quick_summary = EXCLUDED.quick_summary,
                            exported_symbols = EXCLUDED.exported_symbols,
                            imports = EXCLUDED.imports
                        RETURNING id
                    """),
                    {
                        "rname": repo_name,
                        "fpath": rel_path,
                        "sha": commit_sha,
                        "summary": quick_summary,
                        "syms": json.dumps(exported_meta),
                        "imps": json.dumps(imports)
                    }
                ).fetchone()

                file_id = row[0]
                files_indexed += 1

                # Reemplazar vectores AST de este archivo
                conn.execute(sa.text("DELETE FROM code_ast_vectors WHERE file_id = :fid"), {"fid": file_id})

                # Si el archivo no tiene funciones declaradas, crear un chunk del archivo completo
                chunks = symbols if symbols else [{
                    "name": Path(fname).stem,
                    "signature": f"file {rel_path}",
                    "start_line": 1,
                    "end_line": len(source.splitlines()),
                    "snippet": source[:3000]
                }]

                for ch in chunks:
                    embed_input = f"{rel_path}\n{ch['signature']}\n{ch['snippet']}"
                    vec = compute_embedding_768(embed_input)
                    vec_str = "[" + ",".join(str(x) for x in vec) + "]"

                    conn.execute(
                        sa.text("""
                            INSERT INTO code_ast_vectors (
                                file_id, symbol_name, symbol_signature, start_line, end_line, code_snippet, embedding
                            ) VALUES (
                                :fid, :sname, :ssig, :sline, :eline, :snip, CAST(:emb AS vector)
                            )
                        """),
                        {
                            "fid": file_id,
                            "sname": ch["name"],
                            "ssig": ch["signature"],
                            "sline": ch["start_line"],
                            "eline": ch["end_line"],
                            "snip": ch["snippet"],
                            "emb": vec_str
                        }
                    )
                    vectors_created += 1

        # Reconciliacion: eliminar filas de archivos que ya no existen en el arbol actual.
        # code_ast_vectors tiene ON DELETE CASCADE, asi que sus embeddings se limpian solos.
        if seen_files:
            conn.execute(
                sa.text("""
                    DELETE FROM repo_file_map
                    WHERE repo_name = :rname
                      AND NOT (file_path = ANY(:seen))
                """),
                {"rname": repo_name, "seen": list(seen_files)}
            )
        else:
            conn.execute(sa.text("DELETE FROM repo_file_map WHERE repo_name = :rname"), {"rname": repo_name})

    return {
        "repo_name": repo_name,
        "commit_sha": commit_sha,
        "files_indexed": files_indexed,
        "ast_vectors_created": vectors_created
    }
