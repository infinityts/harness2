import os
import threading
from pathlib import Path

try:
    import fcntl  # Linux
except ImportError:  # Windows local dev
    fcntl = None

REPOS_BASE_DIR = Path(os.getenv("HARNESS_REPOS_DIR", "/var/lib/harness/repos"))

_locks_guard = threading.Lock()
_repo_locks = {}


class _RepoLock:
    """RLock intra-proceso + flock entre procesos sobre <repo>/.harness.lock."""

    def __init__(self, repo_name: str):
        self.repo_name = repo_name
        self._rlock = threading.RLock()
        self._depth = 0
        self._fh = None

    def _ensure_exclude(self) -> None:
        exclude = REPOS_BASE_DIR / self.repo_name / ".git" / "info" / "exclude"
        if not exclude.parent.exists():
            return
        try:
            current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if ".harness.lock" not in current.splitlines():
                with exclude.open("a", encoding="utf-8") as fh:
                    if current and not current.endswith("\n"):
                        fh.write("\n")
                    fh.write(".harness.lock\n")
        except Exception:
            pass

    def __enter__(self):
        self._rlock.acquire()
        if self._depth == 0:
            lock_path = REPOS_BASE_DIR / self.repo_name / ".harness.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(lock_path, "a+")
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            self._ensure_exclude()
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if self._depth == 0:
            if self._fh is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                finally:
                    self._fh.close()
                    self._fh = None
        self._rlock.release()
        return False


def repo_lock(repo_name: str) -> _RepoLock:
    with _locks_guard:
        lock = _repo_locks.get(repo_name)
        if lock is None:
            lock = _RepoLock(repo_name)
            _repo_locks[repo_name] = lock
    return lock
