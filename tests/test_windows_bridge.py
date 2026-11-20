import errno
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bridge
from rollouts import windows_index_path


class WindowsBridgeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.authoring = self.root / ".claw-pet/authoring"
        self.authoring.mkdir(parents=True)
        for context in (patch.object(sys, "platform", "win32"), patch.object(Path, "home", return_value=self.root),
                        patch.dict("os.environ", {"SystemRoot": str(self.root / "Windows"), "APPDATA": str(self.root / "漫游目录")})):
            context.start()
            self.addCleanup(context.stop)

    def test_roaming_directory_and_active_version_without_petui_setup(self):
        self.assertEqual(bridge.manager_data_root(), self.root / "漫游目录/com.petmanager.desktop")
        shell = self.root / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        shell.parent.mkdir(parents=True)
        shell.touch()
        def version(v):
            return subprocess.CompletedProcess([], 0, json.dumps(v).encode())
        with patch.object(subprocess, "run", return_value=version(["0.1.245"])) as call:
            self.assertEqual(bridge.verify_version(), "0.1.245")
            self.assertEqual(call.call_args.args[0][0], str(shell))
        with patch.object(subprocess, "run", return_value=version(["0.1.237", "0.1.245"])):
            with self.assertRaisesRegex(RuntimeError, "多个版本"):
                bridge.verify_version()

    def test_petui_fallback_with_non_ascii_path_and_unknown_version(self):
        exe = self.root / "管理端 & Hachimo.exe"
        exe.touch()
        (self.authoring / "desktop-path").write_text(str(exe) + "\n", encoding="utf-8")
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b'{"version":"0.1.237"}')) as call:
            self.assertEqual(bridge.verify_version(), "0.1.237")
            self.assertEqual(call.call_args.args[0], [str(exe), "--petui", "tool-info"])
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b'{"version":"9.9.9"}')):
            with self.assertRaisesRegex(RuntimeError, "9.9.9"):
                bridge.verify_version()

    def test_exclusive_lock_always_starts_at_byte_zero(self):
        locking = Mock()
        with patch.dict(sys.modules, {"msvcrt": SimpleNamespace(locking=locking, LK_NBLCK=2)}):
            with (self.root / "collector.lock").open("a+b") as guard:
                guard.write(b"prior"); guard.flush()
                bridge.lock_collector(guard)
                self.assertEqual(guard.tell(), 0)
                locking.assert_called_once_with(guard.fileno(), 2, 1)
                locking.side_effect = PermissionError(errno.EACCES, "locked")
                with self.assertRaisesRegex(RuntimeError, "已经在运行"):
                    bridge.lock_collector(guard)

    def test_canonical_rollout_paths_match_windows_session_roots(self):
        self.assertEqual(windows_index_path(r"\\?\D:\用户目录\记录.jsonl"), r"D:\用户目录\记录.jsonl")
        self.assertEqual(windows_index_path(r"\\?\UNC\host\share\记录.jsonl"), r"\\host\share\记录.jsonl")
        self.assertEqual(windows_index_path(r"C:\data\x"), r"C:\data\x")


if __name__ == "__main__":
    unittest.main()
