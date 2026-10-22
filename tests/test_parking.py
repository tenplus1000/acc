import json
import tempfile
import unittest
from pathlib import Path

from acc import store
from acc.modules.goals import GoalsError, GoalsModule
from tests.helpers import run_cli


def read_events(root):
    path = Path(root) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ParkQueueModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_park_moves_bundle_to_end_and_leaves_unset_without_promote(self):
        self.goals.objective_set("p1", "甲", "c1")
        session = self.goals.load_session("p1")
        oid = session["oid"]
        parked = self.goals.park("p1", "使用者要我先做別的", actor="agent")
        self.assertEqual(parked["status"], "unset")
        self.assertEqual(parked["objective"], "")
        self.assertNotIn("oid", parked)
        self.assertEqual(len(parked["parked"]), 1)
        self.assertEqual(parked["parked"][0]["oid"], oid)
        self.assertEqual(parked["parked"][0]["objective"], "甲")

        events = [e for e in read_events(self.root) if e["type"] == "objective.parked"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["oid"], oid)

    def test_second_park_goes_after_first(self):
        self.goals.objective_set("p2", "甲", "c1")
        self.goals.park("p2", "先擱著", actor="agent")
        self.goals.objective_set("p2", "乙", "c2")
        session = self.goals.park("p2", "也先擱著", actor="agent")
        self.assertEqual(len(session["parked"]), 2)
        self.assertEqual(session["parked"][0]["objective"], "甲")
        self.assertEqual(session["parked"][1]["objective"], "乙")

    def test_park_requires_active(self):
        with self.assertRaises(GoalsError):
            self.goals.park("no-such", "reason", actor="agent")

    def test_parked_never_appears_in_hook_context(self):
        self.goals.objective_set("p3", "甲", "c1")
        self.goals.park("p3", "先擱著", actor="agent")
        ctx = self.goals.build_context("p3", str(self.root))
        self.assertNotIn("甲", ctx)
        self.assertNotIn("擱置", ctx)

    def test_done_auto_promotes_first_parked(self):
        self.goals.objective_set("p4", "甲", "c1")
        self.goals.park("p4", "先擱著", actor="agent")
        self.goals.objective_set("p4", "乙", "c2")
        self.goals.objective_set("p4", "乙", "c2") if False else None
        result = self.goals.objective_done("p4", "完成了")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["objective"], "甲")
        self.assertEqual(len(result["parked"]), 0)
        promoted = [e for e in read_events(self.root) if e["type"] == "objective.promoted"]
        self.assertEqual(len(promoted), 1)

    def test_fail_auto_promotes_first_parked_web(self):
        self.goals.objective_set("p5", "甲", "c1")
        self.goals.park("p5", "先擱著", actor="agent")
        self.goals.objective_set("p5", "乙", "c2")
        result = self.goals.objective_fail("p5", "沒做到", closed_by="user")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["objective"], "甲")

    def test_close_without_parked_does_not_promote(self):
        self.goals.objective_set("p6", "甲", "c1")
        result = self.goals.objective_done("p6", "完成了")
        self.assertEqual(result["status"], "unset")

    def test_cli_done_prints_auto_promote_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(["objective", "set", "--session", "s1", "甲", "--criteria", "c1"], tmp)
            run_cli(["objective", "park", "--session", "s1", "--reason", "先擱著"], tmp)
            run_cli(["objective", "set", "--session", "s1", "乙", "--criteria", "c2"], tmp)
            proc = run_cli(["objective", "done", "--session", "s1", "--note", "done"], tmp)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("已自動接回：甲", proc.stdout)

    def test_cli_fail_prints_auto_promote_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(["objective", "set", "--session", "s2", "甲", "--criteria", "c1"], tmp)
            run_cli(["objective", "park", "--session", "s2", "--reason", "先擱著"], tmp)
            run_cli(["objective", "set", "--session", "s2", "乙", "--criteria", "c2"], tmp)
            proc = run_cli(["objective", "fail", "--session", "s2", "--note", "fail"], tmp)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("已自動接回：甲", proc.stdout)

    def test_cli_park_requires_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(["objective", "set", "--session", "s3", "甲", "--criteria", "c1"], tmp)
            proc = run_cli(["objective", "park", "--session", "s3"], tmp)
            self.assertNotEqual(proc.returncode, 0)

    def test_switch_swaps_positions_exactly(self):
        self.goals.objective_set("sw1", "甲", "c1")
        self.goals.park("sw1", "先擱著", actor="agent")
        self.goals.objective_set("sw1", "乙", "c2")
        self.goals.park("sw1", "也先擱著", actor="agent")
        self.goals.objective_set("sw1", "丙", "c3")
        session = self.goals.load_session("sw1")
        oid_甲 = session["parked"][0]["oid"]
        oid_乙 = session["parked"][1]["oid"]
        oid_丙 = session["oid"]

        result = self.goals.switch_parked("sw1", oid_乙, actor="user")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["objective"], "乙")
        self.assertEqual(result["oid"], oid_乙)
        self.assertEqual(len(result["parked"]), 2)
        self.assertEqual(result["parked"][0]["oid"], oid_甲)
        self.assertEqual(result["parked"][1]["oid"], oid_丙)
        self.assertEqual(result["parked"][1]["objective"], "丙")

    def test_switch_with_no_active_promotes(self):
        self.goals.objective_set("sw2", "甲", "c1")
        self.goals.park("sw2", "先擱著", actor="agent")
        session = self.goals.load_session("sw2")
        oid = session["parked"][0]["oid"]
        result = self.goals.switch_parked("sw2", oid, actor="user")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["objective"], "甲")
        self.assertEqual(len(result["parked"]), 0)

    def test_switch_unknown_oid_errors(self):
        self.goals.objective_set("sw3", "甲", "c1")
        with self.assertRaises(GoalsError):
            self.goals.switch_parked("sw3", "nonexistent", actor="user")

    def test_reorder_validates_permutation(self):
        self.goals.objective_set("ro1", "甲", "c1")
        self.goals.park("ro1", "r1", actor="agent")
        self.goals.objective_set("ro1", "乙", "c2")
        self.goals.park("ro1", "r2", actor="agent")
        self.goals.objective_set("ro1", "丙", "c3")
        self.goals.park("ro1", "r3", actor="agent")
        session = self.goals.load_session("ro1")
        oids = [b["oid"] for b in session["parked"]]
        reordered = list(reversed(oids))
        result = self.goals.reorder_parked("ro1", reordered, actor="user")
        self.assertEqual([b["oid"] for b in result["parked"]], reordered)

        with self.assertRaises(GoalsError):
            self.goals.reorder_parked("ro1", oids[:-1], actor="user")
        with self.assertRaises(GoalsError):
            self.goals.reorder_parked("ro1", oids + ["bogus"], actor="user")

    def test_delete_parked_moves_to_history_dropped(self):
        self.goals.objective_set("del1", "甲", "c1")
        self.goals.park("del1", "擱著", actor="agent")
        session = self.goals.load_session("del1")
        oid = session["parked"][0]["oid"]
        result = self.goals.delete_parked("del1", oid, actor="user")
        self.assertEqual(len(result["parked"]), 0)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["status"], "dropped")
        self.assertEqual(result["history"][0]["closed_by"], "user")
        self.assertEqual(result["history"][0]["oid"], oid)


class TurnOidTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turns_carry_oid(self):
        self.goals.touch_session("t1", "claude", str(self.root), "hi")
        self.goals.objective_set("t1", "甲", "c1")
        self.goals.touch_session("t1", "claude", str(self.root), "again")
        session = self.goals.load_session("t1")
        oid = session["oid"]
        turns = session["turns"]
        self.assertIsNone(turns[0]["oid"])
        self.assertEqual(turns[-1]["oid"], oid)

    def test_turn_counts_per_oid_across_park_switch(self):
        self.goals.objective_set("t2", "甲", "c1")
        session = self.goals.load_session("t2")
        oid_a = session["oid"]
        self.goals.touch_session("t2", "claude", str(self.root), "1")
        self.goals.touch_session("t2", "claude", str(self.root), "2")
        # 2 turns while active on oid_a
        self.goals.park("t2", "先擱著", actor="agent")
        # turns while parked must not count
        self.goals.touch_session("t2", "claude", str(self.root), "3")
        self.goals.touch_session("t2", "claude", str(self.root), "4")
        self.goals.objective_set("t2", "乙", "c2")
        self.goals.touch_session("t2", "claude", str(self.root), "5")

        session = self.goals.load_session("t2")
        counts = session["turn_counts_by_oid"]
        self.assertEqual(counts[oid_a], 2)
        self.assertEqual(counts[session["oid"]], 1)

        result = self.goals.objective_done("t2", "done")
        # history entry for 乙 has turns == 1
        entry_乙 = next(h for h in result["history"] if h["objective"] == "乙")
        self.assertEqual(entry_乙["turns"], 1)
        # auto-promoted 甲 is active again; its turns_at_start preserved from park
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["objective"], "甲")


if __name__ == "__main__":
    unittest.main()
