"""Goals module: Ultimate Goal / Ultimate Definition / Super Objective / Objective."""

import datetime
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from acc import defaults, events, store
from acc.modules.rapport import RapportModule


class GoalsError(Exception):
    pass


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_oid() -> str:
    return uuid.uuid4().hex[:8]


def ultimate_hash(goal: str, definition: str) -> str:
    """sha256 of the normalized Ultimate content (goal + "\\n" + definition,
    stripped). Shared by GoalsModule (per-session baseline/comparison) and
    `acc rules install` (what Ultimate text an agent's file currently
    carries), so the two stay comparable."""
    content = (goal or "").strip() + "\n" + (definition or "").strip()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


_ACTOR_LABELS = {"agent": "Agent", "user": "使用者", "hook": "系統"}


def _actor_label(actor) -> str:
    return _ACTOR_LABELS.get(actor, "未知")


def _fmt_dt(iso: str) -> str:
    """'2026-09-17T00:01:30' -> '09-17 00:01'. Never raises."""
    if not iso:
        return "？"
    try:
        dt = datetime.datetime.fromisoformat(iso)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(iso)[:16] or "？"


# Work-log event types, in the order sources are described in the docstring
# of GoalsModule.work_log. objective.parked_reordered is deliberately
# excluded -- it's noisy and carries no information a person needs to see.
_SESSION_LOG_TYPES = {
    "session.created": "對話開始",
    "session.archived": "封存",
    "session.unarchived": "取消封存",
    "session.revived": "從封存自動回來",
    "session.nickname_changed": "改名",
    "session.project_changed": "改專案",
    "rapport.session_enabled_changed": "Rapport 注入變更",  # custom-rendered below
}


def _rapport_enabled_label(v) -> str:
    if v is True:
        return "開"
    if v is False:
        return "關"
    return "跟隨全域"

_WORK_LOG_TYPES = {
    "objective.set",
    "objective.edited",
    "objective.parked",
    "objective.switched",
    "objective.promoted",
    "objective.reopened",
    "objective.parked_deleted",
    "objective.closed",
    "objective.amended",
}

_AMEND_TO_LABELS = {"achieved": "成功", "failed": "失敗", "parked": "擱置", "active": "進行中"}
_STATUS_LABELS_CN = {"achieved": "成功", "failed": "失敗", "dropped": "移除"}


def _status_label_cn(status: str) -> str:
    return _STATUS_LABELS_CN.get(status, status or "")



def _parse_ultimate(text: str):
    """Parse '# Ultimate Goal\\n...\\n# Ultimate Definition\\n...' body text."""
    goal = ""
    definition = ""
    sections = re.split(r"(?m)^#\s+(.+?)\s*$", text)
    # sections[0] is preamble, then alternating heading/body
    it = iter(sections[1:])
    for heading, body in zip(it, it):
        heading = heading.strip()
        body = body.strip("\n")
        if heading == "Ultimate Goal":
            goal = body.strip()
        elif heading == "Ultimate Definition":
            definition = body.strip()
    return goal, definition


def _parse_project(text: str):
    """Parse '# Project\\n<path>\\n\\n# Super Objective\\n<text>\\n'."""
    path = ""
    super_objective = ""
    sections = re.split(r"(?m)^#\s+(.+?)\s*$", text)
    it = iter(sections[1:])
    for heading, body in zip(it, it):
        heading = heading.strip()
        body = body.strip("\n")
        if heading == "Project":
            path = body.strip()
        elif heading == "Super Objective":
            super_objective = body.strip()
    return path, super_objective


class GoalsModule:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "goals"
        self.projects_dir = self.dir / "projects"
        self.sessions_dir = self.dir / "sessions"
        self.archive_dir = self.sessions_dir / "archive"

    # -- ultimate -------------------------------------------------------

    def _ultimate_path(self) -> Path:
        return self.dir / "ultimate.md"

    def load_ultimate(self):
        text = store.read_text(self._ultimate_path(), defaults.ULTIMATE_MD)
        goal, definition = _parse_ultimate(text)
        return {"ultimate_goal": goal, "ultimate_definition": definition}

    def ultimate_history(self, field: str, limit: int = 20):
        """ultimate.changed events for one field ("goal"|"definition"),
        plus external_edit events on goals/ultimate.md (diffed per-field),
        newest first. If neither exists yet but the file has content,
        synthesize a single "初始內容（紀錄開始前）" seed entry from the
        file's current text and mtime."""
        changed = [
            e for e in events.read_events(self.root, type="ultimate.changed")
            if e.get("field") == field
        ]
        external = []
        for e in events.read_events(self.root, type="external_edit"):
            if e.get("file") != "goals/ultimate.md":
                continue
            b_goal, b_def = _parse_ultimate(e.get("before") or "") if e.get("before") else ("", "")
            a_goal, a_def = _parse_ultimate(e.get("after") or "") if e.get("after") else ("", "")
            before_val, after_val = (b_goal, a_goal) if field == "goal" else (b_def, a_def)
            external.append({
                "at": e.get("at", ""), "type": "external_edit", "actor": "hook",
                "field": field, "before": before_val, "after": after_val,
            })
        combined = changed + external
        combined.sort(key=lambda e: e.get("at", ""))
        if not combined:
            p = self._ultimate_path()
            if p.exists():
                ult = self.load_ultimate()
                val = ult["ultimate_goal"] if field == "goal" else ult["ultimate_definition"]
                if val:
                    mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                    combined = [{
                        "at": mtime, "type": "seed", "actor": "seed", "field": field,
                        "before": None, "after": val, "seed": True,
                    }]
        combined.reverse()
        return combined[:limit]

    def set_ultimate(self, goal: str, definition: str, actor: str = "user") -> None:
        before = self.load_ultimate()
        text = f"# Ultimate Goal\n\n{goal.strip()}\n\n# Ultimate Definition\n\n{definition.strip()}\n"
        store.atomic_write_text(self._ultimate_path(), text)
        events.mark_seen(self.root, "goals/ultimate.md")
        goal_s, def_s = goal.strip(), definition.strip()
        if before["ultimate_goal"] != goal_s:
            events.log(self.root, "ultimate.changed", actor, field="goal",
                       before=before["ultimate_goal"], after=goal_s)
        if before["ultimate_definition"] != def_s:
            events.log(self.root, "ultimate.changed", actor, field="definition",
                       before=before["ultimate_definition"], after=def_s)
        self._reinstall_codex_if_stale()

    def current_ultimate_hash(self) -> str:
        ult = self.load_ultimate()
        return ultimate_hash(ult["ultimate_goal"], ult["ultimate_definition"])

    def _new_session_ultimate_baseline(self, agent: str) -> str:
        """What Ultimate version a brand-new session should start "already
        having" (so it doesn't get the 已更新 block on its very first turn
        unless its agent's instruction file is actually stale). Codex has
        no live import, so its baseline is whatever `.rules_installed.json`
        says its file was last written with; Claude imports live, so it
        always starts current."""
        if agent == "codex":
            installed = store.read_json(self.root / ".rules_installed.json", None) or {}
            codex_info = installed.get("codex") or {}
            h = codex_info.get("ultimate_hash")
            if h:
                return h
        return self.current_ultimate_hash()

    def _reinstall_codex_if_stale(self) -> None:
        """Codex has no live-import for Ultimate (unlike Claude, which
        imports templates/rules.md and goals/ultimate.md live) -- so every
        time Ultimate is saved here, keep Codex's copy current by
        re-running `acc rules install codex` into whatever target it was
        last installed to, as long as that target still contains our
        managed block (if the user removed it, don't recreate it). Never
        lets a failure break the save."""
        try:
            installed = store.read_json(self.root / ".rules_installed.json", None) or {}
            codex_info = installed.get("codex")
            if not codex_info or not codex_info.get("target"):
                return
            target = Path(codex_info["target"])
            if not target.exists():
                return
            text = target.read_text(encoding="utf-8")
            from acc.cli import RULES_START, RULES_END, install_rules
            if RULES_START in text and RULES_END in text:
                install_rules("codex", self.root, target)
        except Exception as exc:
            events.log_error(self.root, f"auto-reinstall codex failed: {exc!r}")

    # -- project / super objective ---------------------------------------

    def _project_path(self, key: str) -> Path:
        return self.projects_dir / f"{key}.md"

    def resolve_project(self, cwd: str):
        root_path = store.find_project_root(cwd)
        key = store.project_key(root_path)
        return key, root_path

    def load_project(self, key: str):
        text = store.read_text(self._project_path(key), "")
        if not text:
            return {"path": "", "super_objective": ""}
        path, super_objective = _parse_project(text)
        return {"path": path, "super_objective": super_objective}

    def set_project(self, path: str, super_objective: str, actor: str = "user", via: str = None) -> None:
        key = store.project_key(path)
        before = self.load_project(key)["super_objective"]
        text = f"# Project\n{path}\n\n# Super Objective\n{super_objective.strip()}\n"
        store.atomic_write_text(self._project_path(key), text)
        events.mark_seen(self.root, f"goals/projects/{key}.md")
        after = super_objective.strip()
        if before != after:
            fields = {"project": path, "before": before, "after": after}
            if via:
                fields["via"] = via
            events.log(self.root, "super.changed", actor, **fields)

    def list_projects(self):
        result = []
        if self.projects_dir.exists():
            for p in sorted(self.projects_dir.glob("*.md")):
                key = p.stem
                data = self.load_project(key)
                result.append(
                    {
                        "key": key,
                        "path": data["path"],
                        "super_objective": data["super_objective"],
                    }
                )
        return result

    def list_projects_with_sessions(self):
        """Union of project files AND every distinct `project` path any
        session (active, idle, archived) references -- a project with
        sessions but no file yet still shows up, with super_objective ""
        and has_file False, so the user can find where to set it."""
        paths = set()
        if self.projects_dir.exists():
            for p in self.projects_dir.glob("*.md"):
                data = self.load_project(p.stem)
                if data["path"]:
                    paths.add(data["path"])

        session_counts = {}
        for d in (self.sessions_dir, self.archive_dir):
            if not d.exists():
                continue
            for p in d.glob("*.json"):
                data = store.read_json(p, None)
                proj = data.get("project") if data else ""
                if proj:
                    paths.add(proj)
                    session_counts[proj] = session_counts.get(proj, 0) + 1

        result = []
        for path in paths:
            key = store.project_key(path)
            data = self.load_project(key)
            result.append(
                {
                    "key": key,
                    "path": path,
                    "super_objective": data["super_objective"],
                    "has_file": self._project_path(key).exists(),
                    "session_count": session_counts.get(path, 0),
                }
            )
        result.sort(key=lambda r: r["path"])
        return result

    # -- super objective suggestions ("Agent 只能建議，使用者決定") ----------

    def _suggestions_path(self, key: str) -> Path:
        return self.projects_dir / f"{key}.suggestions.json"

    def _load_suggestions(self, key: str):
        return store.read_json(self._suggestions_path(key), []) or []

    def _save_suggestions(self, key: str, items) -> None:
        store.atomic_write_json(self._suggestions_path(key), items)

    def suggest_super(self, session_id: str, suggestion: str, reason: str):
        """CLI-only (`acc super suggest`). Never changes the super
        objective itself -- just files a pending suggestion for the
        session's project, for the user to accept or reject on the web."""
        session = self.load_session(session_id)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        project = session.get("project", "")
        key = store.project_key(project)
        items = self._load_suggestions(key)
        entry = {
            "id": uuid.uuid4().hex,
            "at": _now_iso(),
            "session_id": session_id,
            "agent": session.get("agent", ""),
            "suggestion": suggestion,
            "reason": reason,
            "status": "pending",
            "decided_at": None,
        }
        items.append(entry)
        self._save_suggestions(key, items)
        events.log(
            self.root, "super.suggested", "agent",
            project=project, session_id=session_id, suggestion=suggestion,
            reason=reason, suggestion_id=entry["id"],
        )
        return project, entry

    def super_for_session(self, session_id: str):
        """Used by `acc super show`: current super objective + pending
        suggestions for the session's project."""
        session = self.load_session(session_id)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        project = session.get("project", "")
        key = store.project_key(project)
        proj = self.load_project(key)
        pending = [s for s in self._load_suggestions(key) if s.get("status") == "pending"]
        return project, proj["super_objective"], pending

    def suggestions_view(self, path: str):
        """Pending first (oldest first), then the last 10 decided
        (newest first) -- used by /api/state."""
        key = store.project_key(path)
        items = self._load_suggestions(key)
        pending = [s for s in items if s.get("status") == "pending"]
        decided = [s for s in items if s.get("status") != "pending"]
        decided.sort(key=lambda s: s.get("decided_at") or "", reverse=True)
        return pending + decided[:10]

    def pending_suggestion_count(self, path: str) -> int:
        key = store.project_key(path)
        return sum(1 for s in self._load_suggestions(key) if s.get("status") == "pending")

    def _decide_suggestion(self, path: str, suggestion_id: str, status: str):
        key = store.project_key(path)
        items = self._load_suggestions(key)
        entry = next((s for s in items if s.get("id") == suggestion_id), None)
        if entry is None:
            raise GoalsError(f"suggestion not found: {suggestion_id}")
        if entry.get("status") != "pending":
            raise GoalsError("這則建議已經被決定過了")
        entry["status"] = status
        entry["decided_at"] = _now_iso()
        self._save_suggestions(key, items)
        return entry

    def accept_suggestion(self, path: str, suggestion_id: str):
        entry = self._decide_suggestion(path, suggestion_id, "accepted")
        self.set_project(path, entry["suggestion"], actor="user", via="suggestion")
        events.log(
            self.root, "super.suggestion_accepted", "user",
            project=path, suggestion_id=suggestion_id, suggestion=entry["suggestion"],
        )
        return entry

    def reject_suggestion(self, path: str, suggestion_id: str):
        entry = self._decide_suggestion(path, suggestion_id, "rejected")
        events.log(
            self.root, "super.suggestion_rejected", "user",
            project=path, suggestion_id=suggestion_id, suggestion=entry["suggestion"],
        )
        return entry

    def super_history(self, path: str, limit: int = 20):
        """super.changed events for this project (direct edits and
        suggestion-accepts) plus external_edit events on its file, newest
        first. If none exist yet but the project file has content,
        synthesize a single "初始內容（紀錄開始前）" seed entry from the
        file's current text and mtime."""
        key = store.project_key(path)
        rel = f"goals/projects/{key}.md"
        changed = [e for e in events.read_events(self.root, type="super.changed") if e.get("project") == path]
        external = [
            {**e, "actor": "hook"}
            for e in events.read_events(self.root, type="external_edit")
            if e.get("file") == rel
        ]
        combined = changed + external
        combined.sort(key=lambda e: e.get("at", ""))
        if not combined:
            p = self._project_path(key)
            if p.exists():
                data = self.load_project(key)
                if data["super_objective"]:
                    mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                    combined = [{
                        "at": mtime, "type": "seed", "actor": "seed", "project": path,
                        "before": None, "after": data["super_objective"], "seed": True,
                    }]
        combined.reverse()
        return combined[:limit]

    # -- sessions ---------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        session_id = store.sanitize_session_id(session_id)
        return self.sessions_dir / f"{session_id}.json"

    def _default_session(self, session_id: str, agent: str, cwd: str, project: str):
        now = _now_iso()
        return {
            "session_id": session_id,
            "agent": agent,
            "cwd": cwd,
            "project": project,
            "first_prompt": "",
            "created": now,
            "last_seen": now,
            "objective": "",
            "success_criteria": "",
            "status": "unset",
            "history": [],
            "turns": [],
            "turn_count": 0,
            "parked": [],
            "turn_counts_by_oid": {},
        }

    def _ensure_oid(self, session: dict) -> None:
        """Objectives without an oid (created before this feature) get one
        lazily, the next time they're written to."""
        if session.get("status") == "active" and not session.get("oid"):
            session["oid"] = _new_oid()

    def _archive_path(self, session_id: str) -> Path:
        session_id = store.sanitize_session_id(session_id)
        return self.archive_dir / f"{session_id}.json"

    def load_session(self, session_id: str):
        """Read-only lookup: checks sessions/ then sessions/archive/.

        Never moves the file -- callers that need to write to an archived
        session go through `_load_for_write`, which unarchives it first.
        """
        data = store.read_json(self._session_path(session_id), None)
        if data is not None:
            return data
        return store.read_json(self._archive_path(session_id), None)

    def save_session(self, session: dict) -> None:
        path = self._session_path(session["session_id"])
        store.atomic_write_json(path, session)

    def _move_session_file(self, session_id: str, src_dir: Path, dst_dir: Path, extra_fields: dict = None):
        """Atomically move a session json from src_dir to dst_dir.

        Content is updated (via extra_fields) with an atomic write while
        still in src_dir, then renamed into dst_dir with os.replace (atomic
        on the same filesystem). Returns the moved session dict, or None if
        it wasn't found in src_dir.
        """
        session_id = store.sanitize_session_id(session_id)
        src = src_dir / f"{session_id}.json"
        data = store.read_json(src, None)
        if data is None:
            return None
        if extra_fields:
            data.update(extra_fields)
        store.atomic_write_text(src, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{session_id}.json"
        os.replace(src, dst)
        return data

    # -- archive ------------------------------------------------------

    def archive_session(self, session_id: str) -> dict:
        """Web op: move sessions/<id>.json to sessions/archive/<id>.json."""
        data = self._move_session_file(
            session_id, self.sessions_dir, self.archive_dir,
            {"archived_at": _now_iso()},
        )
        if data is None:
            raise GoalsError(f"session not found: {session_id}")
        events.log(self.root, "session.archived", "user", session_id=session_id)
        return data

    def unarchive_session(self, session_id: str) -> dict:
        """Web op ("取消封存"): explicit, user-initiated -- does not stamp
        revived_at (that badge is reserved for the hook's surprise revival)."""
        data = self._move_session_file(session_id, self.archive_dir, self.sessions_dir)
        if data is None:
            raise GoalsError(f"archived session not found: {session_id}")
        events.log(self.root, "session.unarchived", "user", session_id=session_id)
        return data

    def _auto_unarchive(self, session_id: str, actor: str = "hook"):
        """Used by write paths (hook + CLI + web): move an archived session
        back to sessions/ and stamp revived_at, keeping everything else."""
        data = self._move_session_file(
            session_id, self.archive_dir, self.sessions_dir,
            {"revived_at": _now_iso()},
        )
        if data is not None:
            events.log(self.root, "session.revived", actor, session_id=session_id)
        return data

    def _load_for_write(self, session_id: str, actor: str = "hook"):
        """Lookup used by every write path: sessions/ first, else
        auto-unarchive from sessions/archive/. Returns None if not found
        in either place (caller then creates a fresh default session).

        `actor` is who's doing the writing (hook/agent/user) -- passed
        through so an auto-revival's session.revived event is attributed
        correctly instead of always looking like the hook did it.
        """
        data = store.read_json(self._session_path(session_id), None)
        if data is not None:
            return data
        return self._auto_unarchive(session_id, actor)

    def list_archived_sessions(self, limit: int = 50):
        sessions = []
        if self.archive_dir.exists():
            for p in self.archive_dir.glob("*.json"):
                data = store.read_json(p, None)
                if data:
                    sessions.append(data)
        sessions.sort(
            key=lambda s: s.get("archived_at", s.get("last_seen", "")), reverse=True
        )
        return sessions[:limit]

    def _fill_project_if_missing(self, session: dict, cwd: str) -> bool:
        """Session "project" is fixed at first creation. Old session files
        from before that rule existed may lack it -- fill it once here."""
        if not session.get("project"):
            _, project = self.resolve_project(cwd)
            session["project"] = project
            return True
        return False

    def _record_turn(self, session: dict) -> None:
        """Append a rapport timeline entry, only when it differs from the
        last recorded one (now also on the active objective's oid, so the
        Objective 時間軸 can tell "進行" runs of an objective apart from
        gaps where it was parked or something else was active). Only ever
        called from touch_session (the hook path) -- this records the
        value at prompt-submit time, not whatever a slider happens to be
        set to between submissions."""
        rapport = RapportModule(self.root)
        eff = rapport.get_effective(session.get("rapport"))
        sent, _source = rapport.effective_enabled(session)
        turns = session.setdefault("turns", [])
        last = turns[-1] if turns else None
        oid = session.get("oid") if session.get("status") == "active" else None
        # Legacy entries without "sent" predate this feature and were all
        # actually sent (Rapport had no on/off switch yet).
        last_sent = last.get("sent", True) if last else None
        if (
            last is None
            or last.get("preset") != eff["presetId"]
            or last.get("level") != eff["level"]
            or last.get("oid") != oid
            or last_sent != sent
        ):
            turns.append(
                {
                    "at": _now_iso(),
                    "preset": eff["presetId"],
                    "level": eff["level"],
                    "name": eff["levelName"],
                    "oid": oid,
                    "sent": sent,
                }
            )
            if len(turns) > 500:
                del turns[: len(turns) - 500]

    def touch_session(self, session_id: str, agent: str, cwd: str, prompt: str = ""):
        """Create/update the session file for this turn.

        "project" is only ever set here when the session is first created
        (or, once, if an older session file is missing it); later calls
        update "cwd" and "last_seen" but never move "project". If the
        session id only exists in sessions/archive/, it's moved back here
        (via _load_for_write) with revived_at stamped.

        If the user's work/rest switch is currently off, the hook path
        auto-resumes it first -- this only touches work_state.json/events.jsonl, never the
        session content or build_context, so it can never change what gets
        injected this turn.
        """
        work_state = self._load_work_state()
        if not work_state.get("working", True):
            self.work_start(actor="hook", auto=True)
        session = self._load_for_write(session_id, actor="hook")
        is_new = session is None
        if session is None:
            key, project = self.resolve_project(cwd)
            session = self._default_session(session_id, agent, cwd, project)
        else:
            session["cwd"] = cwd
            session["agent"] = agent
            self._fill_project_if_missing(session, cwd)
        if is_new:
            session["ultimate_hash"] = self._new_session_ultimate_baseline(agent)
        elif "ultimate_hash" not in session:
            # Legacy session from before this feature existed: it was
            # getting Ultimate every turn until now. Treat it as already
            # having the current version -- don't send it this turn either.
            session["ultimate_hash"] = self.current_ultimate_hash()
        session["last_seen"] = _now_iso()
        if not session.get("first_prompt") and prompt:
            session["first_prompt"] = prompt[:120]
        self._ensure_oid(session)
        self._record_turn(session)
        session["turn_count"] = session.get("turn_count", 0) + 1
        active_oid = session.get("oid") if session.get("status") == "active" else None
        if active_oid:
            counts = session.setdefault("turn_counts_by_oid", {})
            counts[active_oid] = counts.get(active_oid, 0) + 1
        if is_new:
            events.log(
                self.root, "session.created", "hook",
                session_id=session_id, agent=agent, project=session.get("project", ""), cwd=cwd,
            )
        self.save_session(session)

        rapport = RapportModule(self.root)
        eff = rapport.get_effective(session.get("rapport"))
        rapport_sent, rapport_enabled_source = rapport.effective_enabled(session)
        proj = self.load_project(store.project_key(session.get("project", "")))
        events.log(
            self.root, "turn", "hook",
            session_id=session_id, agent=agent, project=session.get("project", ""), cwd=cwd,
            preset=eff["presetId"], level=eff["level"], level_name=eff["levelName"],
            rapport_source="session" if session.get("rapport") else "default",
            rapport_sent=rapport_sent,
            rapport_enabled_source=rapport_enabled_source,
            objective=session.get("objective") or None,
            objective_status=session.get("status", "unset"),
            oid=active_oid,
            super_objective=proj["super_objective"],
            prompt_chars=len(prompt),
        )
        return session

    def list_sessions(self, limit: int = 30):
        """Non-archived sessions, stable-sorted newest-created-first.

        Sorting by "created" (rather than "last_seen") means nothing the
        user does in the web UI -- editing rapport, nickname, objective --
        ever reorders the list.
        """
        sessions = []
        if self.sessions_dir.exists():
            for p in self.sessions_dir.glob("*.json"):
                data = store.read_json(p, None)
                if data:
                    sessions.append(data)
        sessions.sort(key=lambda s: s.get("created", ""), reverse=True)
        return sessions[:limit]

    # -- nickname (web-only) ------------------------------------------------

    def set_nickname(self, session_id: str, nickname: str):
        session = self._load_for_write(session_id, actor="user")
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        before = session.get("nickname", "")
        nickname = (nickname or "").strip()
        if nickname:
            session["nickname"] = nickname
        else:
            session.pop("nickname", None)
        self.save_session(session)
        events.log(self.root, "session.nickname_changed", "user",
                   session_id=session_id, before=before, after=nickname)
        return session

    # -- objective ops ------------------------------------------------------

    def objective_set(self, session_id: str, objective: str, criteria: str, agent: str = "", cwd: str = ""):
        session = self._load_for_write(session_id, actor="agent")
        is_new = session is None
        if session is None:
            key, project = self.resolve_project(cwd or ".")
            session = self._default_session(session_id, agent, cwd, project)
        if session.get("status") == "active":
            raise GoalsError("目前的 Objective 還在進行中：先用 done／fail 結案，或用 park 擱置")
        session["objective"] = objective
        session["success_criteria"] = criteria
        session["status"] = "active"
        session["started"] = _now_iso()
        session["turns_at_start"] = session.get("turn_count", 0)
        session["edits"] = []
        session["oid"] = _new_oid()
        session["last_seen"] = _now_iso()
        if is_new:
            events.log(self.root, "session.created", "agent",
                       session_id=session_id, agent=agent, project=session.get("project", ""), cwd=cwd)
        self.save_session(session)
        events.log(self.root, "objective.set", "agent", session_id=session_id,
                   project=session.get("project", ""), objective=objective, success_criteria=criteria)
        return session

    def objective_revise(self, session_id: str, objective: str | None, criteria: str | None, reason: str):
        """Agent: adjust the scope of the in-progress Objective, with a reason.

        Keeps started/turns_at_start; the change is recorded in `edits`.
        """
        if not reason.strip():
            raise GoalsError("修改進行中的 Objective 需要附上 --reason")
        if objective is None and criteria is None:
            raise GoalsError("沒有要修改的內容：請給新的 Objective 或 --criteria")
        session = self._load_for_write(session_id, actor="agent")
        if session is None or session.get("status") != "active":
            raise GoalsError("目前沒有進行中的 Objective，請用 set 設定")
        self._ensure_oid(session)
        before = {"objective": session.get("objective", ""),
                  "success_criteria": session.get("success_criteria", "")}
        after = {"objective": before["objective"] if objective is None else objective,
                 "success_criteria": before["success_criteria"] if criteria is None else criteria}
        if after == before:
            raise GoalsError("內容和目前的 Objective 相同，沒有修改")
        session["objective"] = after["objective"]
        session["success_criteria"] = after["success_criteria"]
        session.setdefault("edits", []).append(
            {"at": _now_iso(), "actor": "agent", "reason": reason, "before": before, "after": after}
        )
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(self.root, "objective.edited", "agent", session_id=session_id,
                   before=before, after=after, reason=reason)
        return session

    def _objective_close(self, session_id: str, outcome: str, note: str, closed_by: str = "agent"):
        session = self._load_for_write(session_id, actor=closed_by)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        if session.get("status") != "active":
            raise GoalsError("目前沒有進行中的 Objective")
        self._ensure_oid(session)
        oid = session.get("oid")
        turn_count = session.get("turn_count", 0)
        turns_at_start = session.get("turns_at_start", turn_count)
        counts = session.get("turn_counts_by_oid", {})
        turns = counts.get(oid, turn_count - turns_at_start)
        started = session.get("started", "")
        closed = _now_iso()
        session.setdefault("history", []).append(
            {
                "oid": oid,
                "objective": session.get("objective", ""),
                "success_criteria": session.get("success_criteria", ""),
                "status": outcome,
                "note": note,
                "started": started,
                "closed": closed,
                "closed_by": closed_by,
                "turns": turns,
                "edits": session.get("edits", []),
            }
        )
        session["objective"] = ""
        session["success_criteria"] = ""
        session["status"] = "unset"
        session.pop("started", None)
        session.pop("turns_at_start", None)
        session.pop("edits", None)
        session.pop("oid", None)
        session["last_seen"] = _now_iso()
        self._auto_promote(session, closed_by)
        self.save_session(session)
        events.log(
            self.root, "objective.closed", closed_by,
            session_id=session_id, status=outcome, closed_by=closed_by, note=note,
            started=started, closed=closed, turns_count=turns, oid=oid,
        )
        return session

    def objective_done(self, session_id: str, note: str = "", closed_by: str = "agent"):
        return self._objective_close(session_id, "achieved", note, closed_by)

    def objective_fail(self, session_id: str, note: str = "", closed_by: str = "agent"):
        return self._objective_close(session_id, "failed", note, closed_by)

    # -- parking queue --------------------------------------------------

    def park(self, session_id: str, reason: str, actor: str = "agent"):
        """Move the whole active objective bundle to the end of `parked`;
        the session becomes unset. Never auto-promotes anything -- that
        only happens when an objective is *closed*."""
        session = self._load_for_write(session_id, actor=actor)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        if session.get("status") != "active":
            raise GoalsError("目前沒有進行中的 Objective，無法擱置")
        self._ensure_oid(session)
        bundle = {
            "oid": session.get("oid"),
            "objective": session.get("objective", ""),
            "success_criteria": session.get("success_criteria", ""),
            "started": session.get("started", ""),
            "turns_at_start": session.get("turns_at_start", session.get("turn_count", 0)),
            "edits": session.get("edits", []),
            "parked_at": _now_iso(),
        }
        session.setdefault("parked", []).append(bundle)
        session["objective"] = ""
        session["success_criteria"] = ""
        session["status"] = "unset"
        session.pop("started", None)
        session.pop("turns_at_start", None)
        session.pop("edits", None)
        session.pop("oid", None)
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(
            self.root, "objective.parked", actor, session_id=session_id,
            oid=bundle["oid"], objective=bundle["objective"], reason=reason,
        )
        return session

    def _auto_promote(self, session: dict, actor: str):
        """Pop the first parked bundle (if any) into the now-empty active
        slot. Called from inside _objective_close, right after a close
        clears the active objective -- never called from `park` itself."""
        parked = session.get("parked") or []
        if not parked:
            return None
        bundle = parked.pop(0)
        session["objective"] = bundle.get("objective", "")
        session["success_criteria"] = bundle.get("success_criteria", "")
        session["status"] = "active"
        session["started"] = bundle.get("started") or _now_iso()
        session["turns_at_start"] = bundle.get("turns_at_start", session.get("turn_count", 0))
        session["edits"] = bundle.get("edits", [])
        session["oid"] = bundle.get("oid") or _new_oid()
        events.log(
            self.root, "objective.promoted", actor, session_id=session.get("session_id"),
            oid=session["oid"], objective=session["objective"],
        )
        return session

    def switch_parked(self, session_id: str, oid: str, actor: str = "user"):
        """Web-only. If there's an active objective, swap it with the
        parked one: the parked bundle becomes active, and the previously
        active bundle takes its exact position in `parked`. If there's no
        active objective, just promote the parked one (remove from the
        list)."""
        session = self._load_for_write(session_id, actor=actor)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        parked = session.setdefault("parked", [])
        idx = next((i for i, b in enumerate(parked) if b.get("oid") == oid), None)
        if idx is None:
            raise GoalsError(f"parked objective not found: {oid}")
        target = parked[idx]
        had_active = session.get("status") == "active"
        from_oid = None
        if had_active:
            self._ensure_oid(session)
            from_oid = session.get("oid")
            current_bundle = {
                "oid": from_oid,
                "objective": session.get("objective", ""),
                "success_criteria": session.get("success_criteria", ""),
                "started": session.get("started", ""),
                "turns_at_start": session.get("turns_at_start", session.get("turn_count", 0)),
                "edits": session.get("edits", []),
                "parked_at": _now_iso(),
            }
            parked[idx] = current_bundle
        else:
            parked.pop(idx)
        session["objective"] = target.get("objective", "")
        session["success_criteria"] = target.get("success_criteria", "")
        session["status"] = "active"
        session["started"] = target.get("started") or _now_iso()
        session["turns_at_start"] = target.get("turns_at_start", session.get("turn_count", 0))
        session["edits"] = target.get("edits", [])
        session["oid"] = target.get("oid") or _new_oid()
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(
            self.root, "objective.switched", actor, session_id=session_id,
            to_oid=session["oid"], from_oid=from_oid,
        )
        return session

    def reorder_parked(self, session_id: str, oids, actor: str = "user"):
        session = self._load_for_write(session_id, actor=actor)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        parked = session.get("parked", [])
        current_oids = [b.get("oid") for b in parked]
        if sorted(oids) != sorted(current_oids) or len(oids) != len(current_oids):
            raise GoalsError("排序清單跟目前的擱置清單不一致")
        by_oid = {b.get("oid"): b for b in parked}
        session["parked"] = [by_oid[o] for o in oids]
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(self.root, "objective.parked_reordered", actor, session_id=session_id, oids=oids)
        return session

    def delete_parked(self, session_id: str, oid: str, actor: str = "user"):
        session = self._load_for_write(session_id, actor=actor)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        parked = session.get("parked", [])
        idx = next((i for i, b in enumerate(parked) if b.get("oid") == oid), None)
        if idx is None:
            raise GoalsError(f"parked objective not found: {oid}")
        bundle = parked.pop(idx)
        session.setdefault("history", []).append(
            {
                "oid": bundle.get("oid"),
                "objective": bundle.get("objective", ""),
                "success_criteria": bundle.get("success_criteria", ""),
                "status": "dropped",
                "note": "",
                "started": bundle.get("started", ""),
                "closed": _now_iso(),
                "closed_by": actor,
                "turns": session.get("turn_counts_by_oid", {}).get(bundle.get("oid"), 0),
                "edits": bundle.get("edits", []),
            }
        )
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(self.root, "objective.parked_deleted", actor, session_id=session_id, oid=oid)
        return session

    # -- self-correction: `acc objective list` / `amend` ------------------

    def _amend_candidates(self, session: dict):
        """The exact set of ids `amend` accepts: the last 10 history
        entries (newest first, labelled H1..H10, matching what `list`
        prints) plus every parked bundle (in queue order, labelled
        P1..Pn). Each candidate carries enough to resolve either kind of
        transition."""
        history = session.get("history", [])
        recent = list(reversed(history))[:10]
        hist_candidates = []
        for i, h in enumerate(recent, start=1):
            real_idx = len(history) - i
            hist_candidates.append({"label": f"H{i}", "oid": h.get("oid"), "kind": "history", "idx": real_idx, "entry": h})
        parked = session.get("parked", [])
        park_candidates = []
        for i, b in enumerate(parked, start=1):
            park_candidates.append({"label": f"P{i}", "oid": b.get("oid"), "kind": "parked", "idx": i - 1, "entry": b})
        return hist_candidates, park_candidates

    def _resolve_amend_id(self, session: dict, id_str: str):
        hist_c, park_c = self._amend_candidates(session)
        all_c = hist_c + park_c
        for c in all_c:
            if c["label"] == id_str:
                return c
        for c in all_c:
            if c["oid"] and c["oid"] == id_str:
                return c
        valid = "、".join(c["label"] for c in all_c) or "（無）"
        raise GoalsError(f"找不到這個 id：{id_str}（可用：{valid}，或該筆的 oid）")

    def objective_list_text(self, session_id: str) -> str:
        """`acc objective list --session ID`: 進行中／擱置清單／已結案
        （最近 10 筆），每行帶短 id（P<n>/H<n>）與 oid（如果有）。"""
        session = self.load_session(session_id)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")

        lines = []
        if session.get("status") == "active":
            oid_s = f"（oid: {session['oid']}）" if session.get("oid") else ""
            lines.append(f"進行中：{session.get('objective', '')}{oid_s}")
        else:
            lines.append("進行中：尚未設定")

        hist_c, park_c = self._amend_candidates(session)

        lines.append("")
        lines.append(f"擱置清單（{len(park_c)}）：")
        if park_c:
            for c in park_c:
                b = c["entry"]
                oid_s = f"　[oid: {b.get('oid')}]" if b.get("oid") else ""
                lines.append(
                    f"  {c['label']}　{b.get('objective', '')}（擱置於 {_fmt_dt(b.get('parked_at', ''))}）{oid_s}"
                )
        else:
            lines.append("  （無）")

        lines.append("")
        lines.append("已結案（最近 10 筆，新到舊）：")
        if hist_c:
            for c in hist_c:
                h = c["entry"]
                status_label = _status_label_cn(h.get("status", ""))
                oid_s = f"　[oid: {h.get('oid')}]" if h.get("oid") else ""
                lines.append(
                    f"  {c['label']}　{status_label}　{_fmt_dt(h.get('closed', ''))}　{h.get('objective', '')}{oid_s}"
                )
        else:
            lines.append("  （無）")

        return "\n".join(lines)

    def objective_amend(self, session_id: str, id_str: str, to: str, reason: str,
                         note: str = None, actor: str = "agent"):
        """Correct an earlier `done`/`fail`/`park` call that was wrong:
        `id_str` is a `P<n>`/`H<n>` id (see `objective_list_text`) or an
        oid. `to` is achieved|failed|parked|active.

        `--reason` is required for the agent (CLI) -- the user is the
        authority on the web, so it's optional there.
        """
        if to not in _AMEND_TO_LABELS:
            raise GoalsError(f"不支援的目標狀態：{to}")
        if actor == "agent" and not (reason or "").strip():
            raise GoalsError("更正需要附上 --reason")
        session = self._load_for_write(session_id, actor=actor)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")

        if session.get("status") == "active" and session.get("oid") and id_str == session.get("oid"):
            raise GoalsError("這是進行中的 Objective，請用 done／fail／park，不要用 amend")

        cand = self._resolve_amend_id(session, id_str)
        kind, idx = cand["kind"], cand["idx"]

        if kind == "history":
            entry = session["history"][idx]
            oid = entry.get("oid")
            objective_text = entry.get("objective", "")
            orig_status = entry.get("status", "")
            target = "history"
            if to in ("achieved", "failed"):
                before = {"status": orig_status, "note": entry.get("note", "")}
                entry["status"] = to
                if note is not None:
                    entry["note"] = note
                after = {"status": to, "note": entry.get("note", "")}
            elif to == "parked":
                before = {"status": orig_status}
                session["history"].pop(idx)
                bundle = {
                    "oid": oid,
                    "objective": entry.get("objective", ""),
                    "success_criteria": entry.get("success_criteria", ""),
                    "edits": entry.get("edits", []),
                    "parked_at": _now_iso(),
                }
                session.setdefault("parked", []).append(bundle)
                after = {"status": "parked"}
            else:  # to == "active"
                if session.get("status") == "active":
                    raise GoalsError("目前有進行中的 Objective，先結案或擱置才能改成進行中")
                before = {"status": orig_status}
                session["history"].pop(idx)
                session["objective"] = entry.get("objective", "")
                session["success_criteria"] = entry.get("success_criteria", "")
                session["status"] = "active"
                session["started"] = entry.get("started", "")
                turns = entry.get("turns") or 0
                session["turns_at_start"] = session.get("turn_count", 0) - turns
                session["edits"] = entry.get("edits", [])
                session["oid"] = oid or _new_oid()
                after = {"status": "active"}
        else:  # kind == "parked"
            if to == "parked":
                raise GoalsError("這一筆已經在擱置清單裡")
            if to not in ("achieved", "failed"):
                raise GoalsError("擱置中的項目只能更正成 achieved 或 failed")
            bundle = session["parked"][idx]
            oid = bundle.get("oid")
            objective_text = bundle.get("objective", "")
            target = "parked"
            before = {"status": "parked"}
            session["parked"].pop(idx)
            closed = _now_iso()
            turns = session.get("turn_counts_by_oid", {}).get(oid, 0)
            h_entry = {
                "oid": oid,
                "objective": bundle.get("objective", ""),
                "success_criteria": bundle.get("success_criteria", ""),
                "status": to,
                "note": note or "",
                "started": bundle.get("started", ""),
                "closed": closed,
                "closed_by": actor,
                "turns": turns,
                "edits": bundle.get("edits", []),
            }
            session.setdefault("history", []).append(h_entry)
            after = {"status": to, "note": h_entry["note"]}

        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(
            self.root, "objective.amended", actor,
            session_id=session_id, oid=oid, target=target, to=to,
            before=before, after=after, reason=reason or "", note=note,
            objective=objective_text,
        )
        return session

    def amendments_for_oid(self, session_id: str, oid: str):
        """objective.amended events for one oid, oldest first. Used by the
        web view so a timeline/parked entry can show its correction
        history."""
        if not oid:
            return []
        evs = [
            e for e in events.read_events(self.root, type="objective.amended")
            if e.get("session_id") == session_id and e.get("oid") == oid
        ]
        evs.sort(key=lambda e: e.get("at", ""))
        return evs

    # -- 進行 / 擱置 segments (event-derived) --

    def objective_segments(self, session_id: str, oid: str, started: str, closed: str = None):
        """Replay events.jsonl for this session and reconstruct the exact
        進行/擱置 timeline for one objective (`oid`), instead of inferring it
        from `session["turns"]` (which is only written at prompt-submit
        time and therefore misses any park/promote/switch that happens
        between two submissions).

        Returns a list of {"type": "進行"|"擱置", "start": iso, "end": iso|None},
        oldest first. An unfinished last run has end None. Legacy data (no
        oid, or an oid with no matching events) falls back to a single
        進行 segment [started, closed] so the UI shows something truthful
        rather than claiming "no record".
        """
        if not oid or not started:
            return [{"type": "進行", "start": started, "end": closed}] if started else []

        events_all = [
            e for e in events.read_events(self.root) if e.get("session_id") == session_id
        ]

        boundaries = []  # (time, "start"|"end"|"close"), in file (causal) order
        has_close = False
        for e in events_all:
            etype = e.get("type")
            at = e.get("at", "")
            if etype in ("objective.set", "objective.reopened"):
                ev_oid = e.get("oid")
                if ev_oid:
                    if ev_oid == oid:
                        boundaries.append((at, "start"))
                elif at == started:
                    boundaries.append((at, "start"))
            elif etype == "objective.promoted":
                if e.get("oid") == oid:
                    boundaries.append((at, "start"))
            elif etype == "objective.switched":
                if e.get("to_oid") == oid:
                    boundaries.append((at, "start"))
                if e.get("from_oid") == oid:
                    boundaries.append((at, "end"))
            elif etype == "objective.parked":
                if e.get("oid") == oid:
                    boundaries.append((at, "end"))
            elif etype == "objective.closed":
                ev_oid = e.get("oid")
                close_time = e.get("closed") or at
                matched = (ev_oid == oid) if ev_oid else (closed is not None and close_time == closed)
                if matched:
                    boundaries.append((close_time, "close"))
                    has_close = True

        if closed is not None and not has_close:
            boundaries.append((closed, "close"))

        segments = []
        run_start = started
        cur_type = "進行"
        for t, kind in boundaries:
            if kind == "start":
                if cur_type == "擱置":
                    segments.append({"type": "擱置", "start": run_start, "end": t})
                    run_start, cur_type = t, "進行"
            elif kind == "end":
                if cur_type == "進行":
                    segments.append({"type": "進行", "start": run_start, "end": t})
                    run_start, cur_type = t, "擱置"
            elif kind == "close":
                segments.append({"type": cur_type, "start": run_start, "end": t})
                run_start, cur_type = None, None
                break

        if run_start is not None:
            segments.append({"type": cur_type, "start": run_start, "end": None})

        # Drop zero-length segments, then merge adjacent same-type runs.
        segments = [s for s in segments if s["end"] != s["start"]]
        merged = _merge_adjacent_segments(segments)

        # Subtract rest spans from 進行 runs only (a 擱置 run that also
        # spans a rest stays 擱置 -- parking already means not working on
        # it, so 休息 is never nested inside it).
        rest_spans = self._rest_spans(session_id)
        if rest_spans:
            split = []
            for seg in merged:
                if seg["type"] == "進行":
                    split.extend(_split_segment_by_rest(seg, rest_spans))
                else:
                    split.append(seg)
            merged = _merge_adjacent_segments(split)

        return merged

    def reopen(self, session_id: str, index: int):
        """Web-only: pop a history entry back into the active objective.

        Only allowed when the session currently has no active Objective
        (status "unset") -- an in-progress Objective must be closed first.
        """
        session = self._load_for_write(session_id, actor="user")
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        if session.get("status") != "unset":
            raise GoalsError("目前有進行中的 Objective，先結案才能重新開啟")
        history = session.get("history", [])
        if not (0 <= index < len(history)):
            raise GoalsError(f"history index out of range: {index}")
        entry = history.pop(index)
        session["objective"] = entry.get("objective", "")
        session["success_criteria"] = entry.get("success_criteria", "")
        session["status"] = "active"
        session["started"] = _now_iso()
        session["turns_at_start"] = session.get("turn_count", 0)
        session["edits"] = []
        session["oid"] = entry.get("oid") or _new_oid()
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(self.root, "objective.reopened", "user", session_id=session_id,
                   objective=session["objective"], success_criteria=session["success_criteria"])
        return session

    def force_edit_session(self, session_id: str, fields: dict):
        """Web-only: set objective/criteria/status/project freely (user authority)."""
        session = self._load_for_write(session_id, actor="user")
        is_new = session is None
        if session is None:
            key, project = self.resolve_project(fields.get("cwd", "."))
            session = self._default_session(
                session_id, fields.get("agent", ""), fields.get("cwd", ""), project
            )
        touched = [f for f in ("objective", "success_criteria", "status", "project") if f in fields]
        before = {f: session.get(f) for f in touched}
        was_active = session.get("status") == "active"
        if was_active:
            self._ensure_oid(session)
        prior_objective = session.get("objective", "")
        prior_criteria = session.get("success_criteria", "")
        for f in touched:
            session[f] = fields[f]
        now_active = session.get("status") == "active"
        # Only a transition into "active" stamps a fresh started/turns_at_start;
        # an edit that keeps (or leaves alone) an already-active objective must
        # not reset the clock the Objective 時間軸 and turns-count are based on.
        becoming_active = now_active and not was_active
        if becoming_active:
            session["started"] = _now_iso()
            session["turns_at_start"] = session.get("turn_count", 0)
            session["edits"] = []
            session["oid"] = _new_oid()
        obj_touched = ("objective" in touched) or ("success_criteria" in touched)
        if now_active and was_active and obj_touched:
            new_objective = session.get("objective", "")
            new_criteria = session.get("success_criteria", "")
            if new_objective != prior_objective or new_criteria != prior_criteria:
                session.setdefault("edits", []).append(
                    {
                        "at": _now_iso(),
                        "actor": "user",
                        "before": {"objective": prior_objective, "success_criteria": prior_criteria},
                        "after": {"objective": new_objective, "success_criteria": new_criteria},
                    }
                )
        session["last_seen"] = _now_iso()
        if is_new:
            events.log(self.root, "session.created", "user", session_id=session_id,
                       agent=session.get("agent", ""), project=session.get("project", ""),
                       cwd=session.get("cwd", ""))
        self.save_session(session)
        after = {f: session.get(f) for f in touched}
        if touched:
            events.log(self.root, "objective.edited", "user", session_id=session_id,
                       before=before, after=after)
        if "project" in touched and before.get("project") != after.get("project"):
            events.log(self.root, "session.project_changed", "user", session_id=session_id,
                       before=before.get("project"), after=after.get("project"))
        return session

    # -- rapport override (web-only; users, not agents, control this) -----

    def set_session_rapport(self, session_id: str, preset_id: str, level: int, cwd: str = ""):
        session = self._load_for_write(session_id, actor="user")
        is_new = session is None
        if session is None:
            key, project = self.resolve_project(cwd or ".")
            session = self._default_session(session_id, "", cwd, project)
        rapport = RapportModule(self.root)
        if not rapport.preset_exists(preset_id):
            raise GoalsError(f"preset not found: {preset_id}")
        before = session.get("rapport")
        session["rapport"] = {"preset": preset_id, "level": int(level)}
        session["last_seen"] = _now_iso()
        if is_new:
            events.log(self.root, "session.created", "user", session_id=session_id,
                       agent="", project=session.get("project", ""), cwd=cwd)
        self.save_session(session)
        events.log(self.root, "rapport.session_set", "user", session_id=session_id,
                   before=before, after=session["rapport"])
        return session

    def clear_session_rapport(self, session_id: str):
        session = self._load_for_write(session_id, actor="user")
        if session is None:
            raise GoalsError(f"session not found: {session_id}")
        before = session.get("rapport")
        session.pop("rapport", None)
        session["last_seen"] = _now_iso()
        self.save_session(session)
        events.log(self.root, "rapport.session_cleared", "user", session_id=session_id, before=before)
        return session

    # -- rapport on/off switch (web-only; per-session override of the
    # -- global config.json modules.rapport switch) -----------------------

    def set_session_rapport_enabled(self, session_id: str, value, cwd: str = ""):
        """`value`: True/False sets an explicit per-session override;
        None clears it (the session goes back to following the global
        switch)."""
        session = self._load_for_write(session_id, actor="user")
        is_new = session is None
        if session is None:
            key, project = self.resolve_project(cwd or ".")
            session = self._default_session(session_id, "", cwd, project)
        before = session.get("rapport_enabled")
        if value is None:
            session.pop("rapport_enabled", None)
        else:
            session["rapport_enabled"] = bool(value)
        after = session.get("rapport_enabled")
        session["last_seen"] = _now_iso()
        if is_new:
            events.log(self.root, "session.created", "user", session_id=session_id,
                       agent="", project=session.get("project", ""), cwd=cwd)
        self.save_session(session)
        events.log(self.root, "rapport.session_enabled_changed", "user", session_id=session_id,
                   before=before, after=after)
        return session

    # -- work / rest switch (the user's own record; never changes what is
    # -- injected) --------

    def _work_state_path(self) -> Path:
        return self.root / "work_state.json"

    def _idle_hours(self) -> int:
        cfg = store.read_json(self.root / "config.json", None) or {}
        return cfg.get("idle_hours", 6)

    def _load_work_state(self) -> dict:
        state = store.read_json(self._work_state_path(), None)
        if state is None:
            now = _now_iso()
            state = {"working": True, "since": now, "index": 1}
            store.atomic_write_json(self._work_state_path(), state)
            events.log(self.root, "work.started", "hook", index=1, since=now, auto=False)
        return state

    def _save_work_state(self, state: dict) -> None:
        store.atomic_write_json(self._work_state_path(), state)

    def work_status(self) -> dict:
        return dict(self._load_work_state())

    def _sessions_eligible_for_rest(self):
        """Every session that is neither archived (not in sessions_dir) nor
        idle (last_seen older than config idle_hours -- the same rule the
        web state's 閒置 split uses)."""
        idle_hours = self._idle_hours()
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=idle_hours)
        out = []
        if self.sessions_dir.exists():
            for p in self.sessions_dir.glob("*.json"):
                data = store.read_json(p, None)
                if not data:
                    continue
                last_seen = data.get("last_seen", "")
                try:
                    dt = datetime.datetime.fromisoformat(last_seen)
                except Exception:
                    dt = None
                if dt is not None and dt < cutoff:
                    continue
                out.append(data)
        return out

    def work_stop(self, actor: str = "user") -> dict:
        state = self._load_work_state()
        if not state.get("working", True):
            raise GoalsError("已經是休息中")
        index = state.get("index", 1)
        now = _now_iso()
        frm = state.get("since", now)
        state["working"] = False
        state["since"] = now
        self._save_work_state(state)
        events.log(self.root, "work.stopped", actor, index=index, **{"from": frm, "to": now})
        for sess in self._sessions_eligible_for_rest():
            events.log(self.root, "rest.started", actor,
                       session_id=sess.get("session_id"), index=index)
        return state

    def work_start(self, actor: str = "user", auto: bool = False) -> dict:
        state = self._load_work_state()
        if state.get("working", True):
            raise GoalsError("已經是工作中")
        prev_index = state.get("index", 1)
        now = _now_iso()
        new_index = prev_index + 1
        state["working"] = True
        state["since"] = now
        state["index"] = new_index
        self._save_work_state(state)
        events.log(self.root, "work.started", actor, index=new_index, since=now, auto=auto)
        started = [
            e for e in events.read_events(self.root, type="rest.started")
            if e.get("index") == prev_index
        ]
        already_ended = {
            e.get("session_id") for e in events.read_events(self.root, type="rest.ended")
            if e.get("index") == prev_index
        }
        for e in started:
            sid = e.get("session_id")
            if sid and sid not in already_ended:
                events.log(self.root, "rest.ended", actor, session_id=sid, index=prev_index, auto=auto)
        return state

    def _rest_spans(self, session_id: str):
        """{"start","end" (None if still resting),"auto_resumed"} spans of
        rest for one session, oldest first. `auto_resumed` reflects the
        `auto` flag on the work.started call that ended this span (logged
        onto the matching rest.ended event -- see work_start)."""
        starts = [
            e for e in events.read_events(self.root, type="rest.started")
            if e.get("session_id") == session_id
        ]
        ends = {
            e.get("index"): e for e in events.read_events(self.root, type="rest.ended")
            if e.get("session_id") == session_id
        }
        spans = []
        for s in starts:
            idx = s.get("index")
            end_e = ends.get(idx)
            spans.append({
                "start": s.get("at", ""),
                "end": end_e.get("at") if end_e else None,
                "auto_resumed": bool(end_e.get("auto")) if end_e else False,
            })
        spans.sort(key=lambda x: x["start"])
        return spans

    def _work_session_label(self, index: int, start, end) -> str:
        if index == 0:
            return "紀錄開始前"
        s = _fmt_dt(start) if start else "？"
        e = _hm(end) if end else ""
        return f"#{index}　{s}–{e}"

    def work_sessions(self):
        """Ordered list of {"index","start","end" (None if current),
        "label"}, built from work.started/work.stopped events. Everything
        before the first work.started event is index 0 ("紀錄開始前")."""
        evs = [
            e for e in events.read_events(self.root)
            if e.get("type") in ("work.started", "work.stopped")
        ]
        evs.sort(key=lambda e: e.get("at", ""))
        started_events = [e for e in evs if e.get("type") == "work.started"]
        first_start = started_events[0].get("at", "") if started_events else None

        sessions = [{"index": 0, "start": None, "end": first_start}]
        open_by_index = {}
        for e in evs:
            if e.get("type") == "work.started":
                open_by_index[e.get("index")] = e.get("since", e.get("at"))
            else:
                idx = e.get("index")
                start = open_by_index.pop(idx, e.get("from"))
                sessions.append({"index": idx, "start": start, "end": e.get("to", e.get("at"))})
        for idx, start in open_by_index.items():
            sessions.append({"index": idx, "start": start, "end": None})

        sessions.sort(key=lambda s: s["index"])
        for s in sessions:
            s["label"] = self._work_session_label(s["index"], s["start"], s["end"])
        return sessions

    def session_work_sessions(self, session_id: str):
        """The subset of work_sessions() this conversation has activity in
        (any turn or objective.* event for it falling inside that
        session's span)."""
        all_sessions = self.work_sessions()
        session_events = [
            e for e in events.read_events(self.root)
            if e.get("session_id") == session_id
            and (e.get("type") == "turn" or str(e.get("type", "")).startswith("objective."))
        ]
        if not session_events:
            return []
        times = sorted(e.get("at", "") for e in session_events)
        result = []
        for s in all_sessions:
            start = s["start"] or ""
            end = s["end"]
            for t in times:
                if t >= start and (end is None or t < end):
                    result.append(s)
                    break
        return result

    # -- context ------------------------------------------------------------

    def build_context(self, session_id: str, cwd: str, record: bool = False) -> str:
        """`record=True` is for the hook path only: when the Ultimate block
        needs to be sent this turn, it both appears in the returned text
        AND updates the session's stored ultimate_hash (+ logs
        `ultimate.sent`). Preview paths (`acc context`, the web
        `/api/context`) call this with record=False (the default) so they
        can show what the next real turn would contain without mutating
        anything.
        """
        template = store.read_text(self.dir.parent / "templates" / "goals.md", defaults.GOALS_MD)
        ult = self.load_ultimate()
        current_hash = ultimate_hash(ult["ultimate_goal"], ult["ultimate_definition"])

        session = self.load_session(session_id) if session_id else None
        if session is not None:
            # Only persist the backfill if the session file actually lives
            # in sessions/ -- build_context is read-only for archived
            # sessions (an archived session shouldn't spawn a duplicate
            # active file just from being previewed).
            if self._fill_project_if_missing(session, cwd) and self._session_path(session_id).exists():
                self.save_session(session)
            project = session.get("project", "")
        else:
            _, project = self.resolve_project(cwd)
        key = store.project_key(project)
        proj = self.load_project(key)
        super_objective = proj["super_objective"] or "（尚未設定）"
        project_display = project

        # Ultimate is loaded once through the agent's instruction file at
        # conversation start; it's only re-sent in a turn when it changed
        # compared with what this session already has (session is None, or
        # never got a baseline at all -> nothing to say).
        ultimate_block = ""
        if session is not None and "ultimate_hash" in session and session.get("ultimate_hash") != current_hash:
            tmpl_u = store.read_text(
                self.dir.parent / "templates" / "ultimate_updated.md", defaults.ULTIMATE_UPDATED_MD
            )
            ultimate_block = (
                tmpl_u.replace("{ultimate_goal}", ult["ultimate_goal"] or "（尚未設定）")
                .replace("{ultimate_definition}", ult["ultimate_definition"] or "（尚未設定）")
            ).strip()
            if record and self._session_path(session_id).exists():
                session["ultimate_hash"] = current_hash
                self.save_session(session)
                events.log(self.root, "ultimate.sent", "hook", session_id=session_id, hash=current_hash)

        objective_block = ""
        if session_id:
            if session and session.get("status") == "active":
                tmpl = store.read_text(
                    self.dir.parent / "templates" / "objective_active.md",
                    defaults.OBJECTIVE_ACTIVE_MD,
                )
                objective_block = (
                    tmpl.replace("{objective}", session.get("objective", ""))
                    .replace("{success_criteria}", session.get("success_criteria", ""))
                    .replace("{session_id}", session_id)
                )
            else:
                tmpl = store.read_text(
                    self.dir.parent / "templates" / "objective_unset.md",
                    defaults.OBJECTIVE_UNSET_MD,
                )
                objective_block = tmpl.replace("{session_id}", session_id)

        text = template
        # Back-compat: a goals.md still carrying the old literal
        # {ultimate_goal}/{ultimate_definition} placeholders (from before
        # Ultimate moved to the instruction file) keeps working exactly as
        # before -- always substituted, every turn.
        if "{ultimate_goal}" in text or "{ultimate_definition}" in text:
            text = text.replace("{ultimate_goal}", ult["ultimate_goal"] or "（尚未設定）")
            text = text.replace("{ultimate_definition}", ult["ultimate_definition"] or "（尚未設定）")
        suggest_block = ""
        pending_count = self.pending_suggestion_count(project) if project else 0
        if pending_count:
            suggest_block = (
                store.read_text(self.dir.parent / "templates" / "super_pending.md", defaults.SUPER_PENDING_MD)
                .replace("{count}", str(pending_count)).strip()
            )

        text = text.replace("{project}", project_display)
        text = text.replace("{super_objective}", super_objective)
        # An empty block removes its whole line instead of leaving a blank one.
        for name, value in (
            ("{ultimate_block}", ultimate_block),
            ("{suggest_block}", suggest_block),
            ("{objective_block}", objective_block.strip()),
        ):
            text = text.replace(name + "\n", "") if not value else text.replace(name, value)
        return text.strip("\n")

    # -- work log (per-session, chronological, human-readable) --------------

    def work_log(self, session_id: str, work_from: int = None, work_to: int = None) -> str:
        """A Markdown work log for this session: every objective.* thing
        that happened, oldest first, merging events.jsonl with whatever
        history predates it, plus a "目前" snapshot of the live state
        (always included, regardless of any work-session filter).

        Used identically by `acc objective log` (agent-facing) and the web
        panel's 「複製工作紀錄」 button (user-facing) -- same text either way.

        `work_from`/`work_to`: an inclusive pair of work-session indexes
        (see GoalsModule.work_sessions) -- when given, only records whose
        timestamp falls inside that span of work sessions are included,
        and the title line states the range.
        """
        session = self.load_session(session_id)
        if session is None:
            raise GoalsError(f"session not found: {session_id}")

        range_start = range_end = range_title = None
        if work_from is not None or work_to is not None:
            lo = work_from if work_from is not None else work_to
            hi = work_to if work_to is not None else work_from
            if lo > hi:
                lo, hi = hi, lo
            by_index = {s["index"]: s for s in self.work_sessions()}
            if lo not in by_index or hi not in by_index:
                raise GoalsError("找不到指定的工作階段範圍")
            range_start = by_index[lo]["start"] or ""
            range_end = by_index[hi]["end"]
            range_title = f"工作階段 #{lo}" if lo == hi else f"工作階段 #{lo}–#{hi}"

        events_all = [
            e for e in events.read_events(self.root)
            if e.get("session_id") == session_id and e.get("type") in _WORK_LOG_TYPES
        ]

        history = session.get("history", [])
        closed_events = [e for e in events_all if e.get("type") == "objective.closed"]
        dropped_events = [e for e in events_all if e.get("type") == "objective.parked_deleted"]
        closed_by_oid = {e["oid"]: e for e in closed_events if e.get("oid")}
        closed_by_time = {e["closed"]: e for e in closed_events if e.get("closed")}
        dropped_by_oid = {e["oid"]: e for e in dropped_events if e.get("oid")}
        dropped_by_time = {e["at"]: e for e in dropped_events if e.get("at")}

        records = []  # each: {"at", "kind", "seq", "lines": [str, ...]}
        seq = [0]

        def emit(at, kind, lines):
            seq[0] += 1
            records.append({"at": at or "", "kind": kind, "seq": seq[0], "lines": lines})

        # -- reconstruct history entries that predate the event log ---------
        start_types = ("objective.set", "objective.reopened", "objective.promoted", "objective.switched")
        start_times = [e.get("at", "") for e in events_all if e.get("type") in start_types]
        edited_times = {e.get("at") for e in events_all if e.get("type") == "objective.edited"}

        for h in history:
            status = h.get("status", "")
            oid = h.get("oid")
            closed_at = h.get("closed", "")
            if status == "dropped":
                matched = (oid and oid in dropped_by_oid) or (closed_at and closed_at in dropped_by_time)
            else:
                matched = (oid and oid in closed_by_oid) or (closed_at and closed_at in closed_by_time)
            if matched:
                # The close is in the event log, but its start (and any edits)
                # may predate it: fill in whatever the log doesn't cover.
                h_started, h_closed = h.get("started", ""), h.get("closed", "")
                started_logged = any(h_started <= t <= h_closed for t in start_times) if h_started else True
                if status != "dropped" and not started_logged:
                    marker = "　（事件紀錄開始前，依結案資料重建）"
                    first = (h.get("edits") or [{}])[0].get("before") or {}
                    set_obj = first.get("objective", h.get("objective", ""))
                    set_crit = first.get("success_criteria", h.get("success_criteria", ""))
                    lines = [f"- {_fmt_dt(h_started)} 設定　{set_obj}{marker}"]
                    if set_crit:
                        lines.append(f"  標準：{set_crit}")
                    emit(h_started, "set", lines)
                for ed in h.get("edits", []):
                    if ed.get("at") not in edited_times:
                        emit(ed.get("at", h_started), "edited", _render_edited(ed))
                continue

            marker = "　（事件紀錄開始前，依結案資料重建）"
            objective = h.get("objective", "")
            first = (h.get("edits") or [{}])[0].get("before") or {}
            criteria = first.get("success_criteria", h.get("success_criteria", ""))
            set_objective = first.get("objective", objective)
            started = h.get("started", "")

            if status != "dropped":
                set_lines = [f"- {_fmt_dt(started) if started else '？'} 設定　{set_objective}{marker}"]
                if criteria:
                    set_lines.append(f"  標準：{criteria}")
                # Unknown start: sort it right before its own close.
                emit(started or closed_at, "set", set_lines)

                for ed in h.get("edits", []):
                    emit(ed.get("at", started), "edited", _render_edited(ed))

                status_label = {"achieved": "成功", "failed": "失敗"}.get(status, status or "結案")
                turns = h.get("turns")
                close_lines = [
                    "- {ts} {label}（{actor}，{turns} 輪，{start} → {end}）　{obj}{marker}".format(
                        ts=_fmt_dt(closed_at), label=status_label, actor=_actor_label(h.get("closed_by")),
                        turns=turns if turns is not None else "?", start=_hm(started),
                        end=_hm(closed_at), obj=objective, marker=marker,
                    )
                ]
                if h.get("note"):
                    close_lines.append(f"  備註：{h['note']}")
                if oid and started:
                    summary = _segments_summary_line(self.objective_segments(session_id, oid, started, closed_at))
                    if summary:
                        close_lines.append(f"  進行 / 擱置：{summary}")
                emit(closed_at, "closed", close_lines)
            else:
                emit(closed_at, "parked_deleted",
                     [f"- {_fmt_dt(closed_at)} 移除（{_actor_label(h.get('closed_by'))}）　{objective}{marker}"])

        # -- replay events.jsonl in order, tracking the objective text that
        # -- each oid refers to (see docstring above for why this beats
        # -- trying to match everything back against the mutable history[]).
        active = None  # {"objective": ..., "success_criteria": ...} | None
        parked_map = {}  # oid -> {"objective": ..., "success_criteria": ...}
        history_by_oid = {h.get("oid"): h for h in history if h.get("oid")}
        history_by_closed = {h.get("closed"): h for h in history if h.get("closed")}

        # A promoted-objective event is always logged *before* the closed
        # event that triggered it (_auto_promote runs, then the caller logs
        # "objective.closed"). Buffer it and flush right after the closed
        # record so the rendered narrative reads "closed" then "自動接回",
        # regardless of how the raw timestamps tie.
        pending_promote = None

        def flush_pending_promote():
            nonlocal pending_promote
            if pending_promote is not None:
                seq[0] += 1
                pending_promote["seq"] = seq[0]
                records.append(pending_promote)
                pending_promote = None

        for e in events_all:
            etype = e.get("type", "")
            at = e.get("at", "")

            if etype != "objective.closed":
                flush_pending_promote()

            if etype == "objective.set":
                objective = e.get("objective", "")
                criteria = e.get("success_criteria", "")
                active = {"objective": objective, "success_criteria": criteria}
                lines = [f"- {_fmt_dt(at)} 設定　{objective}"]
                if criteria:
                    lines.append(f"  標準：{criteria}")
                emit(at, "set", lines)

            elif etype == "objective.edited":
                before = e.get("before") or {}
                after = e.get("after") or {}
                if "objective" in after or "success_criteria" in after:
                    active = active or {"objective": "", "success_criteria": ""}
                    if "objective" in after:
                        active["objective"] = after.get("objective", "")
                    if "success_criteria" in after:
                        active["success_criteria"] = after.get("success_criteria", "")
                if after.get("status") == "unset":
                    active = None
                emit(at, "edited", _render_edited(e))

            elif etype == "objective.parked":
                oid = e.get("oid")
                objective = e.get("objective", "") or (active or {}).get("objective", "")
                criteria = (active or {}).get("success_criteria", "")
                if oid:
                    parked_map[oid] = {"objective": objective, "success_criteria": criteria}
                reason = e.get("reason", "")
                prefix = f"理由：{reason}　" if reason else ""
                emit(at, "parked", [f"- {_fmt_dt(at)} 擱置（{_actor_label(e.get('actor'))}）{prefix}{objective}"])
                active = None

            elif etype == "objective.switched":
                to_oid = e.get("to_oid")
                from_oid = e.get("from_oid")
                to_bundle = parked_map.get(to_oid, {"objective": "", "success_criteria": ""})
                to_text = to_bundle.get("objective", "")
                if from_oid:
                    from_text = (active or {}).get("objective", "")
                    parked_map[from_oid] = {
                        "objective": from_text,
                        "success_criteria": (active or {}).get("success_criteria", ""),
                    }
                    line = f"- {_fmt_dt(at)} 切上來（{_actor_label(e.get('actor'))}）{to_text}（與「{from_text}」對調）"
                else:
                    line = f"- {_fmt_dt(at)} 切上來（{_actor_label(e.get('actor'))}）{to_text}"
                active = dict(to_bundle)
                emit(at, "switched", [line])

            elif etype == "objective.promoted":
                oid = e.get("oid")
                objective = e.get("objective", "")
                criteria = parked_map.get(oid, {}).get("success_criteria", "")
                active = {"objective": objective, "success_criteria": criteria}
                pending_promote = {
                    "at": at, "kind": "promoted",
                    "lines": [f"- {_fmt_dt(at)} 自動接回　{objective}"],
                }

            elif etype == "objective.reopened":
                objective = e.get("objective", "")
                criteria = e.get("success_criteria", "")
                active = {"objective": objective, "success_criteria": criteria}
                lines = [f"- {_fmt_dt(at)} 重新開啟（{_actor_label(e.get('actor'))}）　{objective}"]
                if criteria:
                    lines.append(f"  標準：{criteria}")
                emit(at, "reopened", lines)

            elif etype == "objective.closed":
                oid = e.get("oid")
                h = history_by_oid.get(oid) or history_by_closed.get(e.get("closed"))
                if h is not None:
                    objective = h.get("objective", "")
                    criteria = h.get("success_criteria", "")
                    note = e.get("note", h.get("note", ""))
                    started = e.get("started", h.get("started", ""))
                    turns = e.get("turns_count", h.get("turns"))
                else:
                    objective = (active or {}).get("objective", "")
                    criteria = (active or {}).get("success_criteria", "")
                    note = e.get("note", "")
                    started = e.get("started", "")
                    turns = e.get("turns_count")
                closed_at = e.get("closed", at)
                status = e.get("status", "")
                status_label = {"achieved": "成功", "failed": "失敗"}.get(status, status or "結案")
                lines = [
                    "- {ts} {label}（{actor}，{turns} 輪，{start} → {end}）　{obj}".format(
                        ts=_fmt_dt(closed_at), label=status_label, actor=_actor_label(e.get("closed_by")),
                        turns=turns if turns is not None else "?", start=_hm(started),
                        end=_hm(closed_at), obj=objective,
                    )
                ]
                if note:
                    lines.append(f"  備註：{note}")
                if oid and started:
                    summary = _segments_summary_line(self.objective_segments(session_id, oid, started, closed_at))
                    if summary:
                        lines.append(f"  進行 / 擱置：{summary}")
                emit(closed_at, "closed", lines)
                flush_pending_promote()
                active = None

            elif etype == "objective.parked_deleted":
                oid = e.get("oid")
                h = history_by_oid.get(oid)
                if h is not None:
                    objective = h.get("objective", "")
                else:
                    objective = parked_map.get(oid, {}).get("objective", "")
                emit(at, "parked_deleted",
                     [f"- {_fmt_dt(at)} 移除（{_actor_label(e.get('actor'))}）　{objective}"])

            elif etype == "objective.amended":
                oid = e.get("oid")
                to = e.get("to", "")
                target = e.get("target", "")
                objective = e.get("objective", "")
                if to == "parked" and oid:
                    parked_map[oid] = {"objective": objective, "success_criteria": ""}
                    if target == "history":
                        active = None
                elif to == "active":
                    active = {"objective": objective, "success_criteria": ""}
                elif to in ("achieved", "failed") and target == "parked" and oid:
                    parked_map.pop(oid, None)
                emit(at, "amended", _render_amended(e))

        flush_pending_promote()

        # -- the conversation itself: archive, rename, project -------------
        for e in events.read_events(self.root):
            if e.get("session_id") != session_id or e.get("type") not in _SESSION_LOG_TYPES:
                continue
            at, etype, who = e.get("at", ""), e["type"], _actor_label(e.get("actor"))
            if etype == "session.nickname_changed":
                text = f"改名（{who}）{e.get('before') or '（無）'} → {e.get('after') or '（無）'}"
            elif etype == "session.project_changed":
                text = f"改專案（{who}）{e.get('before') or '（無）'} → {e.get('after') or '（無）'}"
            elif etype == "rapport.session_enabled_changed":
                before_label = _rapport_enabled_label(e.get("before"))
                after_label = _rapport_enabled_label(e.get("after"))
                text = f"Rapport 注入：{before_label} → {after_label}（{who}）"
            else:
                text = f"{_SESSION_LOG_TYPES[etype]}（{who}）"
            emit(at, "session", [f"- {_fmt_dt(at)} {text}"])

        # -- Rapport as it was at each submission (changes only) ------------
        preset_names = {p["id"]: p["name"] for p in RapportModule(self.root).list_presets()}
        turn_events = [e for e in events.read_events(self.root, type="turn")
                       if e.get("session_id") == session_id]
        first_logged = turn_events[0].get("at", "") if turn_events else None
        points = [
            {"at": t.get("at", ""), "preset": t.get("preset"), "level": t.get("level"),
             "name": t.get("name", ""), "source": None, "sent": t.get("sent", True),
             "enable_source": None}
            for t in session.get("turns", [])
            if first_logged is None or t.get("at", "") < first_logged
        ]
        points += [
            {"at": e.get("at", ""), "preset": e.get("preset"), "level": e.get("level"),
             "name": e.get("level_name", ""), "source": e.get("rapport_source"),
             "sent": e.get("rapport_sent", True), "enable_source": e.get("rapport_enabled_source")}
            for e in turn_events
        ]
        # A change is a different preset/level, a different known source
        # (older per-turn records don't carry the source), or a different
        # "sent" state (whether the Rapport switch was actually on).
        last_level, last_source, last_sent = None, None, None
        for p in points:
            level = (p["preset"], p["level"])
            source_changed = p["source"] is not None and last_source is not None and p["source"] != last_source
            sent_changed = last_sent is not None and p["sent"] != last_sent
            if level != last_level or source_changed or sent_changed:
                label = "Rapport 起始" if last_level is None else "Rapport 變更"
                detail = f"{preset_names.get(p['preset'], p['preset'])} L{p['level']} {p['name']}"
                extras = []
                src_label = {"session": "個別設定", "default": "跟隨預設"}.get(p["source"])
                if src_label:
                    extras.append(src_label)
                enable_label = {"session": "個別設定", "global": "跟隨全域"}.get(p["enable_source"])
                if enable_label and enable_label not in extras:
                    extras.append(enable_label)
                if not p["sent"]:
                    extras.append("未送出")
                suffix = f"（{'，'.join(extras)}）" if extras else ""
                emit(p["at"], "rapport", [f"- {_fmt_dt(p['at'])} {label}　{detail}{suffix}"])
            last_level = level
            if p["source"] is not None:
                last_source = p["source"]
            last_sent = p["sent"]

        # -- this conversation's rest spans, as single lines ----------------
        for r in self._rest_spans(session_id):
            dur = _format_duration(r["start"], r["end"])
            end_label = _hm(r["end"]) if r["end"] else "現在"
            line = f"- {_fmt_dt(r['start'])} → {end_label} 休息（{dur}）"
            if r.get("auto_resumed"):
                line += "（自動恢復）"
            emit(r["start"], "rest", [line])

        # `seq` reflects processing order: reconstructed (pre-event) entries
        # first in history order, then events.jsonl entries in file order,
        # with "objective.promoted" deliberately re-sequenced (above) to
        # come right after the "objective.closed" that triggered it -- i.e.
        # narrative order, robust to same-second timestamp ties that a
        # string timestamp comparison alone can't resolve.
        records.sort(key=lambda r: (r["at"], r["seq"]))

        if range_start is not None:
            def _in_range(at):
                if not at:
                    return False
                if at < range_start:
                    return False
                if range_end is not None and at >= range_end:
                    return False
                return True
            records = [r for r in records if _in_range(r["at"])]

        out_lines = []
        title = session.get("nickname") or f"{Path(session.get('project', '')).name or '（未知專案）'} · {session_id[:8]}"
        if range_title:
            out_lines.append(f"# 工作紀錄 — {title}（{range_title}）")
        else:
            out_lines.append(f"# 工作紀錄 — {title}")
        out_lines.append(
            f"Session：{session_id}｜Agent：{session.get('agent', '')}｜專案：{session.get('project', '')}"
        )
        out_lines.append("")
        for r in records:
            out_lines.extend(r["lines"])

        out_lines.append("")
        out_lines.append("## 目前")
        if session.get("status") == "active":
            out_lines.append(f"進行中：{session.get('objective', '')}（{_fmt_dt(session.get('started', ''))} 起）")
            if session.get("success_criteria"):
                out_lines.append(f"標準：{session['success_criteria']}")
            active_oid, active_started = session.get("oid"), session.get("started", "")
            if active_oid and active_started:
                summary = _segments_summary_line(self.objective_segments(session_id, active_oid, active_started, None))
                if summary:
                    out_lines.append(f"進行 / 擱置：{summary}")
        else:
            out_lines.append("進行中：尚未設定")
        parked = session.get("parked") or []
        if parked:
            out_lines.append("擱置：")
            for i, bundle in enumerate(parked, 1):
                out_lines.append(f"{i}. {bundle.get('objective', '')}")

        return "\n".join(out_lines).rstrip("\n") + "\n"


def _hm(iso: str) -> str:
    return _fmt_dt(iso)[-5:] if iso else "？"


def _fmt_time_with_date(iso: str, last_date):
    """'2026-09-18T18:33:00' -> ('09-18 18:33', '09-18') the first time a
    date is seen, or ('18:33', last_date) when it matches the date last
    shown -- same-day segments in a 進行/擱置 summary line then show time
    only. Never raises."""
    if not iso:
        return "", last_date
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except Exception:
        return str(iso)[:16], last_date
    date_s, time_s = dt.strftime("%m-%d"), dt.strftime("%H:%M")
    if date_s != last_date:
        return f"{date_s} {time_s}", date_s
    return time_s, last_date


def _merge_adjacent_segments(segments):
    """Merge chronologically adjacent same-type segments (end of one ==
    start of the next)."""
    merged = []
    for s in segments:
        if merged and merged[-1]["type"] == s["type"] and merged[-1]["end"] == s["start"]:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    return merged


# Sentinel used so "open-ended" (None) start/end timestamps still compare
# correctly against real ISO-8601 timestamps with plain string ops.
_FAR_FUTURE = "9999-12-31T23:59:59"


def _split_segment_by_rest(seg, rest_spans):
    """Split one {"type":"進行", start, end} segment on every rest span
    that overlaps it, inserting {"type":"休息", ...} segments in between."""
    start = seg["start"]
    end = seg["end"] if seg["end"] is not None else _FAR_FUTURE
    cur = start
    out = []
    for r in rest_spans:
        r_start = r["start"]
        r_end = r["end"] if r["end"] is not None else _FAR_FUTURE
        if r_end <= cur or r_start >= end:
            continue
        ov_start = max(r_start, cur)
        ov_end = min(r_end, end)
        if ov_start >= ov_end:
            continue
        if ov_start > cur:
            out.append({"type": "進行", "start": cur, "end": ov_start})
        out.append({"type": "休息", "start": ov_start, "end": None if ov_end == _FAR_FUTURE else ov_end})
        cur = ov_end
        if cur >= end:
            break
    if cur < end:
        out.append({"type": "進行", "start": cur, "end": None if end == _FAR_FUTURE else end})
    return [s for s in out if s["start"] != s["end"]]


def _format_duration(start: str, end: str = None) -> str:
    """'2026-09-18T22:10:00' -> now/end -> '11 小時 5 分'. Never raises."""
    try:
        s = datetime.datetime.fromisoformat(start)
        e = datetime.datetime.fromisoformat(end) if end else datetime.datetime.now()
        total_minutes = max(0, int((e - s).total_seconds() // 60))
        h, m = divmod(total_minutes, 60)
        if h and m:
            return f"{h} 小時 {m} 分"
        if h:
            return f"{h} 小時"
        return f"{m} 分"
    except Exception:
        return "？"


def _segments_summary_line(segments) -> str:
    """{type,start,end} list -> '09-18 18:33–18:49 進行 · 18:49–18:53 擱置 ·
    18:53– 進行' (an open end renders as an empty end, i.e. a trailing
    dash). Empty input -> ''."""
    if not segments:
        return ""
    parts = []
    last_date = None
    for seg in segments:
        start_s, last_date = _fmt_time_with_date(seg.get("start"), last_date)
        if seg.get("end"):
            end_s, last_date = _fmt_time_with_date(seg.get("end"), last_date)
        else:
            end_s = ""
        parts.append(f"{start_s}–{end_s} {seg.get('type', '')}")
    return " · ".join(parts)


def _render_edited(e: dict):
    before = e.get("before") or {}
    after = e.get("after") or {}
    reason = e.get("reason") or e.get("note", "")
    header = f"- {_fmt_dt(e.get('at', ''))} 修改（{_actor_label(e.get('actor'))}）"
    if reason:
        header += f"理由：{reason}"
    lines = [header]
    if before.get("objective", "") != after.get("objective", ""):
        lines.append(f"  目標：{before.get('objective', '')} → {after.get('objective', '')}")
    if before.get("success_criteria", "") != after.get("success_criteria", ""):
        lines.append(f"  標準：{before.get('success_criteria', '')} → {after.get('success_criteria', '')}")
    return lines


def _render_amended(e: dict):
    to = e.get("to", "")
    target = e.get("target", "")
    before = e.get("before") or {}
    objective = e.get("objective", "")
    reason = e.get("reason", "")
    note = e.get("note")
    if target == "history" and to in ("achieved", "failed"):
        before_label = _status_label_cn(before.get("status", ""))
    elif target == "history":
        before_label = "已結案"
    else:  # target == "parked"
        before_label = "擱置"
    after_label = _AMEND_TO_LABELS.get(to, to)
    header = f"- {_fmt_dt(e.get('at', ''))} 更正（{_actor_label(e.get('actor'))}）{before_label} → {after_label}　{objective}"
    lines = [header]
    if reason:
        lines.append(f"  理由：{reason}")
    if note:
        lines.append(f"  備註：{note}")
    return lines
