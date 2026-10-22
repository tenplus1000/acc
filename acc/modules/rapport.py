"""Rapport module: prompt dial (preset + level)."""

from pathlib import Path

from acc import defaults, events, store


class RapportError(Exception):
    pass


class RapportModule:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "rapport"
        self.presets_dir = self.dir / "presets"

    # -- low level -----------------------------------------------------

    def _state_path(self) -> Path:
        return self.dir / "state.json"

    def load_state(self) -> dict:
        state = store.read_json(self._state_path(), None)
        if state is None:
            state = dict(defaults.DEFAULT_RAPPORT_STATE)
            self.save_state(state)
        return state

    def save_state(self, state: dict) -> None:
        store.atomic_write_json(self._state_path(), state)

    def _preset_ids(self):
        if not self.presets_dir.exists():
            return []
        return sorted(
            p.name for p in self.presets_dir.iterdir() if p.is_dir()
        )

    def _preset_dir(self, preset_id: str) -> Path:
        return self.presets_dir / preset_id

    def _preset_config_path(self, preset_id: str) -> Path:
        return self._preset_dir(preset_id) / "config.json"

    def load_preset_config(self, preset_id: str) -> dict:
        cfg = store.read_json(self._preset_config_path(preset_id), None)
        if cfg is None:
            raise RapportError(f"preset not found: {preset_id}")
        return cfg

    def save_preset_config(self, preset_id: str, cfg: dict) -> None:
        store.atomic_write_json(self._preset_config_path(preset_id), cfg)

    def _level_prompt_path(self, preset_id: str, n: int) -> Path:
        return self._preset_dir(preset_id) / f"{n}.md"

    def get_level_prompt(self, preset_id: str, n: int) -> str:
        return store.read_text(self._level_prompt_path(preset_id, n), "")

    def set_level_prompt_file(self, preset_id: str, n: int, prompt: str) -> None:
        store.atomic_write_text(self._level_prompt_path(preset_id, n), prompt)
        events.mark_seen(self.root, f"rapport/presets/{preset_id}/{n}.md")

    def _config_path(self) -> Path:
        return self.root / "config.json"

    def globally_enabled(self) -> bool:
        """The config.json `modules.rapport` switch -- the default for any
        session that hasn't set its own `rapport_enabled` override."""
        cfg = store.read_json(self._config_path(), None) or {}
        return cfg.get("modules", {}).get("rapport", True)

    def set_globally_enabled(self, enabled: bool, actor: str = "user") -> None:
        cfg = store.read_json(self._config_path(), None) or {}
        mods = cfg.setdefault("modules", {})
        before = mods.get("rapport", True)
        after = bool(enabled)
        mods["rapport"] = after
        store.atomic_write_json(self._config_path(), cfg)
        events.log(self.root, "rapport.enabled_changed", actor, before=before, after=after)

    def effective_enabled(self, session: dict = None):
        """Whether Rapport injection is on for this session, and why.

        A session's own `rapport_enabled` (True/False), if present,
        overrides the global config.json switch; absent (or no session)
        falls back to the global switch. Returns (enabled: bool,
        source: "session"|"global").
        """
        session_val = session.get("rapport_enabled") if session else None
        if session_val is not None:
            return bool(session_val), "session"
        return self.globally_enabled(), "global"

    def _resolve_active_preset_id(self, state: dict) -> str:
        ids = self._preset_ids()
        if not ids:
            raise RapportError("no presets exist")
        active = state.get("activePreset")
        if active in ids:
            return active
        return ids[0]

    # -- public read -----------------------------------------------------

    def list_presets(self):
        result = []
        for pid in self._preset_ids():
            cfg = self.load_preset_config(pid)
            levels = []
            for i, lvl in enumerate(cfg.get("levels", []), start=1):
                levels.append(
                    {
                        "n": i,
                        "name": lvl.get("name", f"Level {i}"),
                        "prompt": self.get_level_prompt(pid, i),
                    }
                )
            result.append({"id": pid, "name": cfg.get("name", pid), "levels": levels})
        return result

    def preset_exists(self, preset_id: str) -> bool:
        return preset_id in self._preset_ids()

    def get_active(self):
        """The default (session-independent) rapport setting."""
        return self.get_effective(None)

    def get_effective(self, override: dict = None):
        """Resolve the effective preset/level for a session.

        `override` is a session's optional {"preset": id, "level": n} field.
        If it names a preset that no longer exists, fall back to the
        default preset; the level is always clamped to that preset's
        level count (this is what keeps overrides valid after a preset is
        deleted or shrunk, without rewriting session files).
        """
        state = self.load_state()
        default_preset_id = self._resolve_active_preset_id(state)
        ids = self._preset_ids()
        if override and override.get("preset") in ids:
            preset_id = override["preset"]
            level = override.get("level", 1)
        else:
            preset_id = default_preset_id
            level = state.get("level", 1)
        cfg = self.load_preset_config(preset_id)
        levels = cfg.get("levels", [])
        count = len(levels)
        level = max(1, min(count, level)) if count else 1
        return {
            "presetId": preset_id,
            "presetName": cfg.get("name", preset_id),
            "level": level,
            "levelCount": count,
            "levelName": levels[level - 1].get("name", "") if count else "",
        }

    # -- ops --------------------------------------------------------------

    def select_preset(self, preset_id: str, actor: str = "user") -> None:
        if preset_id not in self._preset_ids():
            raise RapportError(f"preset not found: {preset_id}")
        state = self.load_state()
        before = {"preset": state.get("activePreset"), "level": state.get("level")}
        state["activePreset"] = preset_id
        cfg = self.load_preset_config(preset_id)
        count = len(cfg.get("levels", []))
        state["level"] = max(1, min(count, state.get("level", 1))) if count else 1
        self.save_state(state)
        after = {"preset": state.get("activePreset"), "level": state.get("level")}
        events.log(self.root, "rapport.default_changed", actor, before=before, after=after)

    def set_level(self, level: int, actor: str = "user") -> None:
        state = self.load_state()
        preset_id = self._resolve_active_preset_id(state)
        before = {"preset": state.get("activePreset"), "level": state.get("level")}
        cfg = self.load_preset_config(preset_id)
        count = len(cfg.get("levels", []))
        level = max(1, min(count, int(level))) if count else 1
        state["activePreset"] = preset_id
        state["level"] = level
        self.save_state(state)
        after = {"preset": state.get("activePreset"), "level": state.get("level")}
        events.log(self.root, "rapport.default_changed", actor, before=before, after=after)

    def create_preset(self, name: str, actor: str = "user") -> str:
        name = name.strip()
        if not name:
            raise RapportError("preset name required")
        preset_id = store.unique_slug(name, self._preset_ids())
        pdir = self._preset_dir(preset_id)
        pdir.mkdir(parents=True, exist_ok=True)
        levels = [{"name": n} for n, _ in defaults.DEFAULT_CODING_LEVELS]
        self.save_preset_config(preset_id, {"name": name, "levels": levels})
        for i, (_, prompt) in enumerate(defaults.DEFAULT_CODING_LEVELS, start=1):
            self.set_level_prompt_file(preset_id, i, prompt)
        events.log(self.root, "rapport.preset_created", actor, preset=preset_id, name=name)
        return preset_id

    def rename_preset(self, preset_id: str, name: str, actor: str = "user") -> None:
        name = name.strip()
        if not name:
            raise RapportError("preset name required")
        cfg = self.load_preset_config(preset_id)
        before = cfg.get("name", preset_id)
        cfg["name"] = name
        self.save_preset_config(preset_id, cfg)
        events.log(self.root, "rapport.preset_renamed", actor, preset=preset_id, before=before, after=name)

    def delete_preset(self, preset_id: str, actor: str = "user") -> None:
        ids = self._preset_ids()
        if preset_id not in ids:
            raise RapportError(f"preset not found: {preset_id}")
        if len(ids) <= 1:
            raise RapportError("不能刪除最後一個 Preset")
        import shutil

        name = self.load_preset_config(preset_id).get("name", preset_id)
        shutil.rmtree(self._preset_dir(preset_id))
        state = self.load_state()
        if state.get("activePreset") == preset_id:
            remaining = self._preset_ids()
            new_active = remaining[0]
            cfg = self.load_preset_config(new_active)
            count = len(cfg.get("levels", []))
            state["activePreset"] = new_active
            state["level"] = max(1, min(count, state.get("level", 1))) if count else 1
            self.save_state(state)
        events.log(self.root, "rapport.preset_deleted", actor, preset=preset_id, name=name)

    def add_level(self, preset_id: str, actor: str = "user") -> None:
        cfg = self.load_preset_config(preset_id)
        levels = cfg.get("levels", [])
        n = len(levels) + 1
        levels.append({"name": f"Level {n}"})
        cfg["levels"] = levels
        self.save_preset_config(preset_id, cfg)
        self.set_level_prompt_file(preset_id, n, "")
        events.log(self.root, "rapport.level_added", actor, preset=preset_id, n=n)

    def remove_last_level(self, preset_id: str, actor: str = "user") -> None:
        cfg = self.load_preset_config(preset_id)
        levels = cfg.get("levels", [])
        count = len(levels)
        if count <= 1:
            raise RapportError("至少要保留 1 個 Level")
        levels.pop()
        cfg["levels"] = levels
        self.save_preset_config(preset_id, cfg)
        path = self._level_prompt_path(preset_id, count)
        if path.exists():
            path.unlink()

        state = self.load_state()
        if state.get("activePreset") == preset_id:
            new_count = count - 1
            state["level"] = max(1, min(new_count, state.get("level", 1)))
            self.save_state(state)
        events.log(self.root, "rapport.level_removed", actor, preset=preset_id, n=count)

    def rename_level(self, preset_id: str, n: int, name: str, actor: str = "user") -> None:
        name = name.strip()
        if not name:
            raise RapportError("level name required")
        cfg = self.load_preset_config(preset_id)
        levels = cfg.get("levels", [])
        if not (1 <= n <= len(levels)):
            raise RapportError(f"level not found: {n}")
        before = levels[n - 1].get("name", "")
        levels[n - 1]["name"] = name
        cfg["levels"] = levels
        self.save_preset_config(preset_id, cfg)
        events.log(self.root, "rapport.level_renamed", actor, preset=preset_id, n=n, before=before, after=name)

    def set_level_prompt(self, preset_id: str, n: int, prompt: str, actor: str = "user") -> None:
        cfg = self.load_preset_config(preset_id)
        levels = cfg.get("levels", [])
        if not (1 <= n <= len(levels)):
            raise RapportError(f"level not found: {n}")
        before = self.get_level_prompt(preset_id, n)
        self.set_level_prompt_file(preset_id, n, prompt)
        events.log(self.root, "rapport.level_prompt_changed", actor, preset=preset_id, n=n, before=before, after=prompt)

    # -- context ------------------------------------------------------

    def build_context(self, override: dict = None) -> str:
        active = self.get_effective(override)
        if active["levelCount"] == 0:
            return ""
        template = store.read_text(
            self.root / "templates" / "rapport.md", defaults.RAPPORT_MD
        )
        prompt = self.get_level_prompt(active["presetId"], active["level"])
        text = template
        text = text.replace("{preset}", active["presetName"])
        text = text.replace("{level_count}", str(active["levelCount"]))
        text = text.replace("{level_name}", active["levelName"])
        text = text.replace("{level}", str(active["level"]))
        text = text.replace("{prompt}", prompt)
        return text.strip("\n")
