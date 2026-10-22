import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "acc" / "web" / "index.html"


class ParkQueueUiMarkersTests(unittest.TestCase):
    """UI/JS-only pieces (parking queue + session card rework) can't be unit
    tested through the Python test runner -- these check that the required
    strings and endpoints are wired into the page, plus that the extracted
    <script> is syntactically valid JS (via `node --check`)."""

    def setUp(self):
        self.text = INDEX_HTML.read_text(encoding="utf-8")

    def test_required_ui_strings_present(self):
        for marker in (
            "切上來", "擱置", "確認成功", "確認失敗", "移除",
            "設定目標", "進行中", "已結案", "編輯 Objective",
        ):
            self.assertIn(marker, self.text, f"missing UI string: {marker!r}")

    def test_new_endpoints_wired(self):
        for path in (
            "/api/goals/session/park",
            "/api/goals/session/parked/switch",
            "/api/goals/session/parked/reorder",
            "/api/goals/session/parked/delete",
        ):
            self.assertIn(path, self.text, f"missing endpoint call: {path!r}")

    def test_no_prompt_alert_confirm_calls(self):
        self.assertNotIn("prompt(", self.text)
        self.assertNotIn("alert(", self.text)
        self.assertNotIn("confirm(", self.text)

    def test_drag_and_drop_and_fallback_buttons_present(self):
        self.assertIn("draggable = true", self.text)
        self.assertIn("dragstart", self.text)
        self.assertIn("btn-parked-up", self.text)
        self.assertIn("btn-parked-down", self.text)

    def test_extracted_script_is_valid_js(self):
        m = re.search(r"<script>(.*)</script>", self.text, re.S)
        self.assertIsNotNone(m)
        script_path = Path("/tmp/acc_index_script_test.js")
        script_path.write_text(m.group(1), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["node", "--check", str(script_path)],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            self.skipTest("node is not available in this environment")
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TemplateParkSentenceTests(unittest.TestCase):
    def test_objective_active_template_mentions_park(self):
        from acc import defaults

        self.assertIn(
            'acc objective park --session <session> --reason "<使用者的原話>"',
            defaults.RULES_MD,
        )


if __name__ == "__main__":
    unittest.main()
