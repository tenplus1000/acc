"""Ultimate Goal / Ultimate Definition: loaded once via the agent's
instruction file at conversation start, re-sent in a turn only when they
changed compared with what that session already has."""

import json
import tempfile
import unittest
from pathlib import Path

from acc import store
from acc.events import read_events
from acc.modules.goals import GoalsModule, ultimate_hash
from tests.helpers import parse_hook_context, run_cli, run_hook


class UltimateInjectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        store.ensure_seeded(self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def _hook(self, agent, session_id, prompt="hi"):
        return parse_hook_context(run_hook(
            agent, {"session_id": session_id, "cwd": ".", "prompt": prompt,
                    "hook_event_name": "UserPromptSubmit"}, self.home,
        ))

    def test_first_turn_of_new_claude_session_omits_ultimate(self):
        ctx = self._hook("claude", "sess-new-claude")
        self.assertNotIn("Ultimate 已更新", ctx)
        self.assertNotIn("Ultimate Goal", ctx)

    def test_set_ultimate_then_next_turn_sends_once_then_omits(self):
        # establish baseline on turn 1
        ctx1 = self._hook("claude", "sess-ult")
        self.assertNotIn("Ultimate 已更新", ctx1)

        goals = GoalsModule(self.home)
        goals.set_ultimate("新的 Ultimate Goal", "新的 Definition")

        ctx2 = self._hook("claude", "sess-ult")
        self.assertIn("Ultimate 已更新", ctx2)
        self.assertIn("新的 Ultimate Goal", ctx2)
        self.assertIn("新的 Definition", ctx2)

        ctx3 = self._hook("claude", "sess-ult")
        self.assertNotIn("Ultimate 已更新", ctx3)

    def test_ultimate_sent_event_logged_once(self):
        self._hook("claude", "sess-evt")
        goals = GoalsModule(self.home)
        goals.set_ultimate("目標X", "定義X")
        self._hook("claude", "sess-evt")
        sent = [e for e in read_events(self.home, type="ultimate.sent") if e.get("session_id") == "sess-evt"]
        self.assertEqual(len(sent), 1)
        expected_hash = ultimate_hash("目標X", "定義X")
        self.assertEqual(sent[0]["hash"], expected_hash)
        self._hook("claude", "sess-evt")
        sent2 = [e for e in read_events(self.home, type="ultimate.sent") if e.get("session_id") == "sess-evt"]
        self.assertEqual(len(sent2), 1)  # not sent again on the following turn

    def test_legacy_session_without_ultimate_hash_does_not_get_it(self):
        goals = GoalsModule(self.home)
        # simulate a session created before this feature existed
        session = goals._default_session("sess-legacy", "claude", ".", str(self.home))
        goals.save_session(session)
        self.assertNotIn("ultimate_hash", goals.load_session("sess-legacy"))

        ctx = self._hook("claude", "sess-legacy")
        self.assertNotIn("Ultimate 已更新", ctx)
        # backfilled to current so future changes are still detected
        self.assertIn("ultimate_hash", goals.load_session("sess-legacy"))

    def test_codex_session_created_with_stale_installed_copy_gets_block_first_turn(self):
        installed = {"codex": {"ultimate_hash": "stale-hash-value",
                                "target": str(self.home / "AGENTS.md"), "at": "2020-01-01T00:00:00"}}
        store.atomic_write_json(self.home / ".rules_installed.json", installed)

        ctx = self._hook("codex", "sess-codex-stale")
        self.assertIn("Ultimate 已更新", ctx)

        ctx2 = self._hook("codex", "sess-codex-stale")
        self.assertNotIn("Ultimate 已更新", ctx2)

    def test_preview_does_not_change_session_ultimate_hash(self):
        self._hook("claude", "sess-preview")
        goals = GoalsModule(self.home)
        goals.set_ultimate("新目標", "新定義")

        before = goals.load_session("sess-preview")["ultimate_hash"]
        proc = run_cli(["context", "--session", "sess-preview", "--cwd", "."], self.home)
        self.assertIn("Ultimate 已更新", proc.stdout)
        after = goals.load_session("sess-preview")["ultimate_hash"]
        self.assertEqual(before, after)
        self.assertNotEqual(after, ultimate_hash("新目標", "新定義"))

        # a real hook turn still catches the update
        ctx = self._hook("claude", "sess-preview")
        self.assertIn("Ultimate 已更新", ctx)

    def test_set_ultimate_auto_reinstalls_codex_not_claude(self):
        from acc.cli import install_rules

        codex_target = self.home / "AGENTS.md"
        claude_target = self.home / "CLAUDE.md"
        install_rules("codex", self.home, codex_target)
        install_rules("claude", self.home, claude_target)

        claude_before = claude_target.read_text()

        goals = GoalsModule(self.home)
        goals.set_ultimate("重裝後的目標", "重裝後的定義")

        codex_text = codex_target.read_text()
        self.assertIn("重裝後的目標", codex_text)
        self.assertIn("重裝後的定義", codex_text)

        # claude imports live -- its file (a fixed import line) is untouched
        self.assertEqual(claude_target.read_text(), claude_before)

    def test_set_ultimate_skips_reinstall_when_managed_block_removed(self):
        from acc.cli import install_rules

        codex_target = self.home / "AGENTS.md"
        install_rules("codex", self.home, codex_target)
        codex_target.write_text("# 使用者自己清掉了受管區塊\n")

        goals = GoalsModule(self.home)
        goals.set_ultimate("不該出現", "不該出現的定義")
        self.assertNotIn("不該出現", codex_target.read_text())

    def test_old_goals_md_with_placeholders_still_renders(self):
        goals_md_path = self.home / "templates" / "goals.md"
        goals_md_path.write_text(
            "<Goals>\nUltimate Goal：{ultimate_goal}\nUltimate Definition：\n{ultimate_definition}\n"
            "Project：{project}\nSuper Objective：{super_objective}\n{suggest_block}\n{objective_block}\n</Goals>\n",
            encoding="utf-8",
        )
        goals = GoalsModule(self.home)
        goals.set_ultimate("舊版目標", "舊版定義")

        ctx = self._hook("claude", "sess-oldtmpl")
        self.assertIn("舊版目標", ctx)
        self.assertIn("舊版定義", ctx)

        # and it keeps showing up every turn (old behavior), unlike the new template
        ctx2 = self._hook("claude", "sess-oldtmpl")
        self.assertIn("舊版目標", ctx2)
