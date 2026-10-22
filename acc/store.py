"""Data root resolution, atomic IO helpers, and first-run seeding."""

import json
import os
import re
import tempfile
from pathlib import Path

from acc import defaults

ROOT_DIRNAME = ".agent-context"
ROOT_CACHE = Path.home() / ".config" / "acc" / "root"


def _windows_home_from_wsl():
    """On WSL, the Windows user profile as a WSL path (e.g. /mnt/c/Users/<name>)."""
    try:
        if "microsoft" not in Path("/proc/version").read_text().lower():
            return None
        import subprocess
        win = subprocess.run(["cmd.exe", "/c", "echo %USERPROFILE%"], capture_output=True,
                             text=True, timeout=5, cwd="/mnt/c").stdout.strip()
        if not win or "%" in win:
            return None
        path = subprocess.run(["wslpath", "-u", win], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        return Path(path) if path else None
    except (OSError, ValueError, Exception):
        return None


def default_root() -> Path:
    """Windows profile on WSL (so the Windows browser and WSL agents share it),
    otherwise the Linux home. The WSL lookup is cached in ~/.config/acc/root
    because it spawns cmd.exe, which is too slow for every hook call."""
    try:
        cached = ROOT_CACHE.read_text(encoding="utf-8").strip()
        if cached:
            return Path(cached)
    except OSError:
        pass
    win_home = _windows_home_from_wsl()
    root = (win_home or Path.home()) / ROOT_DIRNAME
    try:
        ROOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ROOT_CACHE.write_text(str(root) + "\n", encoding="utf-8")
    except OSError:
        pass
    return root


def get_root() -> Path:
    root = os.environ.get("ACC_HOME")
    return Path(root) if root else default_root()


# ---------------------------------------------------------------------------
# Atomic IO
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_text(path: Path, default: str = "") -> str:
    path = Path(path)
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return default
    return json.loads(text)


def seed_file(path: Path, text: str) -> None:
    path = Path(path)
    if not path.exists():
        atomic_write_text(path, text)


def seed_json(path: Path, obj) -> None:
    path = Path(path)
    if not path.exists():
        atomic_write_json(path, obj)


# ---------------------------------------------------------------------------
# Name / id sanitizing
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    if not slug:
        slug = "preset"
    return slug


def unique_slug(name: str, existing) -> str:
    base = slugify(name)
    slug = base
    n = 2
    existing = set(existing)
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def sanitize_session_id(session_id: str) -> str:
    if not session_id or not SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session id: {session_id!r}")
    return session_id


def safe_filename_component(text: str) -> str:
    """Replace filesystem-unsafe characters, keep it readable."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def project_key(path: str) -> str:
    key = path.lstrip("/")
    key = key.replace("/", "__")
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return key


def find_project_root(cwd: str) -> str:
    """Walk up from cwd looking for a .git dir/file; else return cwd."""
    p = Path(cwd).resolve()
    cur = p
    while True:
        if (cur / ".git").exists():
            return str(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return str(p)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def ensure_seeded(root: Path) -> None:
    root = Path(root)
    (root / "templates").mkdir(parents=True, exist_ok=True)
    (root / "rapport" / "presets").mkdir(parents=True, exist_ok=True)
    (root / "goals" / "projects").mkdir(parents=True, exist_ok=True)
    (root / "goals" / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    seed_json(root / "config.json", defaults.DEFAULT_CONFIG)

    seed_file(root / "templates" / "wrapper.md", defaults.WRAPPER_MD)
    seed_file(root / "templates" / "rapport.md", defaults.RAPPORT_MD)
    seed_file(root / "templates" / "goals.md", defaults.GOALS_MD)
    seed_file(root / "templates" / "rules.md", defaults.RULES_MD)
    seed_file(root / "templates" / "super_pending.md", defaults.SUPER_PENDING_MD)
    seed_file(root / "templates" / "ultimate_updated.md", defaults.ULTIMATE_UPDATED_MD)
    seed_file(
        root / "templates" / "objective_active.md", defaults.OBJECTIVE_ACTIVE_MD
    )
    seed_file(
        root / "templates" / "objective_unset.md", defaults.OBJECTIVE_UNSET_MD
    )

    seed_json(root / "rapport" / "state.json", dict(defaults.DEFAULT_RAPPORT_STATE))

    coding_dir = root / "rapport" / "presets" / "coding"
    if not coding_dir.exists():
        coding_dir.mkdir(parents=True, exist_ok=True)
        levels = [{"name": name} for name, _ in defaults.DEFAULT_CODING_LEVELS]
        seed_json(coding_dir / "config.json", {"name": "Coding", "levels": levels})
        for i, (_, prompt) in enumerate(defaults.DEFAULT_CODING_LEVELS, start=1):
            seed_file(coding_dir / f"{i}.md", prompt)

    seed_file(root / "goals" / "ultimate.md", defaults.ULTIMATE_MD)
