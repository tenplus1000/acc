import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "acc" / "web" / "index.html"


class RapportEnabledUiMarkersTests(unittest.TestCase):
    """The Rapport on/off switch (global + per-session) is UI/JS-only --
    check the required strings and endpoints are wired into the page, and
    that it doesn't reach for prompt/alert/confirm (test_park_ui.py already
    covers `node --check` on the whole extracted <script>)."""

    def setUp(self):
        self.text = INDEX_HTML.read_text(encoding="utf-8")

    def test_required_ui_strings_present(self):
        for marker in ("注入 Rapport", "跟隨全域", "Rapport 關"):
            self.assertIn(marker, self.text, f"missing UI string: {marker!r}")

    def test_new_endpoints_wired(self):
        for path in ("/api/config/rapport_enabled", "/api/goals/session/rapport_enabled"):
            self.assertIn(path, self.text, f"missing endpoint call: {path!r}")

    def test_no_prompt_alert_confirm_calls(self):
        self.assertNotIn("prompt(", self.text)
        self.assertNotIn("alert(", self.text)
        self.assertNotIn("confirm(", self.text)


if __name__ == "__main__":
    unittest.main()
