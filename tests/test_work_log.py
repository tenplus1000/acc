import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from acc import store
from acc.modules.goals import GoalsError, GoalsModule
from tests.helpers import run_cli


class WorkLogScenarioTests(unittest.TestCase):
    """set -> revise -> park -> set other -> done (auto-promote) -> switch
    -> fail -> reopen -> parked delete, exercised through the real
    GoalsModule ops so the events.jsonl + session.json shapes are the real
    ones, then checked against the rendered work_log()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)
        self.sid = "wl1"
        self._build_scenario()

    def tearDown(self):
        self._tmp.cleanup()

    def _build_scenario(self):
        g = self.goals
        g.objective_set(self.sid, "甲", "c1")
        g.objective_revise(self.sid, "甲改", None, reason="R1")
        g.park(self.sid, "P1", actor="agent")
        g.objective_set(self.sid, "乙", "c2")
        g.objective_done(self.sid, "完成了")  # closes 乙, auto-promotes 甲改

        g.park(self.sid, "P2", actor="user")  # 甲改 parked again
        g.objective_set(self.sid, "丙", "c3")
        session = g.load_session(self.sid)
        oid_甲改 = session["parked"][0]["oid"]
        g.switch_parked(self.sid, oid_甲改, actor="user")  # 甲改 active, 丙 parked
        g.objective_fail(self.sid, "F1", closed_by="agent")  # closes 甲改, auto-promotes 丙
        g.objective_done(self.sid, "D2")  # closes 丙, nothing left parked

        session = g.load_session(self.sid)
        idx = next(i for i, h in enumerate(session["history"]) if h["status"] == "failed")
        g.reopen(self.sid, idx)  # 甲改 active again

        g.park(self.sid, "P3", actor="agent")  # 甲改 parked, prepping delete
        session = g.load_session(self.sid)
        oid2 = session["parked"][0]["oid"]
        g.delete_parked(self.sid, oid2, actor="user")

        self.log = g.work_log(self.sid)

    def _pos(self, needle):
        i = self.log.find(needle)
        self.assertGreaterEqual(i, 0, f"missing: {needle!r}\n---\n{self.log}")
        return i

    def test_order_and_labels(self):
        p_set1 = self._pos("設定　甲\n")
        p_revise = self._pos("修改（Agent）理由：R1")
        p_diff = self._pos("目標：甲 → 甲改")
        p_park1 = self._pos("擱置（Agent）理由：P1　甲改")
        p_set2 = self._pos("設定　乙")
        p_done1 = self._pos("成功（Agent")
        p_note1 = self._pos("備註：完成了")
        p_promote1 = self._pos("自動接回　甲改")
        p_park2 = self._pos("擱置（使用者）理由：P2　甲改")
        p_set3 = self._pos("設定　丙")
        p_switch = self._pos("切上來（使用者）甲改（與「丙」對調）")
        p_fail = self._pos("失敗（Agent")
        p_note2 = self._pos("備註：F1")
        p_promote2 = self._pos("自動接回　丙")
        p_done2 = self._pos("備註：D2")
        p_reopen = self._pos("重新開啟（使用者）　甲改")
        p_park3 = self._pos("擱置（Agent）理由：P3　甲改")
        p_delete = self._pos("移除（使用者）　甲改")

        positions = [
            p_set1, p_revise, p_diff, p_park1, p_set2, p_done1, p_note1,
            p_promote1, p_park2, p_set3, p_switch, p_fail, p_note2,
            p_promote2, p_done2, p_reopen, p_park3, p_delete,
        ]
        self.assertEqual(positions, sorted(positions))

    def test_current_section_after_scenario(self):
        self.assertIn("## 目前", self.log)
        self.assertIn("進行中：尚未設定", self.log)
        tail = self.log[self.log.index("## 目前"):]
        self.assertNotIn("擱置：", tail)

    def test_header_has_session_and_project(self):
        self.assertIn(f"Session：{self.sid}", self.log)
        self.assertIn("Agent：", self.log)
        self.assertIn("專案：", self.log)

    def test_unknown_session_raises(self):
        with self.assertRaises(GoalsError):
            self.goals.work_log("no-such-session")


class WorkLogPreEventReconstructionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_pre_event_history_entry_reconstructed_and_not_duplicated(self):
        sid = "old1"
        session = {
            "session_id": sid,
            "agent": "claude",
            "cwd": str(self.root),
            "project": str(self.root),
            "first_prompt": "",
            "created": "2026-01-01T00:00:00",
            "last_seen": "2026-01-01T00:10:00",
            "objective": "",
            "success_criteria": "",
            "status": "unset",
            "history": [
                {
                    "objective": "舊時代的事",
                    "success_criteria": "舊標準",
                    "status": "achieved",
                    "note": "舊備註",
                    "started": "2026-01-01T00:00:00",
                    "closed": "2026-01-01T00:05:00",
                    "closed_by": "agent",
                    "turns": 3,
                    "edits": [],
                }
            ],
            "turns": [],
            "turn_count": 0,
            "parked": [],
            "turn_counts_by_oid": {},
        }
        self.goals.save_session(session)
        # No events.jsonl entries reference this session at all -- fully pre-event.

        log = self.goals.work_log(sid)
        self.assertIn("（事件紀錄開始前，依結案資料重建）", log)
        self.assertIn("設定　舊時代的事", log)
        self.assertIn("成功（Agent，3 輪，00:00 → 00:05）　舊時代的事", log)
        self.assertIn("備註：舊備註", log)
        # Rendered exactly once each (not duplicated).
        self.assertEqual(log.count("舊時代的事"), 2)  # one 設定 line, one 成功 line

    def test_history_entry_with_matching_event_not_reconstructed(self):
        sid = "real1"
        self.goals.objective_set(sid, "新的事", "c1")
        self.goals.objective_done(sid, "做完了")
        log = self.goals.work_log(sid)
        self.assertNotIn("重建", log)
        self.assertEqual(log.count("新的事"), 2)  # 設定 + 成功 lines, no duplicate


class WorkLogCliTests(unittest.TestCase):
    def test_cli_prints_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(["objective", "set", "--session", "c1", "甲", "--criteria", "c1"], tmp)
            run_cli(["objective", "done", "--session", "c1", "--note", "ok"], tmp)
            proc = run_cli(["objective", "log", "--session", "c1"], tmp)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("工作紀錄", proc.stdout)
            self.assertIn("甲", proc.stdout)

    def test_cli_errors_on_unknown_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(["objective", "log", "--session", "nope"], tmp)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(proc.stderr.strip())


class WorkLogRulesTests(unittest.TestCase):
    def test_rules_md_mentions_log_command(self):
        from acc import defaults

        self.assertIn("acc objective log", defaults.RULES_MD)


class WorkLogWebTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["ACC_HOME"] = str(self.home)

        from acc.web.server import Handler

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        del os.environ["ACC_HOME"]
        self._tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return json.loads(resp.read())

    def test_session_log_endpoint(self):
        from acc.core import get_root
        from acc.modules.goals import GoalsModule

        goals = GoalsModule(get_root())
        goals.objective_set("w1", "甲", "c1")
        goals.objective_done("w1", "完成")

        data = self._get("/api/session/log?session=w1")
        self.assertIn("text", data)
        self.assertIn("工作紀錄", data["text"])
        self.assertIn("甲", data["text"])

    def test_session_log_endpoint_unknown_session_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/session/log?session=nope")
        self.assertEqual(ctx.exception.code, 400)


class WorkLogIndexHtmlTests(unittest.TestCase):
    def test_index_html_has_copy_button_and_no_native_dialogs(self):
        from acc.web.server import INDEX_HTML_PATH

        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        self.assertIn("複製工作紀錄", html)
        self.assertNotIn("prompt(", html)
        self.assertNotIn("alert(", html)
        self.assertNotIn("confirm(", html)


class WorkLogRapportAndSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        store.ensure_seeded(self.root)
        self.goals = GoalsModule(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rapport_changes_at_submission_and_session_events(self):
        from acc.modules.rapport import RapportModule

        g, r = self.goals, RapportModule(self.root)
        g.touch_session("rs", "claude", ".", "first")          # L3 default
        r.set_level(5)
        r.set_level(2)                                         # between submissions: not logged
        g.touch_session("rs", "claude", ".", "second")         # L2 default
        g.touch_session("rs", "claude", ".", "third")          # unchanged
        g.set_session_rapport("rs", "coding", 2)
        g.touch_session("rs", "claude", ".", "fourth")         # same level, now 個別設定
        g.set_nickname("rs", "小名")
        g.archive_session("rs")
        g.touch_session("rs", "claude", ".", "fifth")          # auto-revived
        log = g.work_log("rs")

        rapport_lines = [l for l in log.splitlines() if "Rapport" in l]
        self.assertEqual(len(rapport_lines), 3, log)
        self.assertIn("Rapport 起始　Coding L3 Normal（跟隨預設，跟隨全域）", rapport_lines[0])
        self.assertIn("Rapport 變更　Coding L2 Careful（跟隨預設，跟隨全域）", rapport_lines[1])
        self.assertIn("Rapport 變更　Coding L2 Careful（個別設定，跟隨全域）", rapport_lines[2])
        self.assertNotIn("L5", log)
        for text in ("對話開始", "改名（使用者）（無） → 小名", "封存（使用者）", "從封存自動回來"):
            self.assertIn(text, log)
        self.assertNotIn("first", log)  # prompt text never appears

if __name__ == "__main__":
    unittest.main()
