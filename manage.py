"""Install, inspect or remove the Codex dashboard collector for the current user."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from zoneinfo import reset_tzpath

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "pc"))
if (ROOT / "zones/Asia/Shanghai").is_file():
    reset_tzpath([str(ROOT / "zones")])

import bridge
import install_bridge as mac
import manager
import windows_service
from page_feed import SUPPORTED_MANAGERS
from rollouts import can_read_compressed

VERSION = "3.1.10"
PACKAGE = "codex-observatory.hachiapp"
PACKAGE_SHA256 = "5a4f4a64ecba5512e59cd127648dd6ad3d856bde7c237d13b09fbcf3e33f316b"
PROGRAMS = ("manage.py", "zones/Asia/Shanghai", PACKAGE, *(f"pc/{file}" for file in (*mac.FILES, "windows_service.py")))


def system():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    raise RuntimeError("请在运行 Codex 和 Hachimodock 的 Windows 或 Mac 桌面会话中执行。")


def local_root(os_name):
    if os_name == "windows":
        return Path(os.environ["LOCALAPPDATA"]) / "HachimoDock/CodexObservatory"
    return mac.locations()["root"]


def interpreter(directory, os_name):
    return directory / ("runtime/python.exe" if os_name == "windows" else "runtime/bin/python3")


def component_state():
    root = Path.home() / ".claw-pet/components"
    try:
        key = json.loads((root / "selected/codex-observatory.json").read_bytes())["hash"]
        if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{16}", key):
            raise ValueError("invalid selection")
        selected_dir = root / "library/codex-observatory" / key
        metadata = json.loads((selected_dir / "component.json").read_bytes())
        if metadata["id"] != "codex-observatory":
            raise ValueError("invalid component")
        matches = False
        if metadata["version"] == VERSION:
            with zipfile.ZipFile(ROOT / PACKAGE) as archive:
                matches = all(member_matches(archive, entry, selected_dir) for entry in archive.infolist() if not entry.is_dir())
        return {"componentImported": matches,
                "selectedVersion": metadata["version"], "selectedHash": key}
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile):
        return {"componentImported": False, "selectedVersion": None}


def member_matches(archive, entry, directory):
    installed = directory / entry.filename
    return (installed.is_file() and installed.stat().st_size == entry.file_size
            and installed.read_bytes() == archive.read(entry))


def check(command, codex):
    version = manager.version(command)
    if version not in SUPPORTED_MANAGERS:
        raise RuntimeError(f"Hachimodock {version} 尚未适配，已核对的版本为 {' / '.join(SUPPORTED_MANAGERS)}。")
    if hashlib.sha256((ROOT / PACKAGE).read_bytes()).hexdigest() != PACKAGE_SHA256:
        raise RuntimeError("组件文件校验失败，请重新下载完整发行包。")
    if not can_read_compressed():
        raise RuntimeError("缺少 Zstandard 解码器，请使用 Release 内置的 Python 运行。")
    db = codex / "state_5.sqlite"
    if db.is_file():
        from contextlib import closing
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
                connection.execute("SELECT id, rollout_path, updated_at FROM threads LIMIT 0")
        except sqlite3.Error as error:
            raise RuntimeError("本机 Codex 索引暂不可读，请检查所选历史目录。") from error
    manager.invoke(command, "validate", ROOT / PACKAGE)
    return {"managerVersion": version, "codexIndex": "readable" if db.is_file() else "waiting_for_history"}


def stage(base, os_name):
    if not interpreter(ROOT, os_name).is_file():
        raise RuntimeError("请下载并完整解压对应系统的 Release 安装包，其中包含可独立运行的 Python。")
    fingerprint = hashlib.sha256()
    for name in PROGRAMS:
        fingerprint.update((ROOT / name).read_bytes())
    # Associate the installed code with exactly this packaged interpreter build.
    fingerprint.update((ROOT / "runtime-build.txt").read_bytes())
    target = base / "versions" / f"{VERSION}-{fingerprint.hexdigest()[:16]}"
    if target.exists():
        if interpreter(target, os_name).is_file() and all(
            (target / name).is_file() and (target / name).read_bytes() == (ROOT / name).read_bytes()
            for name in PROGRAMS
        ):
            return target
        raise RuntimeError(f"已安装的采集器文件不完整，请检查 {target} 后重试。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / "payload"
        for name in (*PROGRAMS, "runtime-build.txt"):
            dest = staged / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        shutil.copytree(ROOT / "runtime", staged / "runtime", symlinks=True)
        probe = [str(interpreter(staged, os_name)), "-I", "-B", str(staged / "manage.py"), "runtime-check"]
        subprocess.run(probe, check=True, capture_output=True, timeout=30)
        staged.rename(target)
    return target


def install(base, os_name, codex, command):
    base.mkdir(parents=True, exist_ok=True)
    with (base / "install.lock").open("a+b") as guard:
        bridge.lock_collector(guard)
        payload = stage(base, os_name)
        python = str(interpreter(payload, os_name))
        executable = command[0] if len(command) == 2 else None
        if os_name == "macos":
            mac.preflight(mac.locations(), payload / "pc", python, codex, manager_command=command)
        selected = component_state()
        if not selected["componentImported"]:
            if selected["selectedVersion"]:
                try:
                    newer = tuple(map(int, selected["selectedVersion"].split("."))) > tuple(map(int, VERSION.split(".")))
                except ValueError:
                    newer = False
                if newer:
                    raise RuntimeError("本机已选中更新版本的仪表盘，请使用与该版本匹配的 Release。")
            manager.invoke(command, "publish", ROOT / PACKAGE)
            selected = component_state()
            if not selected["componentImported"]:
                raise RuntimeError("管理端导入后未选中本包，请检查「组件中心 → 我的组件」。")
        if os_name == "macos":
            result = mac.install(mac.locations(), payload / "pc", python, codex, manager_path=executable)
        else:
            result = windows_service.install(base, payload, codex, executable)
        return {**selected, **result, "deviceSync": "not_checked"}


def health(os_name, base):
    if os_name == "windows":
        return windows_service.status(base / "health.json")
    paths = mac.locations()
    return {"registered": paths["plist"].is_file(), **mac.job_status(paths, "gui/" + str(os.getuid()))}


def worker(base, codex, explicit):
    path = base / "health.json"
    try:
        if explicit:
            os.environ["HACHIMODOCK_MANAGER"] = str(explicit)
        bridge.main(["--codex-dir", str(codex), "--health-file", str(path)])
    except bridge.CollectorAlreadyRunning:
        # A logon trigger can overlap the worker started by the installer.
        # The lock owner continues writing its own heartbeat.
        return
    except Exception as error:
        bridge.write_health(path, "error", error)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="install",
                        choices=("install", "check", "status", "remove", "worker", "runtime-check"))
    parser.add_argument("--manager", type=Path, help="installed Hachimodock executable or macOS .app")
    parser.add_argument("--codex-dir", type=Path, default=os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    args = parser.parse_args()
    if args.action == "runtime-check":
        from collect import TZ
        from compression import zstd
        assert zstd.decompress(zstd.compress(b"check")) == b"check" and datetime.now(TZ).utcoffset()
        return 0
    os_name = system()
    base = local_root(os_name)
    codex = args.codex_dir.expanduser().resolve()
    if args.action == "worker":
        worker(base, codex, args.manager)
        return 0
    if args.action == "status":
        result = {**component_state(), **health(os_name, base), "deviceSync": "not_checked"}
    elif args.action == "remove":
        base.mkdir(parents=True, exist_ok=True)
        with (base / "install.lock").open("a+b") as guard:
            bridge.lock_collector(guard)
            if os_name == "windows":
                pid = windows_service.read_health(base / "health.json").get("pid") or ""
                windows_service.task("remove", pid=pid)
            else:
                paths = mac.locations()
                if paths["plist"].is_file():
                    mac.stop_job(paths, "gui/" + str(os.getuid()))
                    paths["plist"].unlink()
            result = {"registered": False, "running": False}
    else:
        command = manager.cli(args.manager)
        result = check(command, codex)
        if args.action == "install":
            result.update(install(base, os_name, codex, command))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
