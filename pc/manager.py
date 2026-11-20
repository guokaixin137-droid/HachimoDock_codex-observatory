"""Find the installed Hachimodock CLI and import the community package."""
import json
import os
from pathlib import Path
import subprocess
import sys


def cli(explicit=None):
    windows = sys.platform == "win32"
    home = Path.home()
    authoring = home / ".claw-pet/authoring"
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        pointer = authoring / "desktop-path"
        if pointer.is_file():
            candidates.append(Path(pointer.read_text(encoding="utf-8-sig").strip()))
        if windows:
            for prefix in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
                if not os.environ.get(prefix):
                    continue
                for parent in (Path(os.environ[prefix]), Path(os.environ[prefix]) / "Programs"):
                    for name in ("Hachimodock", "HachimiDock"):
                        candidates.extend(parent / name / f for f in ("pet-manager-tauri.exe", name + ".exe"))
        else:
            for directory in (Path("/Applications"), home / "Applications"):
                candidates.extend(directory / name for name in ("Hachimodock.app", "HachimiDock.app"))
    for candidate in candidates:
        if not windows and candidate.suffix.casefold() == ".app":
            candidate = candidate / "Contents/MacOS/pet-manager-tauri"
        if candidate.is_absolute() and candidate.is_file() and (not windows or candidate.suffix.casefold() == ".exe"):
            return [str(candidate), "--petui"]
    if not windows and not explicit and (authoring / "petui").is_file():
        return [str(authoring / "petui")]
    raise RuntimeError("请打开 Hachimodock 的「组件中心 → 创建组件」后重试，或用 --manager 指定已安装的客户端。")


def invoke(command, *arguments, timeout=120):
    result = subprocess.run([*command, *map(str, arguments)], capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Hachimodock {arguments[0]} 失败。{detail[:800]}")
    return result.stdout


def version(command):
    return json.loads(invoke(command, "tool-info", timeout=15))["version"]
