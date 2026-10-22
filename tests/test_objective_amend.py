import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from acc import store
from acc.modules.goals import GoalsError, GoalsModule
from tests.helpers import run_cli

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "acc" / "web" / "index.html"


def _fake_clock():
    import datetime
    state = {"t": datetime.datetime(2026, 1, 1, 0, 0, 0)}

    def _now():
        import datetime as dt
        state["t"] += dt.timedelta(seconds=1)
        return state["t"].isoformat(timespec="seconds")

    return _now


def patch_clock(testcase):
    clock = _fake_clock()
    p1 = mock.patch("acc.modules.goals._now_iso", side_effect=clock)
    p2 = mock.patch("acc.events._now_iso", side_effect=clock)
    p1.start()
    p2.start()
    testcase.addCleanup(p1.stop)
    testcase.addCleanup(p2.stop)


class ObjectiveListTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_unset_session_shows_placeholder(self):
        g, sid = self.goals, "s1"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        text = g.objective_list_text(sid)
        self.assertIn("擱置清單（0）", text)
        self.assertIn("已結案（最近 10 筆，新到舊）", text)
        self.assertIn("H1", text)
        self.assertIn("甲", text)

    def test_active_objective_shown(self):
        g, sid = self.goals, "s2"
        g.objective_set(sid, "乙", "c2")
        text = g.objective_list_text(sid)
        self.assertIn("進行中：乙", text)

    def test_parked_and_history_numbered(self):
        g, sid = self.goals, "s3"
        g.objective_set(sid, "甲", "c1")
        g.park(sid, "先放著", actor="agent")
        g.objective_set(sid, "乙", "c2")
        g.park(sid, "也放著", actor="agent")
        text = g.objective_list_text(sid)
        self.assertIn("P1", text)
        self.assertIn("P2", text)
        self.assertIn("甲", text)
        self.assertIn("乙", text)

    def test_oid_shown_when_present(self):
        g, sid = self.goals, "s4"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        g.objective_done(sid, "完成")
        text = g.objective_list_text(sid)
        self.assertIn(oid, text)

    def test_unknown_session_raises(self):
        with self.assertRaises(GoalsError):
            self.goals.objective_list_text("does-not-exist")


class ObjectiveAmendCoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_history_achieved_to_failed_keeps_metadata(self):
        g, sid = self.goals, "s1"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "本來以為成功")
        before = g.load_session(sid)["history"][-1]
        started, closed, turns, oid = before["started"], before["closed"], before["turns"], before["oid"]

        g.objective_amend(sid, "H1", "failed", "其實沒有做到", note="重新檢查後發現沒過", actor="agent")

        after = g.load_session(sid)["history"][-1]
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["note"], "重新檢查後發現沒過")
        self.assertEqual(after["started"], started)
        self.assertEqual(after["closed"], closed)
        self.assertEqual(after["turns"], turns)
        self.assertEqual(after["oid"], oid)

    def test_note_omitted_keeps_existing_note(self):
        g, sid = self.goals, "s2"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "原始備註")
        g.objective_amend(sid, "H1", "failed", "判斷錯了", actor="agent")
        after = g.load_session(sid)["history"][-1]
        self.assertEqual(after["note"], "原始備註")

    def test_history_to_parked_moves_to_end_of_queue(self):
        g, sid = self.goals, "s3"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        g.objective_set(sid, "乙", "c2")
        g.park(sid, "先放著", actor="agent")

        g.objective_amend(sid, "H1", "parked", "其實還沒做完", actor="agent")

        session = g.load_session(sid)
        self.assertEqual(session["history"], [])
        self.assertEqual(len(session["parked"]), 2)
        self.assertEqual(session["parked"][-1]["objective"], "甲")

    def test_history_to_active_restores_when_no_active(self):
        g, sid = self.goals, "s4"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        g.objective_done(sid, "完成")

        g.objective_amend(sid, "H1", "active", "其實沒完成", actor="agent")

        session = g.load_session(sid)
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["objective"], "甲")
        self.assertEqual(session["oid"], oid)
        self.assertEqual(session["history"], [])

    def test_history_to_active_blocked_when_active_exists(self):
        g, sid = self.goals, "s5"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        g.objective_set(sid, "乙", "c2")
        with self.assertRaises(GoalsError):
            g.objective_amend(sid, "H1", "active", "其實沒完成", actor="agent")

    def test_parked_to_achieved_appends_to_history(self):
        g, sid = self.goals, "s6"
        g.objective_set(sid, "甲", "c1")
        g.park(sid, "先放著", actor="agent")

        g.objective_amend(sid, "P1", "achieved", "其實已經做完了", note="使用者確認過了", actor="agent")

        session = g.load_session(sid)
        self.assertEqual(session["parked"], [])
        self.assertEqual(len(session["history"]), 1)
        h = session["history"][0]
        self.assertEqual(h["status"], "achieved")
        self.assertEqual(h["note"], "使用者確認過了")

    def test_parked_to_parked_is_noop_error(self):
        g, sid = self.goals, "s7"
        g.objective_set(sid, "甲", "c1")
        g.park(sid, "先放著", actor="agent")
        with self.assertRaises(GoalsError):
            g.objective_amend(sid, "P1", "parked", "理由", actor="agent")

    def test_active_objective_cannot_be_amended(self):
        g, sid = self.goals, "s8"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        with self.assertRaises(GoalsError):
            g.objective_amend(sid, oid, "failed", "理由", actor="agent")

    def test_unknown_id_lists_valid_ids(self):
        g, sid = self.goals, "s9"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        try:
            g.objective_amend(sid, "H99", "failed", "理由", actor="agent")
            self.fail("expected GoalsError")
        except GoalsError as exc:
            self.assertIn("H1", str(exc))

    def test_reason_required_for_agent(self):
        g, sid = self.goals, "s10"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        with self.assertRaises(GoalsError):
            g.objective_amend(sid, "H1", "failed", "", actor="agent")

    def test_reason_optional_for_user(self):
        g, sid = self.goals, "s11"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        # Should not raise even with an empty reason when actor="user".
        g.objective_amend(sid, "H1", "failed", "", actor="user")
        after = g.load_session(sid)["history"][-1]
        self.assertEqual(after["status"], "failed")

    def test_oid_accepted_in_place_of_ph_id(self):
        g, sid = self.goals, "s12"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        g.objective_done(sid, "完成")
        g.objective_amend(sid, oid, "failed", "理由", actor="agent")
        after = g.load_session(sid)["history"][-1]
        self.assertEqual(after["status"], "failed")

    def test_event_logged_with_before_after(self):
        from acc import events
        g, sid = self.goals, "s13"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        g.objective_amend(sid, "H1", "failed", "判斷錯了", note="重新看了一次", actor="agent")
        evs = [e for e in events.read_events(self.root, type="objective.amended") if e["session_id"] == sid]
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual(e["target"], "history")
        self.assertEqual(e["to"], "failed")
        self.assertEqual(e["before"]["status"], "achieved")
        self.assertEqual(e["after"]["status"], "failed")
        self.assertEqual(e["reason"], "判斷錯了")
        self.assertEqual(e["actor"], "agent")

    def test_amendments_for_oid(self):
        g, sid = self.goals, "s14"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        g.objective_done(sid, "完成")
        g.objective_amend(sid, "H1", "failed", "判斷錯了", actor="agent")
        amendments = g.amendments_for_oid(sid, oid)
        self.assertEqual(len(amendments), 1)
        self.assertEqual(amendments[0]["to"], "failed")

    def test_work_log_renders_amendment_lines(self):
        g, sid = self.goals, "s15"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        g.objective_amend(sid, "H1", "failed", "判斷錯了", actor="agent")
        log = g.work_log(sid)
        self.assertIn("更正", log)
        self.assertIn("成功 → 失敗", log)
        self.assertIn("理由：判斷錯了", log)

    def test_work_log_renders_move_to_parked(self):
        g, sid = self.goals, "s16"
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成")
        g.objective_amend(sid, "H1", "parked", "其實還沒做完", actor="agent")
        log = g.work_log(sid)
        self.assertIn("已結案 → 擱置", log)

    def test_objective_segments_still_computed_for_amended_entry(self):
        # A history->parked amend moves the bundle to `parked`; make sure
        # nothing downstream (segments computation) blows up on it once
        # it's later re-promoted and closed again.
        g, sid = self.goals, "s17"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        started = session["started"]
        g.objective_done(sid, "完成")
        g.objective_amend(sid, "H1", "parked", "其實還沒做完", actor="agent")
        # promote it back manually by setting+closing to trigger auto-promote
        g.objective_set(sid, "乙", "c2")
        g.objective_done(sid, "完成乙")  # auto-promotes 甲 back
        segs = g.objective_segments(sid, oid, started, None)
        self.assertTrue(segs)


class ObjectiveAmendCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_and_amend_roundtrip(self):
        run_cli(["objective", "set", "obj1", "--session", "s1"], self.home)
        run_cli(["objective", "done", "--session", "s1", "--note", "完成"], self.home)

        proc = run_cli(["objective", "list", "--session", "s1"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("H1", proc.stdout)

        proc = run_cli(
            ["objective", "amend", "--session", "s1", "--id", "H1", "--to", "failed", "--reason", "判斷錯了"],
            self.home,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("已更正", proc.stdout)

    def test_amend_without_reason_fails(self):
        run_cli(["objective", "set", "obj1", "--session", "s1"], self.home)
        run_cli(["objective", "done", "--session", "s1"], self.home)
        proc = run_cli(
            ["objective", "amend", "--session", "s1", "--id", "H1", "--to", "failed", "--reason", ""],
            self.home,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_list_unknown_session_errors(self):
        proc = run_cli(["objective", "list", "--session", "nope"], self.home)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.strip())


class ObjectiveAmendWebTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["ACC_HOME"] = str(self.home)

        from acc.web.server import Handler

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        del os.environ["ACC_HOME"]
        self._tmp.cleanup()

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_amend_endpoint_without_reason(self):
        from acc.core import get_root
        goals = GoalsModule(get_root())
        goals.objective_set("s1", "obj", "crit")
        goals.objective_done("s1", "done")

        data = self._post("/api/goals/session/amend", {"sessionId": "s1", "id": "H1", "to": "failed"})
        sessions = data["goals"]["sessions"] + data["goals"]["idle_sessions"]
        s = next(s for s in sessions if s["session_id"] == "s1")
        self.assertEqual(s["history"][-1]["status"], "failed")

    def test_session_view_carries_amendments(self):
        from acc.core import get_root
        goals = GoalsModule(get_root())
        goals.objective_set("s1", "obj", "crit")
        goals.objective_done("s1", "done")
        self._post("/api/goals/session/amend", {"sessionId": "s1", "id": "H1", "to": "failed", "reason": "理由"})

        data = self._post("/api/goals/session/amend", {"sessionId": "s1", "id": "H1", "to": "achieved"})
        sessions = data["goals"]["sessions"] + data["goals"]["idle_sessions"]
        s = next(s for s in sessions if s["session_id"] == "s1")
        self.assertIn("amendments", s["history"][-1])
        self.assertEqual(len(s["history"][-1]["amendments"]), 2)


class ObjectiveAmendUiMarkersTests(unittest.TestCase):
    def setUp(self):
        self.text = INDEX_HTML.read_text(encoding="utf-8")

    def test_amend_control_present(self):
        self.assertIn("更正", self.text)
        self.assertIn("/api/goals/session/amend", self.text)

    def test_no_prompt_alert_confirm_calls(self):
        self.assertNotIn("prompt(", self.text)
        self.assertNotIn("alert(", self.text)
        self.assertNotIn("confirm(", self.text)

    def test_extracted_script_is_valid_js(self):
        m = re.search(r"<script>(.*)</script>", self.text, re.S)
        self.assertIsNotNone(m)
        script_path = Path(tempfile.gettempdir()) / "acc_index_script_amend_test.js"
        script_path.write_text(m.group(1), encoding="utf-8")
        try:
            proc = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True)
        except FileNotFoundError:
            self.skipTest("node is not available in this environment")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
