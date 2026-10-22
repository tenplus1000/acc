import json
import tempfile
import time
import unittest
from pathlib import Path

from acc import events, store
from acc.modules.goals import GoalsError, GoalsModule
from acc.modules.rapport import RapportModule


def read_events(root):
    path = Path(root) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class EventLogBasicsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_log_appends_one_json_line_with_at_type_actor(self):
        events.log(self.root, "turn", "hook", session_id="s1", foo="bar")
        lines = read_events(self.root)
        self.assertEqual(len(lines), 1)
        e = lines[0]
        self.assertEqual(e["type"], "turn")
        self.assertEqual(e["actor"], "hook")
        self.assertEqual(e["session_id"], "s1")
        self.assertEqual(e["foo"], "bar")
        self.assertIn("at", e)

    def test_logging_failure_does_not_raise(self):
        # Point root at something that can't hold a directory (a file),
        # so the append fails -- log() must swallow it, not propagate.
        bad_root = self.root / "not-a-dir"
        bad_root.write_text("x")
        try:
            events.log(bad_root, "turn", "hook")
        except Exception as exc:  # pragma: no cover - failure would show here
            self.fail(f"events.log raised: {exc!r}")

    def test_hook_failure_in_logging_does_not_break_hook(self):
        # touch_session must succeed even if the events file can't be
        # written -- simulate by making events.jsonl a directory.
        (self.root / "events.jsonl").mkdir()
        goals = GoalsModule(self.root)
        session = goals.touch_session("evt-fail", "claude", ".", "hi")
        self.assertEqual(session["session_id"], "evt-fail")


class TurnEventTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turn_logged_every_hook_call_even_when_unchanged(self):
        self.goals.touch_session("t1", "claude", ".", "a")
        self.goals.touch_session("t1", "claude", ".", "b")
        turns = [e for e in read_events(self.root) if e["type"] == "turn"]
        self.assertEqual(len(turns), 2)

    def test_turn_event_never_contains_prompt_text(self):
        self.goals.touch_session("t2", "claude", ".", "this is secret prompt text")
        turns = [e for e in read_events(self.root) if e["type"] == "turn"]
        self.assertEqual(turns[0]["prompt_chars"], len("this is secret prompt text"))
        dumped = json.dumps(turns[0])
        self.assertNotIn("secret prompt text", dumped)

    def test_turn_event_fields(self):
        self.goals.touch_session("t3", "claude", ".", "hi")
        turn = [e for e in read_events(self.root) if e["type"] == "turn"][0]
        for f in ("session_id", "agent", "project", "cwd", "preset", "level",
                  "level_name", "rapport_source", "objective", "objective_status",
                  "super_objective", "prompt_chars"):
            self.assertIn(f, turn)
        self.assertEqual(turn["rapport_source"], "default")
        self.assertIsNone(turn["objective"])
        self.assertEqual(turn["objective_status"], "unset")

    def test_turn_count_and_turns_at_start_and_history_turns(self):
        self.goals.touch_session("t4", "claude", ".", "a")
        self.goals.touch_session("t4", "claude", ".", "b")
        session = self.goals.objective_set("t4", "任務", "標準")
        self.assertEqual(session["turn_count"], 2)
        self.assertEqual(session["turns_at_start"], 2)

        self.goals.touch_session("t4", "claude", ".", "c")
        session = self.goals.objective_done("t4", "done")
        self.assertEqual(session["history"][0]["turns"], 1)
        self.assertNotIn("turns_at_start", session)

        closed = [e for e in read_events(self.root) if e["type"] == "objective.closed"][0]
        self.assertEqual(closed["turns_count"], 1)


class SuperSuggestionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_suggest_then_accept_updates_super_and_logs(self):
        self.goals.touch_session("sg1", "claude", ".", "hi")
        session = self.goals.load_session("sg1")
        project = session["project"]

        project_out, entry = self.goals.suggest_super("sg1", "新方向", "因為 X")
        self.assertEqual(project_out, project)
        self.assertEqual(entry["status"], "pending")

        suggested = [e for e in read_events(self.root) if e["type"] == "super.suggested"]
        self.assertEqual(len(suggested), 1)
        self.assertEqual(suggested[0]["actor"], "agent")

        accepted = self.goals.accept_suggestion(project, entry["id"])
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.goals.load_project(store.project_key(project))["super_objective"], "新方向")

        changed = [e for e in read_events(self.root) if e["type"] == "super.changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["actor"], "user")

        accepted_events = [e for e in read_events(self.root) if e["type"] == "super.suggestion_accepted"]
        self.assertEqual(len(accepted_events), 1)

    def test_reject_marks_rejected_and_does_not_change_super(self):
        self.goals.touch_session("sg2", "claude", ".", "hi")
        session = self.goals.load_session("sg2")
        project = session["project"]
        _, entry = self.goals.suggest_super("sg2", "另一個方向", "因為 Y")

        rejected = self.goals.reject_suggestion(project, entry["id"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.goals.load_project(store.project_key(project))["super_objective"], "")

        rejected_events = [e for e in read_events(self.root) if e["type"] == "super.suggestion_rejected"]
        self.assertEqual(len(rejected_events), 1)

    def test_agent_has_no_direct_super_cli(self):
        # There is no "acc super set" -- only "suggest" and "show".
        from acc.cli import build_parser

        parser = build_parser()
        sub_actions = [a for a in parser._subparsers._group_actions if a.dest == "command"]
        super_choices = sub_actions[0].choices["super"]
        super_sub_actions = [a for a in super_choices._subparsers._group_actions]
        commands = set(super_sub_actions[0].choices.keys())
        self.assertEqual(commands, {"suggest", "show"})

    def test_goals_context_carries_no_command_text(self):
        self.goals.touch_session("sg3", "claude", ".", "hi")
        self.assertNotIn("acc ", self.goals.build_context("sg3", "."))

    def test_goals_context_shows_pending_count(self):
        self.goals.touch_session("sg4", "claude", ".", "hi")
        session = self.goals.load_session("sg4")
        project = session["project"]
        self.goals.suggest_super("sg4", "方向一", "理由一")
        self.goals.suggest_super("sg4", "方向二", "理由二")
        ctx = self.goals.build_context("sg4", ".")
        self.assertIn("Super Objective 建議：2 則等待使用者決定", ctx)


class ExternalEditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_run_is_silent(self):
        events.check_external_edits(self.root)
        ext = [e for e in read_events(self.root) if e["type"] == "external_edit"]
        self.assertEqual(ext, [])
        self.assertTrue((self.root / ".snapshots.json").exists())

    def test_manual_edit_is_detected(self):
        events.check_external_edits(self.root)  # baseline
        path = self.root / "templates" / "wrapper.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n手動加的一行\n", encoding="utf-8")
        events.check_external_edits(self.root)
        ext = [e for e in read_events(self.root) if e["type"] == "external_edit"]
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0]["file"], "templates/wrapper.md")
        self.assertEqual(ext[0]["actor"], "hook")

    def test_acc_own_write_is_not_reported_as_external(self):
        events.check_external_edits(self.root)  # baseline
        self.goals.set_ultimate("新的 Ultimate Goal", "新的 Definition")
        events.check_external_edits(self.root)
        ext = [e for e in read_events(self.root) if e["type"] == "external_edit"]
        self.assertEqual(ext, [])

    def test_ultimate_external_edit_carries_before_after(self):
        events.check_external_edits(self.root)  # baseline
        path = self.root / "goals" / "ultimate.md"
        path.write_text("# Ultimate Goal\n\n外部改的目標\n\n# Ultimate Definition\n\n外部改的定義\n",
                         encoding="utf-8")
        events.check_external_edits(self.root)
        ext = [e for e in read_events(self.root) if e["type"] == "external_edit"]
        self.assertEqual(len(ext), 1)
        self.assertIn("外部改的目標", ext[0]["after"])
        self.assertIn("Ultimate Goal", ext[0]["before"])

    def test_hook_calls_external_edit_check(self):
        from tests.helpers import run_hook

        proc = run_hook("claude", {"session_id": "ee1", "cwd": ".", "prompt": "hi",
                                    "hook_event_name": "UserPromptSubmit"}, self.root)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue((self.root / ".snapshots.json").exists())


class OtherEventTypeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        self.rapport = RapportModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _types(self):
        return [e["type"] for e in read_events(self.root)]

    def test_rapport_default_changed_actor_user(self):
        self.rapport.set_level(5)
        e = [e for e in read_events(self.root) if e["type"] == "rapport.default_changed"][0]
        self.assertEqual(e["actor"], "user")
        self.assertEqual(e["after"]["level"], 5)

    def test_rapport_preset_lifecycle_events(self):
        pid = self.rapport.create_preset("測試 Preset")
        self.rapport.rename_preset(pid, "改名了")
        self.rapport.add_level(pid)
        self.rapport.rename_level(pid, 1, "新名字")
        self.rapport.set_level_prompt(pid, 1, "新 prompt")
        self.rapport.remove_last_level(pid)
        self.rapport.delete_preset(pid)
        types = self._types()
        for expected in (
            "rapport.preset_created", "rapport.preset_renamed", "rapport.level_added",
            "rapport.level_renamed", "rapport.level_prompt_changed", "rapport.level_removed",
            "rapport.preset_deleted",
        ):
            self.assertIn(expected, types)

    def test_rapport_session_set_and_cleared(self):
        self.goals.touch_session("rs1", "claude", ".", "hi")
        self.goals.set_session_rapport("rs1", "coding", 2)
        self.goals.clear_session_rapport("rs1")
        types = self._types()
        self.assertIn("rapport.session_set", types)
        self.assertIn("rapport.session_cleared", types)

    def test_objective_lifecycle_events_and_actors(self):
        self.goals.objective_set("ob1", "任務", "標準")
        self.goals.objective_done("ob1", "備註", closed_by="agent")
        self.goals.objective_set("ob1", "任務二", "標準二")
        self.goals.objective_fail("ob1", "備註二", closed_by="user")
        self.goals.reopen("ob1", 0)

        by_type = {}
        for e in read_events(self.root):
            by_type.setdefault(e["type"], []).append(e)
        self.assertEqual(by_type["objective.set"][0]["actor"], "agent")
        closes = by_type["objective.closed"]
        self.assertEqual(closes[0]["actor"], "agent")
        self.assertEqual(closes[1]["actor"], "user")
        self.assertEqual(by_type["objective.reopened"][0]["actor"], "user")

    def test_session_lifecycle_events(self):
        self.goals.touch_session("sl1", "claude", ".", "hi")
        self.goals.set_nickname("sl1", "暱稱")
        self.goals.archive_session("sl1")
        self.goals.unarchive_session("sl1")
        self.goals.touch_session("sl1", "claude", ".", "again")  # not archived, no revive
        self.goals.archive_session("sl1")
        self.goals.touch_session("sl1", "claude", ".", "third")  # hook auto-revives

        types = self._types()
        self.assertIn("session.created", types)
        self.assertIn("session.nickname_changed", types)
        self.assertIn("session.archived", types)
        self.assertIn("session.unarchived", types)
        self.assertIn("session.revived", types)
        revived = [e for e in read_events(self.root) if e["type"] == "session.revived"][0]
        self.assertEqual(revived["actor"], "hook")

    def test_ultimate_changed_only_on_actual_change(self):
        self.goals.set_ultimate("目標A", "定義A")
        self.goals.set_ultimate("目標A", "定義A")  # no-op, same text
        changed = [e for e in read_events(self.root) if e["type"] == "ultimate.changed"]
        # first call: two fields changed from seed defaults; second call: none
        first_call_count = len(changed)
        self.goals.set_ultimate("目標B", "定義A")
        changed2 = [e for e in read_events(self.root) if e["type"] == "ultimate.changed"]
        self.assertEqual(len(changed2), first_call_count + 1)
        self.assertEqual(changed2[-1]["field"], "goal")


if __name__ == "__main__":
    unittest.main()


class HistorySeedingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_project_history_seeds_initial_entry_when_no_events(self):
        self.goals.touch_session("hs1", "claude", ".", "hi")
        session = self.goals.load_session("hs1")
        project = session["project"]

        # Write the project file directly (as if it pre-dated events.jsonl),
        # bypassing set_project so no super.changed event exists.
        key = store.project_key(project)
        path = self.root / "goals" / "projects" / f"{key}.md"
        path.write_text(f"# Project\n{project}\n\n# Super Objective\n既有內容\n", encoding="utf-8")

        history = self.goals.super_history(project)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].get("seed"))
        self.assertEqual(history[0]["after"], "既有內容")

    def test_project_history_prefers_real_events_over_seed(self):
        self.goals.touch_session("hs2", "claude", ".", "hi")
        session = self.goals.load_session("hs2")
        project = session["project"]
        self.goals.set_project(project, "第一版")
        self.goals.set_project(project, "第二版")

        history = self.goals.super_history(project)
        self.assertEqual(len(history), 2)
        self.assertFalse(any(h.get("seed") for h in history))
        self.assertEqual(history[0]["after"], "第二版")  # newest first

    def test_ultimate_history_seeds_from_default_file(self):
        history = self.goals.ultimate_history("goal")
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].get("seed"))

    def test_ultimate_history_prefers_real_events(self):
        self.goals.set_ultimate("新目標", "新定義")
        history = self.goals.ultimate_history("goal")
        self.assertFalse(history[0].get("seed"))
        self.assertEqual(history[0]["after"], "新目標")

    def test_super_history_shows_external_edit(self):
        self.goals.touch_session("hs3", "claude", ".", "hi")
        session = self.goals.load_session("hs3")
        project = session["project"]
        self.goals.set_project(project, "原始版本")

        # simulate an external edit of the project file
        events.check_external_edits(self.root)
        key = store.project_key(project)
        path = self.root / "goals" / "projects" / f"{key}.md"
        path.write_text(f"# Project\n{project}\n\n# Super Objective\n外部改的\n", encoding="utf-8")
        events.check_external_edits(self.root)

        history = self.goals.super_history(project)
        self.assertEqual(history[0]["type"], "external_edit")
        self.assertEqual(history[1]["type"], "super.changed")

    def test_accept_via_suggestion_marked_in_history(self):
        self.goals.touch_session("hs4", "claude", ".", "hi")
        session = self.goals.load_session("hs4")
        project = session["project"]
        _, entry = self.goals.suggest_super("hs4", "建議的方向", "理由")
        self.goals.accept_suggestion(project, entry["id"])

        history = self.goals.super_history(project)
        self.assertEqual(history[0]["via"], "suggestion")


class ObjectiveEditsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_force_edit_while_active_appends_edit_entry(self):
        self.goals.objective_set("oe1", "原始任務", "原始標準")
        session = self.goals.force_edit_session("oe1", {"objective": "改過的任務"})
        self.assertEqual(len(session["edits"]), 1)
        edit = session["edits"][0]
        self.assertEqual(edit["actor"], "user")
        self.assertEqual(edit["before"]["objective"], "原始任務")
        self.assertEqual(edit["after"]["objective"], "改過的任務")
        self.assertEqual(edit["before"]["success_criteria"], "原始標準")

    def test_started_and_turns_at_start_unchanged_by_in_place_edit(self):
        session = self.goals.objective_set("oe2", "任務", "標準")
        started = session["started"]
        turns_at_start = session["turns_at_start"]

        time.sleep(1.1)
        session = self.goals.force_edit_session("oe2", {"objective": "改過的任務"})
        self.assertEqual(session["started"], started)
        self.assertEqual(session["turns_at_start"], turns_at_start)

    def test_edits_carried_to_history_on_close(self):
        self.goals.objective_set("oe3", "任務", "標準")
        self.goals.force_edit_session("oe3", {"objective": "改一次"})
        self.goals.force_edit_session("oe3", {"success_criteria": "改標準"})
        session = self.goals.objective_done("oe3", "完成")
        self.assertNotIn("edits", session)
        self.assertEqual(len(session["history"][0]["edits"]), 2)

    def test_no_edit_entry_when_field_unchanged(self):
        self.goals.objective_set("oe4", "任務", "標準")
        session = self.goals.force_edit_session("oe4", {"objective": "任務"})  # same value
        self.assertEqual(session.get("edits", []), [])

    def test_status_transition_to_active_still_resets_started(self):
        session = self.goals.force_edit_session("oe5", {"objective": "手動任務", "status": "active"})
        self.assertIn("started", session)
        self.assertEqual(session.get("edits", []), [])


if __name__ == "__main__":
    unittest.main()
