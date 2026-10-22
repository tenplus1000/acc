import json
import tempfile
import unittest
from pathlib import Path

from acc import core, store
from acc.modules.goals import GoalsModule
from acc.modules.rapport import RapportModule


def read_events(root):
    path = Path(root) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class RapportEnabledContextTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.rapport = RapportModule(self.root)
        self.goals = GoalsModule(self.root)

        import os

        os.environ["ACC_HOME"] = str(self.root)

    def tearDown(self):
        import os

        del os.environ["ACC_HOME"]
        self._tmp.cleanup()

    def test_global_off_removes_rapport_block_keeps_goals(self):
        self.rapport.set_globally_enabled(False)
        self.goals.touch_session("s1", "claude", ".", "hi")
        ctx = core.build_context("s1", ".", "claude", record=True)
        self.assertNotIn("<Rapport", ctx)
        self.assertIn("<Goals>", ctx)

    def test_session_false_overrides_global_true(self):
        self.assertTrue(self.rapport.globally_enabled())
        self.goals.touch_session("s2", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("s2", False)
        ctx = core.build_context("s2", ".", "claude", record=True)
        self.assertNotIn("<Rapport", ctx)

    def test_session_true_overrides_global_false(self):
        self.rapport.set_globally_enabled(False)
        self.goals.touch_session("s3", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("s3", True)
        ctx = core.build_context("s3", ".", "claude", record=True)
        self.assertIn("<Rapport", ctx)

    def test_null_clears_session_override_back_to_global(self):
        self.goals.touch_session("s4", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("s4", False)
        session = self.goals.load_session("s4")
        self.assertEqual(session["rapport_enabled"], False)

        self.goals.set_session_rapport_enabled("s4", None)
        session = self.goals.load_session("s4")
        self.assertNotIn("rapport_enabled", session)

        enabled, source = self.rapport.effective_enabled(session)
        self.assertTrue(enabled)
        self.assertEqual(source, "global")

    def test_preview_is_read_only(self):
        self.rapport.set_globally_enabled(False)
        self.goals.touch_session("s5", "claude", ".", "hi")
        before = self.goals.load_session("s5")
        core.build_context("s5", ".", "claude", record=False)  # preview
        after = self.goals.load_session("s5")
        self.assertEqual(before, after)


class TurnEventAndTimelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.rapport = RapportModule(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turn_event_has_rapport_sent_and_source(self):
        self.goals.touch_session("t1", "claude", ".", "hi")
        events = [e for e in read_events(self.root) if e["type"] == "turn"]
        self.assertEqual(len(events), 1)
        self.assertIn("rapport_sent", events[0])
        self.assertIn("rapport_enabled_source", events[0])
        self.assertTrue(events[0]["rapport_sent"])
        self.assertEqual(events[0]["rapport_enabled_source"], "global")

    def test_turn_event_reflects_session_override(self):
        self.goals.touch_session("t2", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("t2", False)
        self.goals.touch_session("t2", "claude", ".", "again")
        events = [e for e in read_events(self.root) if e["type"] == "turn"]
        self.assertFalse(events[-1]["rapport_sent"])
        self.assertEqual(events[-1]["rapport_enabled_source"], "session")

    def test_turns_dedupe_includes_sent(self):
        self.goals.touch_session("t3", "claude", ".", "p1")
        self.goals.set_session_rapport_enabled("t3", False)
        self.goals.touch_session("t3", "claude", ".", "p2")
        self.goals.touch_session("t3", "claude", ".", "p3")  # still off, no dup
        session = self.goals.load_session("t3")
        turns = session["turns"]
        # p1 (sent) then p2 (not sent) are two distinct entries; p3 (still
        # not sent, unchanged preset/level/oid) does not add a third.
        self.assertEqual(len(turns), 2)
        self.assertTrue(turns[0]["sent"])
        self.assertFalse(turns[1]["sent"])

    def test_preset_level_still_recorded_when_not_sent(self):
        self.goals.touch_session("t4", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("t4", False)
        self.goals.touch_session("t4", "claude", ".", "again")
        session = self.goals.load_session("t4")
        last = session["turns"][-1]
        self.assertFalse(last["sent"])
        self.assertIn("preset", last)
        self.assertIsNotNone(last["preset"])
        self.assertIn("level", last)
        self.assertIsNotNone(last["level"])


class WorkLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.rapport = RapportModule(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_work_log_marks_not_sent_and_session_switch_line(self):
        self.goals.touch_session("w1", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("w1", False)
        self.goals.touch_session("w1", "claude", ".", "again")
        self.goals.set_session_rapport_enabled("w1", None)

        log = self.goals.work_log("w1")
        self.assertIn("未送出", log)
        self.assertIn("Rapport 注入：", log)
        self.assertIn("使用者", log)

    def test_global_enabled_changed_not_listed_in_work_log(self):
        self.goals.touch_session("w2", "claude", ".", "hi")
        self.rapport.set_globally_enabled(False)
        log = self.goals.work_log("w2")
        # The global toggle is not session-specific; the per-turn 未送出
        # marks already reflect its effect, so it shouldn't appear as a
        # session-log line of its own.
        self.assertNotIn("rapport.enabled_changed", log)


class EventLoggingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.rapport = RapportModule(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_global_toggle_logs_event_and_persists(self):
        self.rapport.set_globally_enabled(False)
        cfg = store.read_json(self.root / "config.json", None)
        self.assertFalse(cfg["modules"]["rapport"])

        evs = [e for e in read_events(self.root) if e["type"] == "rapport.enabled_changed"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["before"], True)
        self.assertEqual(evs[0]["after"], False)
        self.assertEqual(evs[0]["actor"], "user")
        self.assertNotIn("session_id", evs[0])

    def test_session_toggle_logs_event(self):
        self.goals.touch_session("e1", "claude", ".", "hi")
        self.goals.set_session_rapport_enabled("e1", True)
        evs = [e for e in read_events(self.root) if e["type"] == "rapport.session_enabled_changed"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["session_id"], "e1")
        self.assertIsNone(evs[0]["before"])
        self.assertEqual(evs[0]["after"], True)


if __name__ == "__main__":
    unittest.main()
