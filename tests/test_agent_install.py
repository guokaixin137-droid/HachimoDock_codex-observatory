import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import manage
import windows_service


class AgentInstallerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_component_import_requires_the_selected_release_contents(self):
        library = self.root / ".claw-pet/components"
        selection = library / "selected/codex-observatory.json"
        selection.parent.mkdir(parents=True)
        key = "0123456789abcdef"
        selection.write_text(json.dumps({"hash": key}))
        selected = library / "library/codex-observatory" / key
        with zipfile.ZipFile(manage.ROOT / manage.PACKAGE) as archive:
            archive.extractall(selected)
        with patch.object(Path, "home", return_value=self.root):
            self.assertTrue(manage.component_state()["componentImported"])
            (selected / "buttons.json").write_text("{}")
            result = manage.component_state()
            self.assertFalse(result["componentImported"])
            self.assertEqual(result["selectedVersion"], manage.VERSION)

    def test_windows_start_uses_payload_and_checks_process_and_data(self):
        payload = self.root / "版本 3.1.8"
        codex = self.root / "Codex 历史"
        manager = self.root / "Hachimodock.exe"
        actions = []

        def fake_task(operation, **values):
            actions.append(operation)
            if operation == "install":
                self.assertEqual(values["python"], payload / "runtime/pythonw.exe")
                self.assertIn(str(payload / "manage.py"), values["args"])
                self.assertIn(str(codex), values["args"])
                self.assertIn(str(manager), values["args"])
                manage.bridge.write_health(self.root / "health.json", "ready")
            if operation == "status":
                self.assertGreater(int(values["pid"]), 0)
                return {"registered": True, "running": True}

        with patch.object(windows_service, "task", side_effect=fake_task):
            state = windows_service.install(self.root, payload, codex, manager)
        self.assertEqual(actions, ["save", "install", "status"])
        self.assertTrue(state["dataReady"])

    def test_windows_task_failure_restores_previous_registration(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                actions = []

                def fail_install(operation, **values):
                    actions.append(operation)
                    if existing and operation == "save":
                        values["backup"].write_text("<Task/>")
                    if operation == "install":
                        raise RuntimeError("registration failed")
                    if operation == "restore":
                        self.assertEqual(values["backup"].read_text(), "<Task/>")

                with patch.object(windows_service, "task", side_effect=fail_install):
                    with self.assertRaisesRegex(RuntimeError, "registration failed"):
                        windows_service.install(self.root, self.root, self.root, None)
                self.assertEqual(actions, ["save", "install", "restore" if existing else "remove"])

    def test_stale_or_unavailable_worker_is_not_data_ready(self):
        health = self.root / "health.json"
        current = int(time.time() * 1000)
        with patch.object(windows_service, "task", return_value={"registered": True, "running": True}):
            for phase, at in (("unavailable", current), ("ready", current - 60_000)):
                health.write_text(json.dumps({"pid": 10, "phase": phase, "at": at}))
                self.assertFalse(windows_service.status(health)["dataReady"])
