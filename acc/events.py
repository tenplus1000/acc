"""Event log: an append-only record of state-changing operations.

Record only -- there is no stats UI yet. Every line in <root>/events.jsonl is
one JSON object: {"at": iso seconds, "type": ..., "actor": "user"|"agent"|
"hook", ...fields}.

Logging must never raise into the caller. Any failure here is swallowed and,
on a best-effort basis, written to <root>/logs/hook.log instead.
"""

import datetime
import hashlib
import json
import time
from pathlib import Path

from acc import store

EVENTS_FILENAME = "events.jsonl"
SNAPSHOT_FILENAME = ".snapshots.json"

# Files whose content (not just hash) we keep in the snapshot, so an
# external_edit event for them can carry a real before/after diff.
_TEXT_TRACKED_PREFIXES = ("goals/ultimate.md", "goals/projects/")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _fallback_log(root, message: str) -> None:
    try:
        root = Path(root)
        ts = _now_iso()
        with open(root / "logs" / "hook.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def log_error(root, message: str) -> None:
    """Public best-effort error logger for callers outside this module that
    need to record a failure without ever raising (e.g. GoalsModule's
    auto-reinstall-codex-on-save). Writes to <root>/logs/hook.log."""
    _fallback_log(root, message)


_CACHE = {"key": None, "events": [], "checked_at": 0.0}
_CACHE_TTL = 1.0


def log(root, type: str, actor: str, **fields) -> None:
    """Append one event. Never raises."""
    try:
        root = Path(root)
        entry = {"at": _now_iso(), "type": type, "actor": actor}
        entry.update(fields)
        line = json.dumps(entry, ensure_ascii=False)
        path = root / EVENTS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _CACHE["key"] = None  # our own append invalidates the read cache
    except Exception as exc:
        _fallback_log(root, f"events.log failed ({type!r}): {exc!r}")


# Building one panel state re-reads the log once per session and per history
# entry (segments, amendments, rest spans...), so parsing it every time made
# /api/state take seconds. Keep the parsed log until the file changes.
# The data dir usually lives on /mnt/c, where every stat() costs about a
# millisecond, so even the freshness check is re-checked at most once a
# second (writes go through acc itself; a second of staleness is invisible).


def _all_events(path: Path):
    now = time.monotonic()
    cached_key = _CACHE["key"]
    if (cached_key and cached_key[0] == str(path)
            and now - _CACHE["checked_at"] < _CACHE_TTL):
        return _CACHE["events"]
    try:
        st = path.stat()
    except OSError:
        return []
    key = (str(path), st.st_mtime_ns, st.st_size)
    _CACHE["checked_at"] = now
    if _CACHE["key"] == key:
        return _CACHE["events"]
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    _CACHE["key"] = key
    _CACHE["events"] = out
    return out


def read_events(root, type: str = None, limit: int = None):
    """Read events.jsonl in file order (oldest first). Never raises."""
    try:
        path = Path(root) / EVENTS_FILENAME
        events = _all_events(path)
        if type is not None:
            events = [e for e in events if e.get("type") == type]
        if limit:
            return events[-limit:]
        return list(events)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# External-edit detection
# ---------------------------------------------------------------------------

def _watched_relpaths(root: Path):
    root = Path(root)
    paths = []
    ultimate = root / "goals" / "ultimate.md"
    if ultimate.exists():
        paths.append("goals/ultimate.md")
    projects_dir = root / "goals" / "projects"
    if projects_dir.exists():
        for p in sorted(projects_dir.glob("*.md")):
            paths.append(f"goals/projects/{p.name}")
    templates_dir = root / "templates"
    if templates_dir.exists():
        for p in sorted(templates_dir.glob("*.md")):
            paths.append(f"templates/{p.name}")
    presets_dir = root / "rapport" / "presets"
    if presets_dir.exists():
        for preset_dir in sorted(p for p in presets_dir.iterdir() if p.is_dir()):
            for p in sorted(preset_dir.glob("*.md")):
                paths.append(f"rapport/presets/{preset_dir.name}/{p.name}")
    return paths


def _is_text_tracked(rel: str) -> bool:
    return rel.startswith(_TEXT_TRACKED_PREFIXES)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mark_seen(root, rel_path: str) -> None:
    """Called by acc's own writers right after writing a watched file, so
    the next hook call doesn't mistake acc's own write for an external
    edit."""
    try:
        root = Path(root)
        abs_path = root / rel_path
        if not abs_path.exists():
            return
        text = abs_path.read_text(encoding="utf-8")
        snap_path = root / SNAPSHOT_FILENAME
        snapshot = store.read_json(snap_path, None) or {}
        entry = {"sha256": _sha256(text)}
        if _is_text_tracked(rel_path):
            entry["text"] = text
        snapshot[rel_path] = entry
        store.atomic_write_json(snap_path, snapshot)
    except Exception as exc:
        _fallback_log(root, f"events.mark_seen failed ({rel_path!r}): {exc!r}")


def check_external_edits(root) -> None:
    """Compare every watched file's hash against the last snapshot; log an
    external_edit event for anything that changed since. First run (no
    snapshot file yet) just records the baseline silently."""
    try:
        root = Path(root)
        snap_path = root / SNAPSHOT_FILENAME
        existing = store.read_json(snap_path, None)
        first_run = existing is None
        snapshot = dict(existing) if existing else {}
        changed = False

        for rel in _watched_relpaths(root):
            abs_path = root / rel
            try:
                text = abs_path.read_text(encoding="utf-8")
            except Exception:
                continue
            digest = _sha256(text)
            track_text = _is_text_tracked(rel)
            prev = snapshot.get(rel)

            if prev is None or prev.get("sha256") != digest:
                if not first_run:
                    fields = {"file": rel}
                    if track_text:
                        fields["before"] = prev.get("text", "") if prev else None
                        fields["after"] = text
                    log(root, "external_edit", "hook", **fields)
                entry = {"sha256": digest}
                if track_text:
                    entry["text"] = text
                snapshot[rel] = entry
                changed = True

        if changed or first_run:
            store.atomic_write_json(snap_path, snapshot)
    except Exception as exc:
        _fallback_log(root, f"events.check_external_edits failed: {exc!r}")
