import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from bridge import document, empty_view, new_state, write_document, STATE_NAME, HEARTBEAT_MS


class BridgeTests(unittest.TestCase):
    def test_unknown_is_not_zero(self):
        d = document({"available": False})
        self.assertEqual(d["overview"]["v4"], "—")
        self.assertEqual(d["quota"]["v1"], "—")
        self.assertEqual(d["quota"]["mode"], -1)

    def test_expired_quota_does_not_show_full(self):
        d = document({"available": True, "quota": {"windows": [{"label":"周额度", "status":"awaiting_reset_refresh", "remainingPercent":None}]}})
        self.assertEqual(d["quota"]["v1"], "—")
        self.assertIn("等待新观测", d["quota"]["v2"])

    def test_stale_quota_labels_last_observation(self):
        d = document({"quota":{"status":"stale", "windows":[{"status":"observed", "label":"周额度", "remainingPercent":75}]}})
        self.assertEqual(d["quota"]["v1"], "75%")
        self.assertIn("旧观测", d["quota"]["v2"])
        self.assertEqual(d["quota"]["mode"], -1)

    def test_navigation_and_receipts_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            s = new_state()
            s["models"]["nav"]["value"]["page"] = 1
            s["receipts"] = [{"id":"event-1", "fingerprint":"a"*64}]
            (root/STATE_NAME).write_text(json.dumps(s))
            d = document({})
            write_document(root, d)
            saved = json.loads((root/STATE_NAME).read_text())
            self.assertEqual(saved["models"]["nav"], s["models"]["nav"])
            self.assertEqual(saved["receipts"], s["receipts"])
            self.assertEqual(saved["models"]["telemetry"]["value"], d)

    def test_observed_host_write_causes_remerge(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            write_document(root, document({}))
            def navigate(attempt):
                if attempt == 0:
                    path = root/STATE_NAME
                    s = json.loads(path.read_text())
                    s["models"]["nav"]["value"]["page"] = 1
                    path.write_text(json.dumps(s))
            changed = document({"agent": {"state": "working"}})
            result = write_document(root, changed, before_commit=navigate)
            self.assertEqual(result["page"], 1)

    def test_unchanged_sample_preserves_navigation_context(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = document({})
            write_document(root, d)
            path = root/STATE_NAME
            before = path.read_bytes()
            later = copy.deepcopy(d)
            later["at"] += 5000
            self.assertFalse(write_document(root, later)["written"])
            self.assertEqual(path.read_bytes(), before)

    def test_heartbeat_and_changed_data_still_refresh(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = document({})
            write_document(root, d)
            d["at"] += HEARTBEAT_MS
            self.assertTrue(write_document(root, d)["written"])
            d["at"] += 5000
            d["overview"]["v1"] = "正在工作"
            self.assertTrue(write_document(root, d)["written"])

    def test_corrupt_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t)/STATE_NAME
            path.write_bytes(b"not-json")
            with self.assertRaises(ValueError):
                write_document(Path(t), document({}))
            self.assertEqual(path.read_bytes(), b"not-json")

    def test_foreign_models_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t)/STATE_NAME
            s = new_state()
            s["models"]["other"] = {"kind":"counter", "value":5}
            raw=json.dumps(s)
            path.write_text(raw)
            with self.assertRaises(ValueError):
                write_document(Path(t), document({}))
            self.assertEqual(path.read_text(), raw)


if __name__ == "__main__":
    unittest.main()
