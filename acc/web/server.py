"""Local JSON API + static UI, standard library only."""

import datetime
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from acc import core
from acc.modules.goals import GoalsError, GoalsModule
from acc.modules.rapport import RapportError, RapportModule

INDEX_HTML_PATH = Path(__file__).parent / "index.html"


def _parse_iso(text: str):
    try:
        return datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _session_view(goals, rapport, s):
    override = s.get("rapport")
    eff = rapport.get_effective(override)
    enabled, enabled_source = rapport.effective_enabled(s)
    sv = dict(s)
    sv["rapport"] = override
    sv["rapport_effective"] = {
        "preset": eff["presetId"],
        "level": eff["level"],
        "name": eff["levelName"],
    }
    # rapport_enabled is the raw tri-state (True/False/None -> follows
    # global); rapport_enabled_effective/source is the resolved switch.
    sv["rapport_enabled"] = s.get("rapport_enabled")
    sv["rapport_enabled_effective"] = enabled
    sv["rapport_enabled_source"] = enabled_source

    # 進行/擱置 segments, event-derived.
    session_id = s.get("session_id", "")
    sv["history"] = [
        dict(
            h,
            segments=goals.objective_segments(
                session_id, h.get("oid"), h.get("started", ""), h.get("closed")
            ),
            amendments=goals.amendments_for_oid(session_id, h.get("oid")),
        )
        for h in s.get("history", [])
    ]
    sv["parked"] = [
        dict(b, amendments=goals.amendments_for_oid(session_id, b.get("oid")))
        for b in s.get("parked", [])
    ]
    if s.get("status") == "active":
        sv["segments"] = goals.objective_segments(
            session_id, s.get("oid"), s.get("started", ""), None
        )
    else:
        sv["segments"] = []
    sv["work_sessions"] = goals.session_work_sessions(session_id)
    return sv


def windows_path(root: Path):
    try:
        out = subprocess.run(
            ["wslpath", "-w", str(root)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def get_state(root: Path) -> dict:
    rapport = RapportModule(root)
    goals = GoalsModule(root)
    cfg = core.load_config(root)

    active = rapport.get_active()
    presets = rapport.list_presets()

    ult = goals.load_ultimate()
    projects = goals.list_projects_with_sessions()
    for p in projects:
        p["suggestions"] = goals.suggestions_view(p["path"])
        p["history"] = goals.super_history(p["path"])
    sessions = goals.list_sessions(limit=200)

    idle_hours = cfg.get("idle_hours", 6)
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=idle_hours)

    session_views = []
    idle_views = []
    for s in sessions:
        sv = _session_view(goals, rapport, s)
        last_seen = _parse_iso(s.get("last_seen", ""))
        if last_seen is not None and last_seen < cutoff:
            idle_views.append(sv)
        else:
            session_views.append(sv)

    archived_views = [_session_view(goals, rapport, s) for s in goals.list_archived_sessions(limit=50)]

    work_state = goals.work_status()

    return {
        "work": {
            "working": work_state.get("working", True),
            "since": work_state.get("since"),
            "index": work_state.get("index"),
            "sessions": goals.work_sessions(),
        },
        "rapport": {
            "presets": presets,
            "activePreset": active["presetId"],
            "level": active["level"],
            "enabled": rapport.globally_enabled(),
        },
        "goals": {
            "ultimate_goal": ult["ultimate_goal"],
            "ultimate_definition": ult["ultimate_definition"],
            "ultimate_goal_history": goals.ultimate_history("goal"),
            "ultimate_definition_history": goals.ultimate_history("definition"),
            "projects": projects,
            "sessions": session_views,
            "idle_sessions": idle_views,
            "idle_hours": idle_hours,
            "archived": archived_views,
        },
        "root": str(root),
        "windows_root": windows_path(root),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ACC/0.1"

    def log_message(self, fmt, *args):
        pass  # keep stdout quiet

    def _root(self) -> Path:
        return core.get_root()

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status=400):
        self._send_json({"error": message}, status=status)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        return json.loads(raw)

    # -- routing ---------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/" or path == "/index.html":
                html = INDEX_HTML_PATH.read_text(encoding="utf-8")
                self._send_html(html)
                return
            if path == "/api/state":
                self._send_json(get_state(self._root()))
                return
            if path == "/api/context":
                qs = parse_qs(parsed.query)
                session_id = (qs.get("session") or [""])[0]
                cwd = (qs.get("cwd") or ["."])[0]
                context = core.build_context(session_id, cwd, "web")
                self._send_json({"context": context})
                return
            if path == "/api/session/log":
                qs = parse_qs(parsed.query)
                session_id = (qs.get("session") or [""])[0]
                wf = qs.get("work_from")
                wt = qs.get("work_to")
                work_from = int(wf[0]) if wf else None
                work_to = int(wt[0]) if wt else None
                goals = GoalsModule(self._root())
                try:
                    text = goals.work_log(session_id, work_from=work_from, work_to=work_to)
                except GoalsError as exc:
                    self._error(str(exc), status=400)
                    return
                self._send_json({"text": text})
                return
            self._error("not found", status=404)
        except Exception as exc:
            self._error(str(exc), status=500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        root = self._root()
        try:
            data = self._read_json_body()
        except Exception:
            self._error("bad JSON body")
            return

        try:
            self._dispatch_post(root, path, data)
        except (RapportError, GoalsError, ValueError) as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(str(exc), status=500)

    def _dispatch_post(self, root: Path, path: str, data: dict):
        rapport = RapportModule(root)
        goals = GoalsModule(root)

        if path == "/api/rapport/select":
            rapport.select_preset(data["presetId"])
        elif path == "/api/rapport/level":
            rapport.set_level(int(data["level"]))
        elif path == "/api/rapport/preset/create":
            rapport.create_preset(data["name"])
        elif path == "/api/rapport/preset/rename":
            rapport.rename_preset(data["presetId"], data["name"])
        elif path == "/api/rapport/preset/delete":
            rapport.delete_preset(data["presetId"])
        elif path == "/api/rapport/level/add":
            rapport.add_level(data["presetId"])
        elif path == "/api/rapport/level/remove":
            rapport.remove_last_level(data["presetId"])
        elif path == "/api/rapport/level/rename":
            rapport.rename_level(data["presetId"], int(data["n"]), data["name"])
        elif path == "/api/rapport/level/prompt":
            rapport.set_level_prompt(data["presetId"], int(data["n"]), data["prompt"])
        elif path == "/api/goals/ultimate":
            goals.set_ultimate(data.get("goal", ""), data.get("definition", ""))
        elif path == "/api/goals/project":
            goals.set_project(data["path"], data.get("super_objective", ""))
        elif path == "/api/goals/session":
            goals.force_edit_session(data["sessionId"], data)
        elif path == "/api/goals/session/done":
            goals.objective_done(data["sessionId"], data.get("note", ""), closed_by="user")
        elif path == "/api/goals/session/fail":
            goals.objective_fail(data["sessionId"], data.get("note", ""), closed_by="user")
        elif path == "/api/goals/session/reopen":
            goals.reopen(data["sessionId"], int(data["index"]))
        elif path == "/api/goals/session/park":
            goals.park(data["sessionId"], data.get("reason", ""), actor="user")
        elif path == "/api/goals/session/parked/switch":
            goals.switch_parked(data["sessionId"], data["oid"], actor="user")
        elif path == "/api/goals/session/parked/reorder":
            goals.reorder_parked(data["sessionId"], data["oids"], actor="user")
        elif path == "/api/goals/session/parked/delete":
            goals.delete_parked(data["sessionId"], data["oid"], actor="user")
        elif path == "/api/goals/session/amend":
            goals.objective_amend(
                data["sessionId"], data["id"], data["to"], data.get("reason", ""),
                note=data.get("note"), actor="user",
            )
        elif path == "/api/goals/session/rapport":
            goals.set_session_rapport(data["sessionId"], data["preset"], int(data["level"]))
        elif path == "/api/goals/session/rapport/clear":
            goals.clear_session_rapport(data["sessionId"])
        elif path == "/api/config/rapport_enabled":
            rapport.set_globally_enabled(bool(data["enabled"]))
        elif path == "/api/goals/session/rapport_enabled":
            goals.set_session_rapport_enabled(data["sessionId"], data.get("value"))
        elif path == "/api/goals/session/archive":
            goals.archive_session(data["sessionId"])
        elif path == "/api/goals/session/unarchive":
            goals.unarchive_session(data["sessionId"])
        elif path == "/api/goals/session/nickname":
            goals.set_nickname(data["sessionId"], data.get("nickname", ""))
        elif path == "/api/goals/project/suggestion/accept":
            goals.accept_suggestion(data["path"], data["id"])
        elif path == "/api/goals/project/suggestion/reject":
            goals.reject_suggestion(data["path"], data["id"])
        elif path == "/api/work":
            if bool(data["working"]):
                goals.work_start(actor="user")
            else:
                goals.work_stop(actor="user")
        elif path == "/api/open":
            self._handle_open(root, data)
            return
        else:
            self._error("not found", status=404)
            return

        self._send_json(get_state(root))

    def _handle_open(self, root: Path, data: dict):
        rel = data.get("path", "")
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self._error("path escapes data root")
            return

        ok = True
        message = ""
        try:
            wpath = subprocess.run(
                ["wslpath", "-w", str(target)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if wpath.returncode != 0:
                raise RuntimeError(wpath.stderr.strip() or "wslpath failed")
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", wpath.stdout.strip()],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            ok = False
            message = str(exc)

        result = get_state(root)
        result["ok"] = ok
        result["message"] = message
        self._send_json(result)


def serve(host: str = "127.0.0.1", port=None):
    root = core.get_root()
    cfg = core.load_config(root)
    if port is None:
        port = cfg.get("port", 8765)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Agent Context Controller: http://{host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
