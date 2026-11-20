"""Read Codex's plain and Zstandard-compressed local rollout streams."""
import contextlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    from compression import zstd as _zstd
except ImportError:
    try:
        import zstandard as _zstd
    except ImportError:
        _zstd = None

UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
ROLLOUT = re.compile(rf"^rollout-.+?-({UUID})(?:_({UUID}))?\.jsonl(?:\.zst)?$", re.I)

def zstd_command():
    bundled = Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin/zstd"
    for entry in (shutil.which("zstd"), "/opt/homebrew/bin/zstd", "/usr/local/bin/zstd", bundled):
        if entry and Path(entry).is_file() and os.access(entry, os.X_OK):
            return str(entry)
    return None

def can_read_compressed():
    return _zstd is not None or zstd_command() is not None

@contextlib.contextmanager
def open_rollout(path):
    path = Path(path)
    if path.suffix != ".zst":
        with path.open("rb") as stream:
            yield stream
        return
    if _zstd is not None:
        try:
            with _zstd.open(path, "rb") as stream:
                yield stream
        except (EOFError, getattr(_zstd, "ZstdError", OSError)) as error:
            raise OSError("Invalid compressed Codex log") from error
        return
    command = zstd_command()
    if not command:
        raise OSError("Reading .jsonl.zst requires Python Zstandard support or zstd")
    with subprocess.Popen([command, "-qdc", "--", str(path)], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            yield process.stdout
        finally:
            process.stdout.close()
        if process.wait():
            raise OSError("Could not decompress Codex log")

def windows_index_path(filename):
    """Match Rust's canonical Windows paths to the session directory spelling."""
    value = os.fspath(filename)
    if value.casefold().startswith("\\\\?\\unc\\"):
        return "\\\\" + value[8:]
    if re.match(r"^\\\\\?\\[A-Za-z]:\\", value):
        return value[4:]
    return value


def resolve_rollout(filename, roots):
    if not filename:
        return None
    path = Path(windows_index_path(filename) if sys.platform == "win32" else filename)
    plain = path.with_suffix("") if path.name.endswith(".jsonl.zst") else path
    for candidate in (plain, Path(str(plain) + ".zst")):
        target = candidate.resolve()
        if any(root in target.parents for root in roots) and target.is_file():
            return target
    return None

def history_files(roots, rows, since):
    """Enumerate every session stream, including resumed runs of the same thread."""
    found = {}
    for root in roots:
        for candidate in root.rglob("rollout-*.jsonl*"):
            match = ROLLOUT.fullmatch(candidate.name)
            if not match or candidate.is_symlink() or root not in candidate.resolve().parents or not candidate.is_file():
                continue
            found.setdefault(match[1].lower(), set()).add(candidate.resolve())
    for thread, name, updated in rows:
        paths = found.pop(thread.lower(), set())
        indexed_path = resolve_rollout(name, roots)
        if indexed_path:
            paths.add(indexed_path)
        if not paths:
            yield thread, None, updated, True
        for path in sorted(paths):
            yield thread, path, updated, True
    for thread, paths in found.items():
        for path in sorted(paths):
            try:
                updated = path.stat().st_mtime
            except OSError:
                continue
            if updated >= since:
                yield thread, path, updated, False
