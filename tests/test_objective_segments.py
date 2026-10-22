import datetime
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from acc import store
from acc.modules.goals import GoalsModule

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "acc" / "web" / "index.html"


def _fake_clock():
    """A monotonic fake `_now_iso` advancing 1 second per call -- real
    operations in these tests run well under a second apart, and
    objective_segments' zero-length-segment dropping would otherwise
    collapse everything into a single second-precision timestamp."""
    state = {"t": datetime.datetime(2026, 1, 1, 0, 0, 0)}

    def _now():
        state["t"] += datetime.timedelta(seconds=1)
        return state["t"].isoformat(timespec="seconds")

    return _now


def patch_clock(testcase):
    """Patches both `_now_iso` implementations (goals.py's own, and
    events.py's, used when it timestamps each logged event) onto one
    shared fake clock, and registers cleanup on `testcase`."""
    clock = _fake_clock()
    p1 = mock.patch("acc.modules.goals._now_iso", side_effect=clock)
    p2 = mock.patch("acc.events._now_iso", side_effect=clock)
    p1.start()
    p2.start()
    testcase.addCleanup(p1.stop)
    testcase.addCleanup(p2.stop)


class ObjectiveSegmentsCoreTests(unittest.TestCase):
    """`GoalsModule.objective_segments` replays events.jsonl directly --
    these exercise the real case that motivated the change: park then an
    auto-promote back, with no session["turns"] entry in between (the
    turn-derived segments used to miss this entirely)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_park_then_auto_promote_with_no_turn_in_between(self):
        g, sid = self.goals, "s1"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid = session["oid"]
        started = session["started"]

        g.park(sid, "先放著", actor="agent")
        g.objective_set(sid, "乙", "c2")
        g.objective_done(sid, "完成乙")  # closes 乙, auto-promotes 甲 back

        segs = g.objective_segments(sid, oid, started, None)
        self.assertEqual([s["type"] for s in segs], ["進行", "擱置", "進行"])
        self.assertEqual(segs[0]["start"], started)
        self.assertIsNone(segs[-1]["end"])
        # No zero-length segments, and boundaries chain (end of one ==
        # start of the next).
        for s in segs:
            self.assertNotEqual(s["start"], s["end"])
        self.assertEqual(segs[0]["end"], segs[1]["start"])
        self.assertEqual(segs[1]["end"], segs[2]["start"])

    def test_switch_produces_segments_for_both_objectives(self):
        g, sid = self.goals, "s2"
        g.objective_set(sid, "甲", "c1")
        s1 = g.load_session(sid)
        oid_a, started_a = s1["oid"], s1["started"]

        g.park(sid, "放著", actor="agent")
        g.objective_set(sid, "乙", "c2")
        s2 = g.load_session(sid)
        oid_b, started_b = s2["oid"], s2["started"]

        g.switch_parked(sid, oid_a, actor="user")  # 甲 active again, 乙 parked

        segs_a = g.objective_segments(sid, oid_a, started_a, None)
        segs_b = g.objective_segments(sid, oid_b, started_b, None)
        self.assertEqual([s["type"] for s in segs_a], ["進行", "擱置", "進行"])
        self.assertIsNone(segs_a[-1]["end"])
        # 乙 ends up parked (not closed) after the switch -- its trailing
        # 擱置 run is still open-ended, same as an in-progress 進行 run would be.
        self.assertEqual([s["type"] for s in segs_b], ["進行", "擱置"])
        self.assertIsNone(segs_b[-1]["end"])

    def test_closed_objective_last_segment_ends_at_closed(self):
        g, sid = self.goals, "s3"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid, started = session["oid"], session["started"]
        g.objective_done(sid, "完成")
        closed_session = g.load_session(sid)
        h = closed_session["history"][-1]
        segs = g.objective_segments(sid, oid, started, h["closed"])
        self.assertEqual(segs[-1]["end"], h["closed"])
        self.assertIsNotNone(segs[-1]["end"])

    def test_active_objective_last_segment_is_open_ended(self):
        g, sid = self.goals, "s4"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid, started = session["oid"], session["started"]
        segs = g.objective_segments(sid, oid, started, None)
        self.assertEqual(len(segs), 1)
        self.assertIsNone(segs[0]["end"])
        self.assertEqual(segs[0]["type"], "進行")

    def test_legacy_session_with_no_events_yields_one_segment(self):
        g, sid = self.goals, "legacy1"
        # No events.jsonl entries for this session at all -- an oid/started
        # pair with nothing matching in the event log.
        segs = g.objective_segments(sid, "deadbeef", "2026-01-01T00:00:00", "2026-01-01T00:10:00")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0], {
            "type": "進行", "start": "2026-01-01T00:00:00", "end": "2026-01-01T00:10:00",
        })

    def test_no_oid_falls_back_to_single_segment(self):
        g = self.goals
        segs = g.objective_segments("legacy2", None, "2026-01-01T00:00:00", "2026-01-01T00:05:00")
        self.assertEqual(segs, [{
            "type": "進行", "start": "2026-01-01T00:00:00", "end": "2026-01-01T00:05:00",
        }])

    def test_zero_length_segments_dropped(self):
        g, sid = self.goals, "s5"
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid, started = session["oid"], session["started"]
        # closed == started: a same-instant close. The single reconstructed
        # segment has start == end and must be dropped, not returned as a
        # zero-length "進行" run.
        segs = g.objective_segments(sid, oid, started, started)
        self.assertEqual(segs, [])


class ObjectiveSegmentsApiStateTests(unittest.TestCase):
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

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return json.loads(resp.read())

    def test_state_carries_segments_for_active_and_history(self):
        from acc.core import get_root
        goals = GoalsModule(get_root())
        patch_clock(self)

        goals.objective_set("api1", "甲", "c1")
        goals.objective_done("api1", "完成")
        goals.objective_set("api1", "乙", "c2")  # now active

        data = self._get("/api/state")
        sessions = data["goals"]["sessions"] + data["goals"]["idle_sessions"]
        s = next(s for s in sessions if s["session_id"] == "api1")

        self.assertIn("segments", s)
        self.assertEqual(len(s["segments"]), 1)
        self.assertIsNone(s["segments"][0]["end"])

        self.assertEqual(len(s["history"]), 1)
        self.assertIn("segments", s["history"][0])
        self.assertEqual(len(s["history"][0]["segments"]), 1)
        self.assertIsNotNone(s["history"][0]["segments"][0]["end"])


class WorkLogSegmentsSummaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_work_log_contains_summary_line_for_closed_objective_with_park(self):
        g, sid = self.goals, "wl-seg-1"
        g.objective_set(sid, "甲", "c1")
        g.park(sid, "放著", actor="agent")
        g.objective_set(sid, "乙", "c2")
        g.objective_done(sid, "完成乙")  # auto-promotes 甲
        g.objective_done(sid, "完成甲")

        log = g.work_log(sid)
        self.assertIn("進行 / 擱置：", log)
        lines = [l for l in log.splitlines() if "進行 / 擱置：" in l]
        self.assertTrue(lines, log)
        for l in lines:
            self.assertIn("進行", l)
            self.assertIn("擱置", l)

    def test_work_log_current_section_has_summary_for_active_objective(self):
        g, sid = self.goals, "wl-seg-2"
        g.objective_set(sid, "甲", "c1")
        g.park(sid, "放著", actor="agent")
        g.objective_set(sid, "乙", "c2")
        g.objective_done(sid, "完成乙")  # auto-promotes 甲 -> active again

        log = g.work_log(sid)
        tail = log[log.index("## 目前"):]
        self.assertIn("進行 / 擱置：", tail)


class ObjectiveSegmentsUiMarkersTests(unittest.TestCase):
    """UI/JS-only pieces can't be unit tested through the Python runner --
    check that buildOidSegments (turn-derived) is gone, the new copy is
    used, no native dialogs are used, and the extracted <script> is still
    syntactically valid JS."""

    def setUp(self):
        self.text = INDEX_HTML.read_text(encoding="utf-8")

    def test_build_oid_segments_removed(self):
        self.assertNotIn("buildOidSegments", self.text)

    def test_segments_field_used(self):
        self.assertIn(".segments", self.text)
        self.assertIn("entry.segments", self.text)

    def test_required_ui_strings_present(self):
        for marker in ("一直進行中，沒有被擱置過", "沒有被擱置過", "進行 / 擱置"):
            self.assertIn(marker, self.text, f"missing UI string: {marker!r}")

    def test_no_prompt_alert_confirm_calls(self):
        self.assertNotIn("prompt(", self.text)
        self.assertNotIn("alert(", self.text)
        self.assertNotIn("confirm(", self.text)

    def test_extracted_script_is_valid_js(self):
        import re
        import subprocess

        m = re.search(r"<script>(.*)</script>", self.text, re.S)
        self.assertIsNotNone(m)
        script_path = Path(tempfile.gettempdir()) / "acc_index_script_segments_test.js"
        script_path.write_text(m.group(1), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["node", "--check", str(script_path)],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            self.skipTest("node is not available in this environment")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
