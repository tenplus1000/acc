"""Shared UserPromptSubmit hook logic for agent adapters.

Contract: never let a hook failure block the user's message. Any exception
is logged to <root>/logs/hook.log; stdout stays empty and the process exits
0 regardless of what went wrong.
"""

import datetime
import json
import sys

from acc import core, events, store
from acc.modules.goals import GoalsModule


def _log_error(root, message: str) -> None:
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(root / "logs" / "hook.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def run_hook(agent_name: str) -> None:
    root = None
    try:
        root = core.get_root()
    except Exception:
        # Cannot even resolve/seed the data root; nothing we can do safely.
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}

        hook_event_name = payload.get("hook_event_name")
        if hook_event_name is not None and hook_event_name != "UserPromptSubmit":
            sys.exit(0)

        session_id = payload.get("session_id") or ""
        cwd = payload.get("cwd") or "."
        prompt = payload.get("prompt") or ""

        events.check_external_edits(root)

        effective_session_id = session_id
        if session_id:
            try:
                store.sanitize_session_id(session_id)
                goals = GoalsModule(root)
                goals.touch_session(session_id, agent_name, cwd, prompt)
            except Exception as exc:
                _log_error(root, f"touch_session failed: {exc!r}")
                effective_session_id = ""

        context = core.build_context(effective_session_id, cwd, agent_name, record=True)
        if context:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            }
            sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        _log_error(root, f"hook failed ({agent_name}): {exc!r}")
        sys.exit(0)
