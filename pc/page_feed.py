"""Publish complete dashboard screens without touching the manager's navigation file."""
import json
import os
from pathlib import Path
import re
import tempfile

ENGINE = "pc-composition-snapshot-feed-v1"
FEED = "composition-snapshot-feed.json"
RECEIPT = "snapshot-feed-host.json"
# The v2 composition reader has the same SHA-256 in these public releases.
SUPPORTED_MANAGERS = ("0.1.230", "0.1.232", "0.1.237", "0.1.241", "0.1.245")
WINDOWS_MANAGERS = SUPPORTED_MANAGERS


def selected_contract(root, selected):
    """Only use the contract exported by the host for the selected package."""
    try:
        receipt = json.loads((root / RECEIPT).read_bytes())
        package = json.loads(selected.read_bytes())
    except FileNotFoundError:
        return None
    if receipt.get("packageHash") != package.get("hash"):
        return None
    support = receipt.get("support") or {}
    if (receipt.get("schema") != 1 or receipt.get("componentId") != root.name
            or not re.fullmatch("[a-f0-9]{16}", package.get("hash") or "")
            or support.get("engine") != ENGINE
            or not re.fullmatch("[a-f0-9]{64}", support.get("contract") or "")):
        raise ValueError("管理端提供的页面缓存契约无效")
    return support["contract"]


def publish(root, contract, screens, at, heartbeat_ms, detailed_home=None):
    path = root / FEED
    old = {}
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= 32768:
        try:
            old = json.loads(path.read_bytes())
        except ValueError:
            pass  # A complete replacement also recovers a truncated feed.
    if (isinstance(old, dict) and old.get("engine") == ENGINE and old.get("contract") == contract
            and isinstance(old.get("documents"), dict)):
        previous = old["documents"].get("telemetry")
        last = old.get("generatedAt")
        if isinstance(previous, dict) and type(last) is int and previous.get("at") == last:
            if at <= last or (at-last < heartbeat_ms and previous.get("screens") == screens
                              and previous.get("detailedHome") == detailed_home):
                return False
    packet = {"engine":ENGINE,"contract":contract,"generatedAt":at,
              "documents":{"telemetry":{"at":at,"cached":True,"screens":screens}}}
    if detailed_home is not None:
        packet["documents"]["telemetry"]["detailedHome"] = detailed_home
    data = json.dumps(packet, ensure_ascii=False, separators=(",",":")).encode()
    if len(data) > 32768:
        raise ValueError("页面快照超出宿主预算")
    fd, temp = tempfile.mkstemp(prefix=".screen-", dir=root)
    try:
        with os.fdopen(fd,"wb") as out:
            out.write(data)
        os.replace(temp,path)
    finally:
        Path(temp).unlink(missing_ok=True)
    return True
