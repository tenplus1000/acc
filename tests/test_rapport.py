import tempfile
import unittest
from pathlib import Path

from acc.modules.rapport import RapportError, RapportModule


class RapportModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        from acc import store

        store.ensure_seeded(self.root)
        self.rapport = RapportModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_remove_last_level_refused_at_one(self):
        preset_id = self.rapport.create_preset("Solo")
        for _ in range(4):
            self.rapport.remove_last_level(preset_id)
        cfg = self.rapport.load_preset_config(preset_id)
        self.assertEqual(len(cfg["levels"]), 1)
        with self.assertRaises(RapportError):
            self.rapport.remove_last_level(preset_id)

    def test_deleting_active_preset_falls_back(self):
        other_id = self.rapport.create_preset("Writing")
        self.rapport.select_preset("coding")
        self.rapport.delete_preset("coding")
        state = self.rapport.load_state()
        self.assertEqual(state["activePreset"], other_id)

    def test_cannot_delete_last_preset(self):
        presets = self.rapport.list_presets()
        self.assertEqual(len(presets), 1)
        with self.assertRaises(RapportError):
            self.rapport.delete_preset("coding")

    def test_level_clamped_on_switch(self):
        self.rapport.set_level(5)
        preset_id = self.rapport.create_preset("Short")
        for _ in range(4):
            self.rapport.remove_last_level(preset_id)  # down to 1 level
        self.rapport.select_preset(preset_id)
        state = self.rapport.load_state()
        self.assertEqual(state["level"], 1)

    def test_add_level_appends(self):
        self.rapport.add_level("coding")
        cfg = self.rapport.load_preset_config("coding")
        self.assertEqual(len(cfg["levels"]), 6)
        self.assertEqual(cfg["levels"][5]["name"], "Level 6")


if __name__ == "__main__":
    unittest.main()
