import tempfile
import unittest
from pathlib import Path

from acc import store
from acc.core import build_context
from acc.modules.goals import GoalsError, GoalsModule
from acc.modules.rapport import RapportModule


class SessionRapportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.rapport = RapportModule(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_session_follows_default_and_changes_with_it(self):
        self.goals.touch_session("s1", "claude", ".", "hi")

        import os

        os.environ["ACC_HOME"] = str(self.root)
        try:
            ctx = build_context("s1", ".", "claude")
            self.assertIn('level="3/5"', ctx)  # default level from seeding

            self.rapport.set_level(5)
            ctx2 = build_context("s1", ".", "claude")
            self.assertIn('level="5/5"', ctx2)
        finally:
            del os.environ["ACC_HOME"]

    def test_session_override_persists_when_default_changes(self):
        self.goals.touch_session("s2", "claude", ".", "hi")
        self.goals.set_session_rapport("s2", "coding", 1)

        import os

        os.environ["ACC_HOME"] = str(self.root)
        try:
            ctx = build_context("s2", ".", "claude")
            self.assertIn('level="1/5"', ctx)

            self.rapport.set_level(4)  # change the default
            ctx2 = build_context("s2", ".", "claude")
            self.assertIn('level="1/5"', ctx2)  # session override unaffected
        finally:
            del os.environ["ACC_HOME"]

    def test_clear_override_returns_to_following_default(self):
        self.goals.touch_session("s3", "claude", ".", "hi")
        self.goals.set_session_rapport("s3", "coding", 1)
        self.rapport.set_level(4)

        session = self.goals.load_session("s3")
        self.assertEqual(session["rapport"], {"preset": "coding", "level": 1})

        self.goals.clear_session_rapport("s3")
        session = self.goals.load_session("s3")
        self.assertNotIn("rapport", session)

        eff = self.rapport.get_effective(session.get("rapport"))
        self.assertEqual(eff["level"], 4)

    def test_override_with_deleted_preset_falls_back_to_default(self):
        other_id = self.rapport.create_preset("Writing")
        self.goals.touch_session("s4", "claude", ".", "hi")
        self.goals.set_session_rapport("s4", other_id, 2)

        self.rapport.delete_preset(other_id)  # falls back active preset -> coding

        session = self.goals.load_session("s4")
        eff = self.rapport.get_effective(session.get("rapport"))
        self.assertEqual(eff["presetId"], "coding")

    def test_set_session_rapport_rejects_unknown_preset(self):
        self.goals.touch_session("s5", "claude", ".", "hi")
        with self.assertRaises(GoalsError):
            self.goals.set_session_rapport("s5", "no-such-preset", 1)


if __name__ == "__main__":
    unittest.main()
