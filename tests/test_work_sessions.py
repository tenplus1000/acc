import datetime
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
from acc.modules.goals import GoalsModule
from tests.helpers import run_cli, run_hook

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "acc" / "web" / "index.html"


def _fake_clock():
    state = {"t": datetime.datetime(2026, 1, 1, 0, 0, 0)}

    def _now():
        state["t"] += datetime.timedelta(seconds=1)
        return state["t"].isoformat(timespec="seconds")

    return _now


def patch_clock(testcase):
    """Same pattern as tests/test_objective_segments.py: shared fake clock
    for both goals.py's and events.py's `_now_iso`."""
    clock = _fake_clock()
    p1 = mock.patch("acc.modules.goals._now_iso", side_effect=clock)
    p2 = mock.patch("acc.events._now_iso", side_effect=clock)
    p1.start()
    p2.start()
    testcase.addCleanup(p1.stop)
    testcase.addCleanup(p2.stop)


def _make_root():
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    store.ensure_seeded(root)
    # The idle-hours cutoff is computed against real wall-clock time (see
    # GoalsModule._sessions_eligible_for_rest); the fake clock above starts
    # at 2026-01-01, which would otherwise look "idle" against whatever the
    # real today is. A huge idle_hours keeps every session eligible for
    # rest recording in these tests, same trick used across the suite.
    cfg = store.read_json(root / "config.json", {})
    cfg["idle_hours"] = 1_000_000
    store.atomic_write_json(root / "config.json", cfg)
    return tmp, root


class WorkStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _make_root()
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_seed_on_first_read(self):
        state = self.goals.work_status()
        self.assertTrue(state["working"])
        self.assertEqual(state["index"], 1)
        self.assertIn("since", state)

    def test_stop_then_start_increments_index(self):
        g = self.goals
        g.work_status()  # seed
        st = g.work_stop(actor="user")
        self.assertFalse(st["working"])
        st2 = g.work_start(actor="user")
        self.assertTrue(st2["working"])
        self.assertEqual(st2["index"], 2)

    def test_double_stop_raises(self):
        g = self.goals
        g.work_status()
        g.work_stop(actor="user")
        with self.assertRaises(Exception):
            g.work_stop(actor="user")

    def test_double_start_raises(self):
        g = self.goals
        with self.assertRaises(Exception):
            g.work_start(actor="user")

    def test_rest_started_for_non_idle_non_archived_sessions_only(self):
        from acc import events

        g = self.goals
        g.touch_session("active1", "claude", "/tmp", "hi")
        g.touch_session("active2", "claude", "/tmp", "hi")
        g.touch_session("to-archive", "claude", "/tmp", "hi")
        g.archive_session("to-archive")

        # idle_hours=6 against *real* wall-clock time (the idle cutoff isn't
        # driven by the fake clock): keep active1 fresh (real "now"),
        # backdate active2 well past the cutoff.
        cfg = store.read_json(self.root / "config.json", {})
        cfg["idle_hours"] = 6
        store.atomic_write_json(self.root / "config.json", cfg)
        real_now = datetime.datetime.now().isoformat(timespec="seconds")
        s1 = g.load_session("active1")
        s1["last_seen"] = real_now
        g.save_session(s1)
        s2 = g.load_session("active2")
        s2["last_seen"] = "2000-01-01T00:00:00"
        g.save_session(s2)

        g.work_status()  # seed working=true
        g.work_stop(actor="user")

        started = events.read_events(self.root, type="rest.started")
        sids = {e["session_id"] for e in started}
        self.assertIn("active1", sids)
        self.assertNotIn("active2", sids)  # idle
        self.assertNotIn("to-archive", sids)  # archived

    def test_rest_ended_on_resume_even_if_session_went_idle(self):
        g = self.goals
        g.touch_session("s1", "claude", "/tmp", "hi")
        g.work_status()
        g.work_stop(actor="user")
        # session goes idle while resting (well past any real idle_hours
        # cutoff -- rest.ended must still be written for it on resume).
        cfg = store.read_json(self.root / "config.json", {})
        cfg["idle_hours"] = 6
        store.atomic_write_json(self.root / "config.json", cfg)
        s1 = g.load_session("s1")
        s1["last_seen"] = "2000-01-01T00:00:00"
        g.save_session(s1)
        g.work_start(actor="user")

        from acc import events
        ends = [e for e in events.read_events(self.root, type="rest.ended") if e["session_id"] == "s1"]
        self.assertEqual(len(ends), 1)

    def test_auto_resume_on_hook_turn(self):
        g = self.goals
        g.touch_session("s1", "claude", "/tmp", "hi")
        g.work_stop(actor="user")
        self.assertFalse(g.work_status()["working"])

        g.touch_session("s1", "claude", "/tmp", "second prompt")
        st = g.work_status()
        self.assertTrue(st["working"])
        self.assertEqual(st["index"], 2)

        from acc import events
        starts = [e for e in events.read_events(self.root, type="work.started") if e.get("index") == 2]
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0]["auto"])
        self.assertEqual(starts[0]["actor"], "hook")

        ends = [e for e in events.read_events(self.root, type="rest.ended") if e["session_id"] == "s1"]
        self.assertEqual(len(ends), 1)
        self.assertTrue(ends[0].get("auto"))

    def test_auto_resume_does_not_change_injected_context(self):
        """The context built for the turn that triggers auto-resume must be
        byte-identical to the context from a turn where the switch was
        never touched -- the switch is recording-only."""
        from acc import core

        tmp_a, root_a = _make_root()
        tmp_b, root_b = _make_root()
        self.addCleanup(tmp_a.cleanup)
        self.addCleanup(tmp_b.cleanup)

        clock_a = _fake_clock()
        with mock.patch("acc.modules.goals._now_iso", side_effect=clock_a), \
             mock.patch("acc.events._now_iso", side_effect=clock_a), \
             mock.patch("acc.store.get_root", return_value=root_a):
            ga = GoalsModule(root_a)
            ga.objective_set("s1", "obj", "crit")
            ctx_a = core.build_context("s1", "/tmp", "claude", record=False)

        clock_b = _fake_clock()
        with mock.patch("acc.modules.goals._now_iso", side_effect=clock_b), \
             mock.patch("acc.events._now_iso", side_effect=clock_b), \
             mock.patch("acc.store.get_root", return_value=root_b):
            gb = GoalsModule(root_b)
            gb.objective_set("s1", "obj", "crit")
            gb.work_stop(actor="user")
            ctx_b = core.build_context("s1", "/tmp", "claude", record=False)

        self.assertEqual(ctx_a, ctx_b)


class WorkSessionsListTests(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _make_root()
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_index_zero_before_first_work_started(self):
        g = self.goals
        g.touch_session("s1", "claude", "/tmp", "hi")  # seeds work session #1
        sessions = g.work_sessions()
        self.assertEqual(sessions[0]["index"], 0)
        self.assertEqual(sessions[0]["label"], "紀錄開始前")
        self.assertIsNone(sessions[0]["start"])

    def test_numbering_across_stop_start(self):
        g = self.goals
        g.touch_session("s1", "claude", "/tmp", "hi")
        g.work_stop(actor="user")
        g.work_start(actor="user")
        indexes = [s["index"] for s in g.work_sessions()]
        self.assertEqual(indexes, [0, 1, 2])
        self.assertIsNone(g.work_sessions()[-1]["end"])

    def test_session_work_sessions_filters_to_activity(self):
        g = self.goals
        g.touch_session("s1", "claude", "/tmp", "hi")
        g.work_stop(actor="user")
        g.work_start(actor="user")
        g.touch_session("s1", "claude", "/tmp", "second")
        result = g.session_work_sessions("s1")
        indexes = [s["index"] for s in result]
        # s1 was active before the stop (index 0/1 span) and again after
        # resuming (index 2).
        self.assertIn(2, indexes)


class SegmentsRestSplitTests(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _make_root()
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_progress_run_split_by_rest(self):
        g, sid = self.goals, "s1"
        g.touch_session(sid, "claude", "/tmp", "hi")
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid, started = session["oid"], session["started"]

        g.work_stop(actor="user")
        g.work_start(actor="user")

        segs = g.objective_segments(sid, oid, started, None)
        self.assertEqual([s["type"] for s in segs], ["進行", "休息", "進行"])
        self.assertIsNone(segs[-1]["end"])
        self.assertEqual(segs[0]["end"], segs[1]["start"])
        self.assertEqual(segs[1]["end"], segs[2]["start"])

    def test_parked_span_not_split_by_rest(self):
        g, sid = self.goals, "s1"
        g.touch_session(sid, "claude", "/tmp", "hi")
        g.objective_set(sid, "甲", "c1")
        session = g.load_session(sid)
        oid, started = session["oid"], session["started"]

        g.park(sid, "先放著", actor="agent")
        g.work_stop(actor="user")
        g.work_start(actor="user")
        g.objective_set(sid, "乙", "c2")
        g.objective_done(sid, "完成乙")  # auto-promotes 甲

        segs = g.objective_segments(sid, oid, started, None)
        types = [s["type"] for s in segs]
        self.assertNotIn("休息", types)
        self.assertEqual(types, ["進行", "擱置", "進行"])


class WorkLogRangeTests(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _make_root()
        self.goals = GoalsModule(self.root)
        patch_clock(self)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rest_lines_rendered_with_duration(self):
        g, sid = self.goals, "s1"
        g.touch_session(sid, "claude", "/tmp", "hi")
        g.work_stop(actor="user")
        g.work_start(actor="user")
        log = g.work_log(sid)
        self.assertIn("休息", log)
        self.assertRegex(log, r"休息（\d+ 分）")

    def test_auto_resumed_rest_marked(self):
        g, sid = self.goals, "s1"
        g.touch_session(sid, "claude", "/tmp", "hi")
        g.work_stop(actor="user")
        g.touch_session(sid, "claude", "/tmp", "second")  # auto-resume
        log = g.work_log(sid)
        self.assertIn("（自動恢復）", log)

    def test_range_filters_and_titles(self):
        g, sid = self.goals, "s1"
        g.touch_session(sid, "claude", "/tmp", "hi")
        g.objective_set(sid, "甲", "c1")
        g.objective_done(sid, "完成甲")
        g.work_stop(actor="user")
        g.work_start(actor="user")
        g.objective_set(sid, "乙", "c2")

        full = g.work_log(sid)
        self.assertIn("甲", full)
        self.assertIn("乙", full)

        ranged = g.work_log(sid, work_from=2, work_to=2)
        self.assertIn("工作階段 #2", ranged.splitlines()[0])
        self.assertNotIn("設定　甲", ranged)
        self.assertIn("進行中：乙", ranged)  # 目前 section is always included

    def test_range_out_of_bounds_raises(self):
        g, sid = self.goals, "s1"
        g.touch_session(sid, "claude", "/tmp", "hi")
        with self.assertRaises(Exception):
            g.work_log(sid, work_from=99, work_to=99)


class WorkCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_start_stop(self):
        proc = run_cli(["work", "status"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("工作中", proc.stdout)

        proc = run_cli(["work", "stop"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("休息中", proc.stdout)

        proc = run_cli(["work", "status"], self.home)
        self.assertIn("休息中", proc.stdout)

        proc = run_cli(["work", "start"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("工作中", proc.stdout)

    def test_objective_log_latest_work(self):
        run_cli(["objective", "set", "obj1", "--session", "s1"], self.home)
        proc = run_cli(["objective", "log", "--session", "s1", "--latest-work"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("工作紀錄", proc.stdout)

    def test_objective_log_work_from_to(self):
        run_cli(["work", "status"], self.home)  # seed work session #1
        run_cli(["objective", "set", "obj1", "--session", "s1"], self.home)
        proc = run_cli(
            ["objective", "log", "--session", "s1", "--work-from", "1", "--work-to", "1"],
            self.home,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("工作階段 #1", proc.stdout.splitlines()[0])


class WorkApiTests(unittest.TestCase):
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

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_state_carries_work_block(self):
        state = self._get("/api/state")
        self.assertIn("work", state)
        self.assertIn("working", state["work"])
        self.assertIn("sessions", state["work"])

    def test_toggle_via_post(self):
        result = self._post("/api/work", {"working": False})
        self.assertFalse(result["work"]["working"])
        result = self._post("/api/work", {"working": True})
        self.assertTrue(result["work"]["working"])

    def test_session_view_has_work_sessions(self):
        from acc.core import get_root
        goals = GoalsModule(get_root())
        goals.objective_set("s1", "obj", "crit")
        state = self._get("/api/state")
        sessions = state["goals"]["sessions"] + state["goals"]["idle_sessions"]
        s = next(s for s in sessions if s["session_id"] == "s1")
        self.assertIn("work_sessions", s)

    def test_session_log_with_range_params(self):
        from acc.core import get_root
        goals = GoalsModule(get_root())
        goals.work_status()  # seed work session #1
        goals.objective_set("s1", "obj", "crit")
        data = self._get("/api/session/log?session=s1&work_from=1&work_to=1")
        self.assertIn("text", data)
        self.assertIn("工作階段 #1", data["text"].splitlines()[0])


class WorkUiMarkersTests(unittest.TestCase):
    def setUp(self):
        self.text = INDEX_HTML.read_text(encoding="utf-8")

    def test_toggle_strings_present(self):
        for marker in ("工作中", "休息中", "全部", "最新一次工作階段", "指定範圍"):
            self.assertIn(marker, self.text, f"missing UI string: {marker!r}")

    def test_no_prompt_alert_confirm_calls(self):
        self.assertNotIn("prompt(", self.text)
        self.assertNotIn("alert(", self.text)
        self.assertNotIn("confirm(", self.text)

    def test_extracted_script_is_valid_js(self):
        m = re.search(r"<script>(.*)</script>", self.text, re.S)
        self.assertIsNotNone(m)
        script_path = Path(tempfile.gettempdir()) / "acc_index_script_work_test.js"
        script_path.write_text(m.group(1), encoding="utf-8")
        try:
            proc = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True)
        except FileNotFoundError:
            self.skipTest("node is not available in this environment")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
