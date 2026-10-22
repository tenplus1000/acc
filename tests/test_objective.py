import tempfile
import unittest
from pathlib import Path

from tests.helpers import run_cli


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_then_web_force_edit_changes_it(self):
        run_cli(["objective", "set", "完成 Claude adapter", "--criteria", "通過測試",
                 "--session", "sess-web"], self.home)

        from acc.core import get_root
        from acc.modules.goals import GoalsModule
        import os

        os.environ["ACC_HOME"] = str(self.home)
        goals = GoalsModule(get_root())
        goals.force_edit_session("sess-web", {"objective": "完成 Codex adapter"})

        from tests.helpers import run_hook, parse_hook_context

        proc = run_hook("claude", {"session_id": "sess-web", "cwd": ".", "prompt": "go",
                                    "hook_event_name": "UserPromptSubmit"}, self.home)
        ctx = parse_hook_context(proc)
        self.assertIn("完成 Codex adapter", ctx)
        del os.environ["ACC_HOME"]

    def test_set_while_active_errors(self):
        proc1 = run_cli(["objective", "set", "第一件事", "--criteria", "做完",
                          "--session", "sess-dup"], self.home)
        self.assertEqual(proc1.returncode, 0)

        proc2 = run_cli(["objective", "set", "第二件事", "--criteria", "做完",
                          "--session", "sess-dup"], self.home)
        self.assertEqual(proc2.returncode, 1)
        self.assertIn("先用 done／fail 結案，或用 park 擱置", proc2.stderr)

    def test_done_moves_to_history_and_resets(self):
        run_cli(["objective", "set", "任務一", "--criteria", "標準一",
                 "--session", "sess-done"], self.home)
        proc = run_cli(["objective", "done", "--note", "驗證過了",
                         "--session", "sess-done"], self.home)
        self.assertEqual(proc.returncode, 0)

        from acc.core import get_root
        from acc.modules.goals import GoalsModule
        import os

        os.environ["ACC_HOME"] = str(self.home)
        goals = GoalsModule(get_root())
        session = goals.load_session("sess-done")
        self.assertEqual(session["status"], "unset")
        self.assertEqual(session["objective"], "")
        self.assertEqual(len(session["history"]), 1)
        self.assertEqual(session["history"][0]["status"], "achieved")
        self.assertEqual(session["history"][0]["note"], "驗證過了")
        del os.environ["ACC_HOME"]

    def test_fail_without_active_objective_errors(self):
        proc = run_cli(["objective", "fail", "--session", "sess-none"], self.home)
        self.assertEqual(proc.returncode, 1)

    def test_closed_by_agent_via_cli(self):
        run_cli(["objective", "set", "任務二", "--criteria", "標準二",
                 "--session", "sess-cli-close"], self.home)
        run_cli(["objective", "done", "--note", "done via cli",
                 "--session", "sess-cli-close"], self.home)

        from acc.core import get_root
        from acc.modules.goals import GoalsModule
        import os

        os.environ["ACC_HOME"] = str(self.home)
        try:
            goals = GoalsModule(get_root())
            session = goals.load_session("sess-cli-close")
            self.assertEqual(session["history"][0]["closed_by"], "agent")
        finally:
            del os.environ["ACC_HOME"]

    def test_closed_by_user_via_web(self):
        from acc.core import get_root
        from acc.modules.goals import GoalsModule
        import os

        os.environ["ACC_HOME"] = str(self.home)
        try:
            goals = GoalsModule(get_root())
            goals.objective_set("sess-web-close", "任務三", "標準三")
            goals.objective_done("sess-web-close", "done via web", closed_by="user")
            session = goals.load_session("sess-web-close")
            self.assertEqual(session["history"][0]["closed_by"], "user")
        finally:
            del os.environ["ACC_HOME"]

    def test_reopen_when_unset_succeeds_and_refuses_when_active(self):
        from acc.core import get_root
        from acc.modules.goals import GoalsError, GoalsModule
        import os

        os.environ["ACC_HOME"] = str(self.home)
        try:
            goals = GoalsModule(get_root())
            goals.objective_set("sess-reopen", "任務四", "標準四")
            goals.objective_done("sess-reopen", "驗證過", closed_by="user")

            session = goals.load_session("sess-reopen")
            self.assertEqual(session["status"], "unset")

            reopened = goals.reopen("sess-reopen", 0)
            self.assertEqual(reopened["status"], "active")
            self.assertEqual(reopened["objective"], "任務四")
            self.assertEqual(reopened["success_criteria"], "標準四")
            self.assertEqual(len(reopened["history"]), 0)

            with self.assertRaises(GoalsError):
                goals.reopen("sess-reopen", 0)
        finally:
            del os.environ["ACC_HOME"]

    def test_rules_cover_every_command_and_state_has_none(self):
        from acc import defaults

        for cmd in ("objective set", "objective done", "objective fail", "objective park",
                    "objective revise", "super suggest"):
            self.assertIn(f"acc {cmd} --session <session>", defaults.RULES_MD)
        self.assertIn("已自動接回", defaults.RULES_MD)
        for tmpl in (defaults.WRAPPER_MD, defaults.GOALS_MD, defaults.OBJECTIVE_ACTIVE_MD,
                     defaults.OBJECTIVE_UNSET_MD, defaults.SUPER_PENDING_MD, defaults.ULTIMATE_UPDATED_MD):
            self.assertNotIn("acc ", tmpl)

    def test_context_names_session_and_rules_show_prints_editable_file(self):
        from tests.helpers import run_hook, parse_hook_context

        ctx = parse_hook_context(run_hook("claude", {"session_id": "tpl", "cwd": ".", "prompt": "x",
                                                     "hook_event_name": "UserPromptSubmit"}, self.home))
        self.assertIn('<AgentContext session="tpl">', ctx)
        (self.home / "templates" / "rules.md").write_text("自訂規則\n")
        out = run_cli(["rules", "show"], self.home)
        self.assertEqual(out.stdout.strip(), "自訂規則")

    def test_rules_install_replaces_only_managed_block(self):
        import json
        from acc.cli import install_rules
        from acc.modules.goals import GoalsModule, ultimate_hash

        target = self.home / "AGENTS.md"
        target.write_text("# 我自己的規則\n保留這行\n")
        install_rules("codex", self.home, target)
        install_rules("codex", self.home, target)
        text = target.read_text()
        self.assertEqual(text.count("<!-- acc:rules start -->"), 1)
        self.assertIn("保留這行", text)
        self.assertIn("acc objective park", text)
        # codex copies rules.md + ultimate.md in full (no import syntax)
        self.assertIn("# Ultimate Goal", text)
        self.assertIn("# Ultimate Definition", text)

        ult = GoalsModule(self.home).load_ultimate()
        expected_hash = ultimate_hash(ult["ultimate_goal"], ult["ultimate_definition"])
        installed = json.loads((self.home / ".rules_installed.json").read_text())
        self.assertEqual(installed["codex"]["ultimate_hash"], expected_hash)
        self.assertEqual(installed["codex"]["target"], str(target))

        claude = self.home / "CLAUDE.md"
        install_rules("claude", self.home, claude)
        claude_text = claude.read_text()
        self.assertIn(f"@{self.home / 'templates' / 'rules.md'}", claude_text)
        self.assertIn(f"@{self.home / 'goals' / 'ultimate.md'}", claude_text)
        installed = json.loads((self.home / ".rules_installed.json").read_text())
        self.assertEqual(installed["claude"]["ultimate_hash"], expected_hash)
        self.assertIn("codex", installed)  # earlier install still recorded

    def _cli(self, root, *args):
        return run_cli(list(args), root)

    def test_revise_keeps_started_and_records_reason(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._cli(tmp, "objective", "set", "--session", "rv", "我要甲", "--criteria", "c1")
            path = Path(tmp) / "goals" / "sessions" / "rv.json"
            started = json.loads(path.read_text())["started"]
            r = self._cli(tmp, "objective", "revise", "--session", "rv", "我要甲和乙",
                          "--reason", "使用者加了乙")
            self.assertEqual(r.returncode, 0, r.stderr)
            s = json.loads(path.read_text())
            self.assertEqual(s["objective"], "我要甲和乙")
            self.assertEqual(s["success_criteria"], "c1")
            self.assertEqual(s["started"], started)
            self.assertEqual(s["edits"][-1]["actor"], "agent")
            self.assertEqual(s["edits"][-1]["reason"], "使用者加了乙")
            events = (Path(tmp) / "events.jsonl").read_text()
            self.assertIn("使用者加了乙", events)

    def test_revise_requires_reason_and_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._cli(tmp, "objective", "revise", "--session", "rv2", "我要甲", "--reason", "x")
            self.assertEqual(r.returncode, 1)
            self._cli(tmp, "objective", "set", "--session", "rv2", "我要甲")
            r = self._cli(tmp, "objective", "revise", "--session", "rv2", "我要乙", "--reason", " ")
            self.assertEqual(r.returncode, 1)
            r = self._cli(tmp, "objective", "revise", "--session", "rv2", "我要甲", "--reason", "x")
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
