"""Run the exact AGENTS.md installation recipe on a fresh GitHub-hosted desktop."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[1]
HOST_RELEASE = "https://github.com/ai-maker-lab/HachimoDock/releases/download/v0.1.245/"
HOST_FILES = {
    "windows": ("HachimiDock_0.1.245_Windows-x64-setup.exe", "526a7b2d017d1712190c75fc9cd69618dcad6e501be127570382fbbcb0ebc6b3"),
    "macos": ("HachimiDock_0.1.245_macOS-arm64.dmg", "c8c539f4c6b1d0ff62345dab22cba72832d1702c814a90526369d93db7ae0007"),
}


def install_official_client(system, scratch):
    name, digest = HOST_FILES[system]
    package = scratch / name
    subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--silent", "--show-error",
                    "--output", str(package), HOST_RELEASE + name], check=True, timeout=240)
    with package.open("rb") as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    if checksum.hexdigest() != digest:
        raise RuntimeError("The official Hachimodock package checksum differs.")
    if system == "windows":
        subprocess.run([str(package), "/S"], check=True, timeout=300)
    else:
        mount = scratch / "image"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(package)],
                       check=True, timeout=120)
        try:
            source, = mount.glob("*.app")
            applications = Path.home() / "Applications"
            applications.mkdir(exist_ok=True)
            subprocess.run(["ditto", str(source), str(applications / source.name)], check=True, timeout=120)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, timeout=60)


def seed_codex(root):
    now = datetime.now(timezone.utc)
    thread = "00000000-0000-0000-0000-000000000001"
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    rollout = sessions / f"rollout-{now:%Y-%m-%d}-{thread}.jsonl"
    record = {"type": "token_usage_record", "timestamp": now.isoformat(),
              "payload": {"response_id": "fixture", "usage": {"input_tokens": 42, "output_tokens": 8}}}
    rollout.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with sqlite3.connect(root / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id TEXT, title TEXT, rollout_path TEXT, updated_at INTEGER)")
        db.execute("INSERT INTO threads VALUES (?,?,?,?)",
                   (thread, "Desktop release fixture", str(rollout), int(now.timestamp())))


def install_recipe(system, scratch):
    docs = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    pattern = rf"<!-- install-{system} -->\s*```\w+\n(.*?)\n```"
    commands = re.search(pattern, docs, re.DOTALL)
    if not commands:
        raise RuntimeError(f"Missing {system} recipe in AGENTS.md")
    script = scratch / ("recipe.ps1" if system == "windows" else "recipe.sh")
    script.write_text(commands.group(1) + "\n", encoding="utf-8-sig" if system == "windows" else "utf-8")
    if system == "windows":
        # Read the documented commands in the system's native Windows PowerShell 5.1.
        args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                "& ([ScriptBlock]::Create([IO.File]::ReadAllText($env:HACHI_TEST_RECIPE)))"]
    else:
        args = ["/bin/sh", str(script)]
    env = {**os.environ, "HACHI_CODEX_DIR": str(scratch / "Codex sample"), "HACHI_TEST_RECIPE": str(script)}
    run = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         encoding="utf-8", errors="replace", timeout=360)
    print(run.stdout, flush=True)
    run.check_returncode()
    entry = re.search(r"^INSTALLER_PATH=(.+)$", run.stdout, re.MULTILINE)
    if not entry:
        raise RuntimeError("Installer path missing from the documented recipe")
    return Path(entry.group(1).strip())


def run_installer(installer, *args):
    if sys.platform == "win32":
        inputs = {"HACHI_TEST_INSTALLER": str(installer), "HACHI_TEST_ARGS": json.dumps(args)}
        command = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                   "$options = @(ConvertFrom-Json $env:HACHI_TEST_ARGS); "
                   "& $env:HACHI_TEST_INSTALLER @options; exit $LASTEXITCODE")
        invocation = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        inputs = {}
        invocation = [str(installer), *args]
    run = subprocess.run(invocation, env={**os.environ, **inputs}, capture_output=True,
                         encoding="utf-8", errors="replace", timeout=120)
    if run.returncode:
        raise RuntimeError(run.stderr or run.stdout)
    return json.loads(run.stdout)


def wait_for_data(installer):
    deadline = time.monotonic() + 100
    while True:
        result = run_installer(installer, "status")
        if all(result.get(k) is True for k in ("registered", "running", "componentImported", "dataReady")):
            print("Verified import, login registration, live collector and current data:", result, flush=True)
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Data did not become ready: {result}")
        time.sleep(3)


def main():
    if (os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("RUNNER_TEMP")
            or os.environ.get("RUNNER_ENVIRONMENT") == "self-hosted"):
        raise SystemExit("This acceptance test requires a disposable GitHub-hosted runner.")
    system = {"darwin": "macos", "win32": "windows"}[sys.platform]
    print(f"{platform.platform()} / {platform.machine()}", flush=True)
    if (Path.home() / ".claw-pet/components/selected/codex-observatory.json").exists():
        raise RuntimeError("A fresh component library is required.")
    desktop_data = (Path(os.environ["APPDATA"]) if system == "windows" else
                    Path.home() / "Library/Application Support") / "com.petmanager.desktop"
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as directory:
        scratch = Path(directory)
        install_official_client(system, scratch)
        seed_codex(scratch / "Codex sample")
        installer = install_recipe(system, scratch)
        initial = wait_for_data(installer)
        telemetry_file = desktop_data / "component-apps/codex-observatory/composition-v2-state.json"
        telemetry = json.loads(telemetry_file.read_bytes())["models"]["telemetry"]["value"]
        overview = telemetry["pixel_overview"]
        if (overview["v4"], overview["v5"]) != ("42", "8") or "Desktop release fixture" not in json.dumps(telemetry):
            raise RuntimeError("The collector did not deliver the expected fixture title and token counts.")
        print("Verified the actual Hachimodock data file: fixture title, 42 input tokens, 8 output tokens.", flush=True)
        # An existing community installation should retain its selection on another install.
        repeat = run_installer(installer, "install", "--codex-dir", str(scratch / "Codex sample"))
        if repeat.get("selectedHash") != initial["selectedHash"] or not repeat.get("running"):
            raise RuntimeError("Reinstallation lost the component selection or the collector.")
        relocated = installer.parent.with_name(installer.parent.name + " moved")
        installer.parent.rename(relocated)
        installer = relocated / installer.name
        wait_for_data(installer)
        run_installer(installer, "remove")
        removed = run_installer(installer, "status")
        if removed["registered"] or removed["running"] or not removed["componentImported"]:
            raise RuntimeError(f"Unexpected state after collector removal: {removed}")
        print("Verified reinstall, moving the download, and stopping the login service.", flush=True)


if __name__ == "__main__":
    main()
