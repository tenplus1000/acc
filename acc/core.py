"""Core: config, enabled modules, and context building."""

from pathlib import Path

from acc import defaults, store
from acc.modules.goals import GoalsModule
from acc.modules.rapport import RapportModule

MODULE_ORDER = ["rapport", "goals"]


def get_root() -> Path:
    root = store.get_root()
    store.ensure_seeded(root)
    return root


def load_config(root: Path) -> dict:
    cfg = store.read_json(root / "config.json", None)
    if cfg is None:
        cfg = dict(defaults.DEFAULT_CONFIG)
        store.atomic_write_json(root / "config.json", cfg)
    return cfg


def save_config(root: Path, cfg: dict) -> None:
    store.atomic_write_json(root / "config.json", cfg)


def enabled_modules(cfg: dict):
    mods = cfg.get("modules", {})
    return [name for name in MODULE_ORDER if mods.get(name, True)]


def build_context(session_id: str, cwd: str, agent: str = "", record: bool = False) -> str:
    root = get_root()
    cfg = load_config(root)
    enabled = enabled_modules(cfg)

    # Per-session rapport override lives on the session file, which exists
    # independently of whether the "goals" module is enabled (the hook
    # always touches it), so it is loaded directly here.
    session = None
    if session_id:
        session = GoalsModule(root).load_session(session_id)

    blocks = []
    rapport = RapportModule(root)
    rapport_enabled, _rapport_enabled_source = rapport.effective_enabled(session)
    if rapport_enabled:
        override = session.get("rapport") if session else None
        block = rapport.build_context(override)
        if block:
            blocks.append(block)
    if "goals" in enabled:
        goals = GoalsModule(root)
        block = goals.build_context(session_id, cwd, record=record)
        if block:
            blocks.append(block)

    if not blocks:
        return ""

    wrapper = store.read_text(root / "templates" / "wrapper.md", defaults.WRAPPER_MD)
    modules_text = "\n\n".join(blocks)
    return (wrapper.replace("{session_id}", session_id or "")
            .replace("{modules}", modules_text).strip("\n") + "\n")
