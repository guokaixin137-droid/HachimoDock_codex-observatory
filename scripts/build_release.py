"""Package the dashboard and verified portable runtimes for GitHub Releases."""
import argparse
import copy
import hashlib
import io
from pathlib import Path, PurePosixPath
import posixpath
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAC = "f15ec3d15460dd66c2eb88639b152cd9040e7d1ece11e38bf89e8f53f0171e02"
WIN = "3c01d7cfc666f746a6f064f60fb2156ab0ec464d3d738ab796371a46a9bb24a2"
VERSION = "3.1.8"
COMMON = ["README.md", "AGENTS.md", "THIRD_PARTY.md", "manage.py", "codex-observatory.hachiapp", "zones/Asia/Shanghai"]
PC = ["activity_view", "bridge", "codex_view", "collect", "heatmap", "home_view", "install_bridge", "manager",
      "page_feed", "quota_layout", "recent_chats", "rollouts", "weather", "windows_service"]
COMMON += ["pc/" + module + ".py" for module in PC]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_bytes(tar, name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.mode, info.size = mode, len(data)
    tar.addfile(info, io.BytesIO(data))


def build(mac, windows, out):
    if digest(mac) != MAC or digest(windows) != WIN:
        raise ValueError("The runtime hashes must match the pinned upstream releases.")
    out.mkdir(parents=True, exist_ok=True)
    win_out = out / f"codex-observatory-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(windows) as runtime, zipfile.ZipFile(win_out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in runtime.namelist():
            dst.writestr("runtime/" + name, runtime.read(name))
        for name in COMMON:
            dst.write(ROOT / name, name)
        cmd = (ROOT / "install-windows.cmd").read_text(encoding="utf-8").replace("\n", "\r\n")
        dst.writestr("install-windows.cmd", cmd.encode())
        dst.writestr("runtime-build.txt", WIN + "\n")
    mac_out = out / f"codex-observatory-{VERSION}-macos-arm64.tar.gz"
    with tarfile.open(mac, "r:gz") as runtime, tarfile.open(mac_out, "w:gz", compresslevel=6) as dst:
        for source in runtime:
            parts = PurePosixPath(source.name).parts
            if not parts or parts[0] != "python" or ".." in parts:
                raise ValueError("Unexpected runtime path")
            member = copy.copy(source)
            member.name = PurePosixPath("runtime", *parts[1:]).as_posix()
            if source.islnk():
                member.linkname = source.linkname.replace("python/", "runtime/", 1)
            if source.issym():
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                if not resolved.startswith("runtime/"):
                    raise ValueError("Runtime symlink points outside the bundle")
            dst.addfile(member, runtime.extractfile(source) if source.isfile() else None)
        for name in COMMON:
            add_bytes(dst, name, (ROOT / name).read_bytes())
        add_bytes(dst, "install-macos.command", (ROOT / "install-macos.command").read_bytes(), 0o755)
        add_bytes(dst, "runtime-build.txt", (MAC + "\n").encode())
    paths = [mac_out, win_out, ROOT / "codex-observatory.hachiapp"]
    (out / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in paths), encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac-runtime", type=Path, required=True)
    parser.add_argument("--windows-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    opts = parser.parse_args()
    build(opts.mac_runtime, opts.windows_runtime, opts.output)
