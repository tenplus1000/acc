import datetime
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from acc import store
from acc.modules.goals import GoalsError, GoalsModule
from acc.modules.rapport import RapportModule

REPO_ROOT = Path(__file__).resolve().parent.parent


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_archive_moves_file_and_hides_from_list(self):
        self.goals.touch_session("arc-1", "claude", ".", "hi")
        self.assertTrue((self.root / "goals" / "sessions" / "arc-1.json").exists())

        self.goals.archive_session("arc-1")
        self.assertFalse((self.root / "goals" / "sessions" / "arc-1.json").exists())
        self.assertTrue((self.root / "goals" / "sessions" / "archive" / "arc-1.json").exists())

        ids = [s["session_id"] for s in self.goals.list_sessions()]
        self.assertNotIn("arc-1", ids)

        archived_ids = [s["session_id"] for s in self.goals.list_archived_sessions()]
        self.assertIn("arc-1", archived_ids)

    def test_unarchive_moves_file_back(self):
        self.goals.touch_session("arc-2", "claude", ".", "hi")
        self.goals.archive_session("arc-2")
        self.goals.unarchive_session("arc-2")

        self.assertTrue((self.root / "goals" / "sessions" / "arc-2.json").exists())
        ids = [s["session_id"] for s in self.goals.list_sessions()]
        self.assertIn("arc-2", ids)

    def test_archive_nonexistent_errors(self):
        with self.assertRaises(GoalsError):
            self.goals.archive_session("no-such-session")

    def test_unarchive_nonexistent_errors(self):
        with self.assertRaises(GoalsError):
            self.goals.unarchive_session("no-such-session")

    def test_load_session_resolves_archived_read_only_without_moving(self):
        self.goals.touch_session("arc-3", "claude", ".", "hi")
        self.goals.archive_session("arc-3")

        session = self.goals.load_session("arc-3")
        self.assertIsNotNone(session)
        self.assertEqual(session["session_id"], "arc-3")
        # still archived -- a read-only lookup must not move it
        self.assertTrue((self.root / "goals" / "sessions" / "archive" / "arc-3.json").exists())
        self.assertFalse((self.root / "goals" / "sessions" / "arc-3.json").exists())

    def test_hook_auto_revives_archived_session_keeping_data(self):
        self.goals.touch_session("arc-4", "claude", ".", "hi")
        self.goals.set_nickname("arc-4", "我的暱稱")
        self.goals.set_session_rapport("arc-4", "coding", 2)
        self.goals.objective_set("arc-4", "目標", "標準")
        self.goals.archive_session("arc-4")

        session = self.goals.touch_session("arc-4", "claude", ".", "again")

        self.assertEqual(session["nickname"], "我的暱稱")
        self.assertEqual(session["rapport"], {"preset": "coding", "level": 2})
        self.assertEqual(session["objective"], "目標")
        self.assertIn("revived_at", session)
        self.assertTrue((self.root / "goals" / "sessions" / "arc-4.json").exists())
        self.assertFalse((self.root / "goals" / "sessions" / "archive" / "arc-4.json").exists())

    def test_explicit_unarchive_does_not_stamp_revived_at(self):
        self.goals.touch_session("arc-5", "claude", ".", "hi")
        self.goals.archive_session("arc-5")
        session = self.goals.unarchive_session("arc-5")
        self.assertNotIn("revived_at", session)

    def test_cli_write_unarchives(self):
        self.goals.objective_set("arc-6", "任務", "標準")
        self.goals.objective_done("arc-6")
        self.goals.archive_session("arc-6")

        session = self.goals.objective_set("arc-6", "新任務", "新標準")
        self.assertEqual(session["objective"], "新任務")
        self.assertTrue((self.root / "goals" / "sessions" / "arc-6.json").exists())


class IdleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_idle_hours_default_seeded(self):
        from acc import core

        cfg = core.load_config(self.root)
        self.assertEqual(cfg.get("idle_hours"), 6)

    def test_idle_hours_missing_in_old_config_defaults_to_six(self):
        cfg_path = self.root / "config.json"
        cfg = json.loads(cfg_path.read_text())
        del cfg["idle_hours"]
        cfg_path.write_text(json.dumps(cfg))

        from acc.web.server import get_state

        state = get_state(self.root)
        self.assertEqual(state["goals"]["idle_hours"], 6)

    def test_idle_classification_respects_config(self):
        self.goals.touch_session("idle-1", "claude", ".", "hi")
        session = self.goals.load_session("idle-1")
        old = (datetime.datetime.now() - datetime.timedelta(hours=10)).isoformat(timespec="seconds")
        session["last_seen"] = old
        self.goals.save_session(session)

        self.goals.touch_session("idle-2", "claude", ".", "hi")  # freshly touched

        from acc.web.server import get_state

        state = get_state(self.root)
        active_ids = [s["session_id"] for s in state["goals"]["sessions"]]
        idle_ids = [s["session_id"] for s in state["goals"]["idle_sessions"]]
        self.assertIn("idle-1", idle_ids)
        self.assertIn("idle-2", active_ids)

        # narrow idle_hours so idle-2 becomes idle too
        cfg_path = self.root / "config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["idle_hours"] = 0
        cfg_path.write_text(json.dumps(cfg))

        state2 = get_state(self.root)
        idle_ids2 = [s["session_id"] for s in state2["goals"]["idle_sessions"]]
        self.assertIn("idle-1", idle_ids2)
        self.assertIn("idle-2", idle_ids2)


class NicknameTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_and_clear_nickname(self):
        self.goals.touch_session("nick-1", "claude", ".", "hi")
        session = self.goals.set_nickname("nick-1", "我的暱稱")
        self.assertEqual(session["nickname"], "我的暱稱")

        session = self.goals.set_nickname("nick-1", "")
        self.assertNotIn("nickname", session)

    def test_set_nickname_nonexistent_errors(self):
        with self.assertRaises(GoalsError):
            self.goals.set_nickname("no-such-session", "x")


class OrderingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_order_by_created_unaffected_by_edits(self):
        self.goals.touch_session("ord-1", "claude", ".", "first")
        time.sleep(1.1)
        self.goals.touch_session("ord-2", "claude", ".", "second")
        time.sleep(1.1)
        self.goals.touch_session("ord-3", "claude", ".", "third")

        ids = [s["session_id"] for s in self.goals.list_sessions()]
        self.assertEqual(ids, ["ord-3", "ord-2", "ord-1"])

        # touching / editing the oldest session must not move it to the front
        self.goals.set_nickname("ord-1", "暱稱")
        self.goals.set_session_rapport("ord-1", "coding", 2)
        self.goals.touch_session("ord-1", "claude", ".", "again")

        ids2 = [s["session_id"] for s in self.goals.list_sessions()]
        self.assertEqual(ids2, ["ord-3", "ord-2", "ord-1"])


class TurnsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        self.rapport = RapportModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turns_dedupe_consecutive_equal(self):
        self.goals.touch_session("turn-1", "claude", ".", "a")
        self.goals.touch_session("turn-1", "claude", ".", "b")
        session = self.goals.load_session("turn-1")
        self.assertEqual(len(session["turns"]), 1)  # same default rapport both times

    def test_turns_record_new_entry_on_change(self):
        self.goals.touch_session("turn-2", "claude", ".", "a")
        self.rapport.set_level(5)
        self.goals.touch_session("turn-2", "claude", ".", "b")
        session = self.goals.load_session("turn-2")
        self.assertEqual(len(session["turns"]), 2)
        self.assertEqual(session["turns"][0]["level"], 3)
        self.assertEqual(session["turns"][1]["level"], 5)

    def test_turns_not_recorded_outside_hook_path(self):
        # objective_set / force_edit_session etc must never append turns.
        self.goals.objective_set("turn-3", "任務", "標準")
        session = self.goals.load_session("turn-3")
        self.assertEqual(session.get("turns", []), [])

    def test_turns_capped_at_500(self):
        session = self.goals.touch_session("turn-4", "claude", ".", "a")
        for i in range(600):
            self.rapport.set_level(2 if i % 2 == 0 else 4)
            session = self.goals.touch_session("turn-4", "claude", ".", f"msg{i}")
        self.assertLessEqual(len(session["turns"]), 500)


class StartedClosedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_started_recorded_on_set_and_carried_to_history_on_close(self):
        session = self.goals.objective_set("sc-1", "任務", "標準")
        self.assertIn("started", session)
        started = session["started"]

        session = self.goals.objective_done("sc-1", "備註")
        self.assertNotIn("started", session)
        self.assertEqual(session["history"][0]["started"], started)
        self.assertIn("closed", session["history"][0])

    def test_reopen_sets_fresh_started(self):
        self.goals.objective_set("sc-2", "任務", "標準")
        self.goals.objective_done("sc-2")
        session = self.goals.load_session("sc-2")
        old_started = session["history"][0]["started"]

        time.sleep(1.1)
        reopened = self.goals.reopen("sc-2", 0)
        self.assertIn("started", reopened)
        self.assertNotEqual(reopened["started"], "")

    def test_force_edit_setting_status_active_stamps_started(self):
        session = self.goals.force_edit_session(
            "sc-3", {"objective": "手動任務", "status": "active"}
        )
        self.assertIn("started", session)


class ProjectsFromSessionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_project_without_file_appears_from_session(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.goals.touch_session("proj-1", "claude", cwd, "hi")
            session = self.goals.load_session("proj-1")
            proj_path = session["project"]

            projects = self.goals.list_projects_with_sessions()
            row = next(p for p in projects if p["path"] == proj_path)
            self.assertFalse(row["has_file"])
            self.assertEqual(row["super_objective"], "")
            self.assertEqual(row["session_count"], 1)

    def test_saving_super_objective_creates_file(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.goals.touch_session("proj-2", "claude", cwd, "hi")
            session = self.goals.load_session("proj-2")
            proj_path = session["project"]

            row = next(p for p in self.goals.list_projects_with_sessions() if p["path"] == proj_path)
            self.assertFalse(row["has_file"])

            self.goals.set_project(proj_path, "我的 Super Objective")

            row2 = next(p for p in self.goals.list_projects_with_sessions() if p["path"] == proj_path)
            self.assertTrue(row2["has_file"])
            self.assertEqual(row2["super_objective"], "我的 Super Objective")

    def test_archived_session_project_still_counted(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.goals.touch_session("proj-3", "claude", cwd, "hi")
            session = self.goals.load_session("proj-3")
            proj_path = session["project"]
            self.goals.archive_session("proj-3")

            row = next(p for p in self.goals.list_projects_with_sessions() if p["path"] == proj_path)
            self.assertEqual(row["session_count"], 1)

    def test_projects_sorted_by_path(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            # force distinct, sortable paths
            paths = sorted([str(Path(a).resolve()), str(Path(b).resolve())])
            self.goals.touch_session("proj-sort-1", "claude", paths[1], "hi")
            self.goals.touch_session("proj-sort-2", "claude", paths[0], "hi")

            projects = self.goals.list_projects_with_sessions()
            result_paths = [p["path"] for p in projects if p["path"] in paths]
            self.assertEqual(result_paths, paths)


class WebSmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        import os

        os.environ["ACC_HOME"] = str(self.home)

        from acc.web.server import Handler

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        import os

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        del os.environ["ACC_HOME"]
        self._tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return json.loads(resp.read())

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_archive_unarchive_nickname_endpoints(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.touch_session("web-arc", "claude", ".", "hi")

        result = self._post("/api/goals/session/nickname", {"sessionId": "web-arc", "nickname": "測試"})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-arc")
        self.assertEqual(session["nickname"], "測試")

        result = self._post("/api/goals/session/archive", {"sessionId": "web-arc"})
        self.assertFalse(any(s["session_id"] == "web-arc" for s in result["goals"]["sessions"]))
        self.assertTrue(any(s["session_id"] == "web-arc" for s in result["goals"]["archived"]))

        result = self._post("/api/goals/session/unarchive", {"sessionId": "web-arc"})
        self.assertTrue(any(s["session_id"] == "web-arc" for s in result["goals"]["sessions"]))

    def test_state_includes_idle_hours_and_archived(self):
        state = self._get("/api/state")
        self.assertIn("idle_hours", state["goals"])
        self.assertIn("archived", state["goals"])
        self.assertIn("idle_sessions", state["goals"])

    def test_state_projects_include_fileless_session_project(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        with tempfile.TemporaryDirectory() as cwd:
            goals.touch_session("web-proj", "claude", cwd, "hi")
            session = goals.load_session("web-proj")
            proj_path = session["project"]

            state = self._get("/api/state")
            row = next(p for p in state["goals"]["projects"] if p["path"] == proj_path)
            self.assertFalse(row["has_file"])
            self.assertEqual(row["session_count"], 1)


class IndexHtmlNoBlockingDialogsTests(unittest.TestCase):
    def test_no_prompt_alert_confirm_calls(self):
        html_path = REPO_ROOT / "acc" / "web" / "index.html"
        text = html_path.read_text(encoding="utf-8")
        self.assertNotIn("prompt(", text)
        self.assertNotIn("alert(", text)
        self.assertNotIn("confirm(", text)

    def test_collapsible_sections_and_save_feedback_present(self):
        html_path = REPO_ROOT / "acc" / "web" / "index.html"
        text = html_path.read_text(encoding="utf-8")
        for marker in (
            "section-rapport", "section-ultimate", "section-projects", "section-sessions",
            "acc.collapse", "field-dirty", "未儲存", "儲存中…", "已儲存 ✓", "儲存失敗",
            "suggestion-item", "history-list", "採用", "不採用", "修改 ",
        ):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
