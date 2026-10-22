import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


class WebServerTests(unittest.TestCase):
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

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_get_state(self):
        state = self._get("/api/state")
        self.assertIn("rapport", state)
        self.assertIn("goals", state)
        self.assertEqual(state["rapport"]["activePreset"], "coding")

    def test_post_rapport_level(self):
        result = self._post("/api/rapport/level", {"level": 4})
        self.assertEqual(result["rapport"]["level"], 4)

        state = self._get("/api/state")
        self.assertEqual(state["rapport"]["level"], 4)

    def test_session_rapport_endpoints(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.touch_session("web-sess", "claude", ".", "hi")

        state = self._get("/api/state")
        session = next(s for s in state["goals"]["sessions"] if s["session_id"] == "web-sess")
        self.assertIsNone(session["rapport"])
        self.assertEqual(session["rapport_effective"]["preset"], "coding")

        result = self._post("/api/goals/session/rapport",
                             {"sessionId": "web-sess", "preset": "coding", "level": 1})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-sess")
        self.assertEqual(session["rapport"], {"preset": "coding", "level": 1})
        self.assertEqual(session["rapport_effective"]["level"], 1)

        result = self._post("/api/goals/session/rapport/clear", {"sessionId": "web-sess"})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-sess")
        self.assertIsNone(session["rapport"])

    def test_reopen_endpoint(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.objective_set("web-reopen", "目標", "標準")
        goals.objective_done("web-reopen", "備註", closed_by="user")

        result = self._post("/api/goals/session/reopen", {"sessionId": "web-reopen", "index": 0})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-reopen")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["objective"], "目標")

    def test_index_page_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("Agent Context Controller", body)


class RapportEnabledEndpointTests(WebServerTests):
    def test_config_rapport_enabled_endpoint_persists_and_returns_fresh_state(self):
        result = self._post("/api/config/rapport_enabled", {"enabled": False})
        self.assertFalse(result["rapport"]["enabled"])

        state = self._get("/api/state")
        self.assertFalse(state["rapport"]["enabled"])

        result = self._post("/api/config/rapport_enabled", {"enabled": True})
        self.assertTrue(result["rapport"]["enabled"])

    def test_session_rapport_enabled_endpoint(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.touch_session("web-renabled", "claude", ".", "hi")

        result = self._post("/api/goals/session/rapport_enabled",
                             {"sessionId": "web-renabled", "value": False})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-renabled")
        self.assertEqual(session["rapport_enabled"], False)
        self.assertFalse(session["rapport_enabled_effective"])
        self.assertEqual(session["rapport_enabled_source"], "session")

        result = self._post("/api/goals/session/rapport_enabled",
                             {"sessionId": "web-renabled", "value": None})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-renabled")
        self.assertIsNone(session["rapport_enabled"])
        self.assertTrue(session["rapport_enabled_effective"])
        self.assertEqual(session["rapport_enabled_source"], "global")


if __name__ == "__main__":
    unittest.main()


class SuggestionEndpointTests(WebServerTests):
    def test_suggest_accept_and_reject_endpoints(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.touch_session("web-sug", "claude", ".", "hi")
        session = goals.load_session("web-sug")
        project = session["project"]

        _, entry = goals.suggest_super("web-sug", "新方向", "理由")

        state = self._get("/api/state")
        row = next(p for p in state["goals"]["projects"] if p["path"] == project)
        self.assertEqual(len(row["suggestions"]), 1)
        self.assertEqual(row["suggestions"][0]["status"], "pending")

        result = self._post("/api/goals/project/suggestion/accept", {"path": project, "id": entry["id"]})
        row = next(p for p in result["goals"]["projects"] if p["path"] == project)
        self.assertEqual(row["super_objective"], "新方向")
        self.assertTrue(any(h.get("after") == "新方向" for h in row["history"]))

    def test_reject_endpoint(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.touch_session("web-sug2", "claude", ".", "hi")
        session = goals.load_session("web-sug2")
        project = session["project"]
        _, entry = goals.suggest_super("web-sug2", "另一方向", "理由二")

        result = self._post("/api/goals/project/suggestion/reject", {"path": project, "id": entry["id"]})
        row = next(p for p in result["goals"]["projects"] if p["path"] == project)
        self.assertEqual(row["super_objective"], "")


class ParkQueueEndpointTests(WebServerTests):
    def test_park_switch_reorder_delete_endpoints(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root

        goals = GoalsModule(get_root())
        goals.objective_set("web-park", "甲", "c1")

        result = self._post("/api/goals/session/park", {"sessionId": "web-park", "reason": "先擱著"})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-park")
        self.assertEqual(session["status"], "unset")
        self.assertEqual(len(session["parked"]), 1)
        oid = session["parked"][0]["oid"]

        result = self._post("/api/goals/session/parked/switch", {"sessionId": "web-park", "oid": oid})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-park")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["objective"], "甲")
        self.assertEqual(len(session["parked"]), 0)

        goals.park("web-park", "again", actor="user")
        goals.objective_set("web-park", "乙", "c2")
        goals.park("web-park", "again2", actor="user")
        session = goals.load_session("web-park")
        oids = [b["oid"] for b in session["parked"]]
        reordered = list(reversed(oids))
        result = self._post("/api/goals/session/parked/reorder", {"sessionId": "web-park", "oids": reordered})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-park")
        self.assertEqual([b["oid"] for b in session["parked"]], reordered)

        result = self._post("/api/goals/session/parked/delete", {"sessionId": "web-park", "oid": reordered[0]})
        session = next(s for s in result["goals"]["sessions"] if s["session_id"] == "web-park")
        self.assertEqual(len(session["parked"]), 1)
        self.assertTrue(any(h.get("status") == "dropped" for h in session["history"]))

    def test_reorder_bad_permutation_returns_400(self):
        from acc.modules.goals import GoalsModule
        from acc.core import get_root
        import urllib.error

        goals = GoalsModule(get_root())
        goals.objective_set("web-park2", "甲", "c1")
        goals.park("web-park2", "先擱著", actor="user")

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/goals/session/parked/reorder",
            data=json.dumps({"sessionId": "web-park2", "oids": ["bogus"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
