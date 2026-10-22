import subprocess
import tempfile
import unittest
from pathlib import Path

from acc import store
from acc.modules.goals import GoalsModule


class ProjectKeyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_project_key_resolves_to_git_root(self):
        with tempfile.TemporaryDirectory() as repo:
            repo_path = Path(repo)
            subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
            sub = repo_path / "a" / "b"
            sub.mkdir(parents=True)

            goals = GoalsModule(self.root)
            key, project = goals.resolve_project(str(sub))
            self.assertEqual(project, str(repo_path.resolve()))

    def test_project_key_falls_back_to_cwd_without_git(self):
        with tempfile.TemporaryDirectory() as plain:
            plain_path = Path(plain)
            goals = GoalsModule(self.root)
            key, project = goals.resolve_project(str(plain_path))
            self.assertEqual(project, str(plain_path.resolve()))

    def test_super_objective_empty_without_project_file(self):
        goals = GoalsModule(self.root)
        key, project = goals.resolve_project("/tmp/definitely-not-set-up")
        data = goals.load_project(key)
        self.assertEqual(data["super_objective"], "")


class SessionProjectFixedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_project_stays_fixed_after_cwd_changes_across_hook_calls(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.goals.touch_session("sess-p1", "claude", first, "hi")
            session = self.goals.load_session("sess-p1")
            self.assertEqual(session["project"], str(Path(first).resolve()))

            self.goals.touch_session("sess-p1", "claude", second, "again")
            session = self.goals.load_session("sess-p1")
            self.assertEqual(session["cwd"], second)
            self.assertEqual(session["project"], str(Path(first).resolve()))

    def test_missing_project_gets_filled_once(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.goals.touch_session("sess-p2", "claude", cwd, "hi")
            session = self.goals.load_session("sess-p2")
            del session["project"]
            self.goals.save_session(session)

            ctx = self.goals.build_context("sess-p2", cwd)
            self.assertIn(str(Path(cwd).resolve()), ctx)

            session = self.goals.load_session("sess-p2")
            self.assertEqual(session["project"], str(Path(cwd).resolve()))

            # A later cwd change still must not move the now-filled project.
            with tempfile.TemporaryDirectory() as other_cwd:
                self.goals.touch_session("sess-p2", "claude", other_cwd, "later")
                session = self.goals.load_session("sess-p2")
                self.assertEqual(session["project"], str(Path(cwd).resolve()))

    def test_force_edit_can_override_project(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.goals.touch_session("sess-p3", "claude", cwd, "hi")
            self.goals.force_edit_session("sess-p3", {"project": "/some/forced/path"})
            session = self.goals.load_session("sess-p3")
            self.assertEqual(session["project"], "/some/forced/path")


class ModuleDisabledTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_disabled_module_block_absent(self):
        import os

        os.environ["ACC_HOME"] = str(self.home)
        try:
            from acc import core

            root = core.get_root()
            cfg = core.load_config(root)
            cfg["modules"]["goals"] = False
            core.save_config(root, cfg)

            ctx = core.build_context("some-session", ".", "claude")
            self.assertIn("<Rapport ", ctx)
            self.assertNotIn("<Goals>", ctx)
        finally:
            del os.environ["ACC_HOME"]


if __name__ == "__main__":
    unittest.main()
