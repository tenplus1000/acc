import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import parse_hook_context, run_hook


class HookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_case_a_rapport_level_switch(self):
        # Default level is 3; bump to 5 first via a CLI-less path (web-op
        # equivalent): use the rapport module directly through the CLI.
        from tests.helpers import run_cli

        run_cli(["root"], self.home)  # seeds the data root

        # set level 5 via a hook-adjacent call: use python -c through the
        # module API (acting as the "web" operation).
        import subprocess
        import sys

        code = (
            "from acc.core import get_root; from acc.modules.rapport import RapportModule; "
            "r = RapportModule(get_root()); r.set_level(5)"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            env={**__import__("os").environ, "ACC_HOME": str(self.home),
                 "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
            check=True,
        )

        proc = run_hook("claude", {"session_id": "s1", "cwd": ".", "prompt": "hi",
                                    "hook_event_name": "UserPromptSubmit"}, self.home)
        self.assertEqual(proc.returncode, 0)
        ctx = parse_hook_context(proc)
        self.assertIn('level="5/5"', ctx)
        self.assertIn("Flow", ctx)

        code2 = (
            "from acc.core import get_root; from acc.modules.rapport import RapportModule; "
            "r = RapportModule(get_root()); r.set_level(2)"
        )
        import subprocess as sp
        sp.run(
            [__import__("sys").executable, "-c", code2],
            env={**__import__("os").environ, "ACC_HOME": str(self.home),
                 "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
            check=True,
        )

        proc2 = run_hook("claude", {"session_id": "s1", "cwd": ".", "prompt": "hi again",
                                     "hook_event_name": "UserPromptSubmit"}, self.home)
        ctx2 = parse_hook_context(proc2)
        self.assertIn('level="2/5"', ctx2)
        self.assertNotIn("Flow", ctx2)

    def test_case_c_both_modules_present(self):
        proc = run_hook("claude", {"session_id": "s2", "cwd": ".", "prompt": "do X",
                                    "hook_event_name": "UserPromptSubmit"}, self.home)
        ctx = parse_hook_context(proc)
        self.assertIn("<Rapport ", ctx)
        self.assertIn("<Goals>", ctx)

    def test_case_d_shared_state_across_agents(self):
        from tests.helpers import run_cli

        run_cli(["objective", "set", "完成 Claude adapter", "--criteria", "測試過關",
                  "--session", "shared1"], self.home)

        proc_claude = run_hook("claude", {"session_id": "shared1", "cwd": ".",
                                           "prompt": "go", "hook_event_name": "UserPromptSubmit"},
                                self.home)
        proc_codex = run_hook("codex", {"session_id": "shared1", "cwd": ".",
                                         "prompt": "go", "hook_event_name": "UserPromptSubmit"},
                               self.home)
        ctx_claude = parse_hook_context(proc_claude)
        ctx_codex = parse_hook_context(proc_codex)
        self.assertIn("完成 Claude adapter", ctx_claude)
        self.assertIn("完成 Claude adapter", ctx_codex)

    def test_case_e_new_session_unset_objective(self):
        proc = run_hook("claude", {"session_id": "brand-new-session", "cwd": ".",
                                    "prompt": "hello", "hook_event_name": "UserPromptSubmit"},
                         self.home)
        ctx = parse_hook_context(proc)
        self.assertIn("尚未設定", ctx)
        self.assertIn("brand-new-session", ctx)

    def test_bad_json_stdin_exits_cleanly(self):
        import subprocess
        import sys
        import os

        env = dict(os.environ)
        env["ACC_HOME"] = str(self.home)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        proc = subprocess.run(
            [sys.executable, "-m", "acc", "hook", "claude"],
            input="{not valid json",
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        log_path = self.home / "logs" / "hook.log"
        self.assertTrue(log_path.exists())
        self.assertTrue(log_path.read_text().strip())

    def test_non_user_prompt_submit_event_no_output(self):
        proc = run_hook("claude", {"session_id": "s3", "cwd": ".", "prompt": "x",
                                    "hook_event_name": "SessionStart"}, self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_missing_session_id_omits_objective_block(self):
        proc = run_hook("claude", {"cwd": ".", "prompt": "no session here",
                                    "hook_event_name": "UserPromptSubmit"}, self.home)
        ctx = parse_hook_context(proc)
        self.assertIsNotNone(ctx)
        # Super Objective (project-level) is always present; the per-session
        # Objective block is what must be omitted when session_id is missing.
        self.assertNotIn("Objective（這個對話）", ctx)
        self.assertNotIn("acc objective set", ctx)

    def test_placeholder_braces_in_prompt_do_not_break(self):
        proc = run_hook(
            "claude",
            {
                "session_id": "brace-session",
                "cwd": ".",
                "prompt": "please handle {modules} and {level} literally",
                "hook_event_name": "UserPromptSubmit",
            },
            self.home,
        )
        self.assertEqual(proc.returncode, 0)
        ctx = parse_hook_context(proc)
        self.assertIsNotNone(ctx)
        self.assertIn("<Rapport ", ctx)


if __name__ == "__main__":
    unittest.main()
