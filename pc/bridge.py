#!/usr/bin/env python3
"""Project local Codex metrics, display titles and city weather for Pet Manager."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from collect import Collector, TZ, timestamp
from heatmap import grid_levels, rolling_grid_levels, month_labels, encode, empty_rows
from quota_layout import ordered_windows, dual_mode, dual_payload
from home_view import HOME_RANGE, DATE, TEMP, chart_line, display_text
from weather import Weather
from codex_view import dashboard_pages
from activity_view import DETAILED_HOME_RANGE, community_pages, detailed_home, device_pages
from page_feed import FEED, SUPPORTED_MANAGERS, WINDOWS_MANAGERS, selected_contract, publish

COMPONENT_ID = "codex-observatory"


def manager_data_root():
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "com.petmanager.desktop"
    return Path.home() / "Library/Application Support/com.petmanager.desktop"


APP_ROOT = manager_data_root()
STATE_ROOT = APP_ROOT / "component-apps" / COMPONENT_ID
STATE_NAME = "composition-v2-state.json"
SELECTED = Path.home() / ".claw-pet/components/selected" / (COMPONENT_ID + ".json")
TTL_MS = 45000
HEARTBEAT_MS = 30000
STATUS = {
    "working": ("正在工作", "正在执行当前任务"),
    "idle": ("暂未工作", "当前没有执行中的任务"),
    "waiting": ("等待处理", "有会话正在等待你的处理"),
    "approval": ("等待授权", "有操作正在等待你的确认"),
    "error": ("需要检查", "有会话报告了执行错误"),
    "unknown": ("状态未知", "暂未取得可判断的会话状态"),
    "disconnected": ("连接中断", "请保持 Pet Manager 运行"),
}


def compact(n):
    if type(n) not in (int, float) or n < 0:
        return "—"
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= scale:
            return f"{n / scale:.2f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


def empty_view(page=0):
    if page == 0:
        return dict(mode=-2, v1="等待数据", v2="等待本地采集器连接", v3="—",
                    v4="—", v5="—", v6="本机统计", v7="")
    rows = empty_rows()
    return dict(mode=-1, v1="—", v2="额度数据暂不可用", v3=week_line({}),
                **{f"v{i+4}": row for i, row in enumerate(rows)})


def week_line(week):
    return "".join(compact(week.get(key)).ljust(11) for key in ("input", "output", "total")).rstrip()


def empty_rolling_view():
    view = empty_view(1)
    view['v1'] = ''  # Month labels need a current clock, not a build-time date.
    return view


def quota_text(window, stale):
    if not window:
        return "—", "账户未提供额外周期", -1
    value = window.get("remainingPercent")
    reset = timestamp(window.get("resetsAt"))
    valid = window.get("status") == "observed" and type(value) in (int, float) and 0 <= value <= 100
    label = window.get("label", "额度")
    remaining = f"{value:g}%" if valid else "—"
    reset_text = reset.astimezone(TZ).strftime("%m/%d %H:%M") if reset else "时间未知"
    detail = f"{reset_text}  {label}"
    if stale:
        detail = f"旧观测 · {reset_text}"
    if window.get("status") == "awaiting_reset_refresh":
        detail = "已到重置时间 · 等待新观测"
    return remaining, detail, round(value) if valid and not stale else -1


def legacy_home(snapshot, local, current):
    # Codex-only 2.x devices still understand the original numeric/glyph layout.
    home = dict(current)
    chat_states = {"done":1,"waiting":2,"error":3,"working":4}
    rows = snapshot.get("recentChats") or []
    a,b,c = ([chat_states.get(row.get("state"),0) for row in rows[:3]] + [0,0,0])[:3]
    weather = snapshot.get("weather") or {}
    code, temperature = weather.get("code"), weather.get("temperature")
    band = (1 if code in (0,1) else 2) if type(code) is int else 0
    pair = c + 8 * ((b-c) % 7)
    home["mode"] = 280*(1+band) + pair + 56*((a-pair)%5)
    temp = str(round(temperature))+"°C" if type(temperature) in (int,float) and -100 <= temperature <= 70 else "—"
    home["v1"] = (local.strftime("%H:%M") + chr(0xe020+local.weekday()) +
                  "".join(DATE[c] for c in local.strftime("%Y.%m.%d")) + " "*4 + "".join(TEMP[c] for c in temp))
    home["v2"] = display_text(weather.get("label") or "天气暂不可用")
    stats = snapshot.get("statistics") or {}
    home["v7"] = chart_line(local.date(), stats.get("days",{}) if snapshot.get("available") else {})
    return home


def document(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(TZ)
    overview, quota = empty_view(), empty_view(1)
    agent = snapshot.get("agent", {}).get("state", "unknown")
    title, detail = STATUS.get(agent, STATUS["unknown"])
    overview.update(v1=title, v2=detail, v6=local.strftime("%m/%d %H:%M"))
    statistics = snapshot.get("statistics", {})
    if snapshot.get("available"):
        today, week = statistics.get("today", {}), statistics.get("week", {})
        overview.update(v3=compact(today.get("total")), v4=compact(today.get("input")), v5=compact(today.get("output")))
        quota.update(v3=week_line(week))
    else:
        overview["v2"] = "本机记录不完整 · 暂无总量"
    history = statistics.get("history", {})
    if history.get("available"):
        quota.update({f"v{i+4}": row for i, row in enumerate(encode(grid_levels(history.get("days", {}), local.date())))})
    limits = snapshot.get("quota", {})
    windows = limits.get("windows", [])
    q1 = quota_text(windows[0] if windows else None, limits.get("status") == "stale")
    quota.update(v1=q1[0], v2=q1[1], mode=q1[2])
    rolling = empty_rolling_view()
    rolling.update(v1=month_labels(local.date()), v2=q1[1], v3=quota['v3'])
    if q1[0].endswith('%'):
        # The numeric variable also supplies the percentage, freeing one text
        # slot for calendar labels. Old observations remain explicitly labelled.
        rolling['mode'] = round(float(q1[0][:-1]))
    if history.get('available'):
        rolling.update({f'v{i+4}': row for i, row in enumerate(encode(
            rolling_grid_levels(history.get('days', {}), local.date())))})
    adaptive = copy.deepcopy(rolling)
    ordered = ordered_windows(limits)
    if not ordered:
        adaptive['v2'] = '额度数据暂不可用'
    if len(ordered) == 2:
        adaptive['mode'] = dual_mode(ordered, limits.get('status') == 'stale')
        adaptive['v2'] = dual_payload(ordered, generic=adaptive['mode'] >= 104)
    # Keep the old projection while the device may still be running 1.0.x.
    # The new package selects pixel_*; publishing alone must not corrupt the
    # older device's labels or expose the heatmap encoding as ordinary text.
    old_overview = dict(mode=-2, v1=title, v2=detail,
                        v3="今日 Token · " + local.strftime("%m/%d"),
                        v4=overview["v4"], v5=overview["v5"], v6="本机统计", v7="01 / 02")
    q2 = quota_text(windows[1] if len(windows) > 1 else None, limits.get("status") == "stale")
    week = statistics.get("week", {}) if snapshot.get("available") else {}
    old_quota = dict(mode=q1[2], v1=q1[0], v2=q1[1], v3=q2[0], v4=q2[1],
                     v5=compact(week.get("input")), v6=compact(week.get("output")), v7=compact(week.get("total")))
    pages = dashboard_pages(snapshot, local)
    result = {"at": int(now.timestamp() * 1000), "overview": old_overview, "quota": old_quota,
              "pixel_overview": overview, "pixel_quota": quota, "rolling_quota": rolling,
              "adaptive_quota": adaptive, "home_v2":legacy_home(snapshot, local, pages["home"]), "dashboard":pages}
    result["community"] = community_pages(pages, statistics, local, windows)
    result["screens"] = device_pages(result["community"], ordered)
    result["detailedHome"] = detailed_home(result["screens"]["home"], snapshot)
    validate_document(result)
    return result


def validate_document(value):
    legacy = {"at", "overview", "quota", "pixel_overview", "pixel_quota", "rolling_quota", "adaptive_quota", "home_v2"}
    assert set(value) == legacy | {"dashboard", "community", "screens", "detailedHome"}
    assert type(value["at"]) is int and 0 <= value["at"] < 2**53
    def check(view, low, high, optional=()):
        required = {"mode", *[f"v{i}" for i in range(1, 8)]}
        assert required <= set(view) <= required.union(optional)
        assert type(view["mode"]) is int and low <= view["mode"] <= high
        for key, text in view.items():
            if key != "mode":
                assert isinstance(text,str) and len(text.encode()) <= 63
                assert not any(ord(c)<32 or 127<=ord(c)<=159 for c in text)
    for name in legacy - {"at"}:
        check(value[name],*( (280,1119) if name=="home_v2" else (-2,106 if name=="adaptive_quota" else 100) ))
    check(value["detailedHome"], *DETAILED_HOME_RANGE, optional=("t4","t5","t6"))
    for pages in (value["dashboard"], value["community"], value["screens"]):
        assert set(pages) == {"home","quota"}
        check(pages["home"], *HOME_RANGE)
        check(pages["quota"], -5, 106)
    assert len(json.dumps(value, ensure_ascii=False,separators=(",",":")).encode()) < 8192


def new_state():
    return {"version": 1, "models": {
        "telemetry": {"kind": "document", "value": {"at": 0, "overview": empty_view(), "quota": empty_view(1)}},
        "nav": {"kind": "document", "value": {"page": 0}},
    }}


def publish_snapshot(root, snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    contract = selected_contract(root, SELECTED)
    if contract:
        if snapshot.get("error"):
            return {"written": False, "reason": "source_unavailable"}
        local = now.astimezone(TZ)
        windows = ordered_windows(snapshot.get("quota") or {})
        pages = community_pages(dashboard_pages(snapshot,local),snapshot.get("statistics") or {},local,windows)
        screens = device_pages(pages, windows)
        written = publish(root,contract,screens,int(now.timestamp()*1000),HEARTBEAT_MS, detailed_home(screens["home"], snapshot))
        return {"written":written,"transport":"page-cache"}
    # Clients without the cache extension consume the original document projection.
    return write_document(root,document(snapshot,now))


def validate_state(state):
    if not isinstance(state, dict) or state.get("version") != 1 or set(state) - {"version", "models", "receipts", "runs"}:
        raise ValueError("Unrecognized state format; existing file preserved")
    models = state.get("models")
    if not isinstance(models, dict) or set(models) not in ({"telemetry","nav"},{"telemetry","nav","focus"}):
        raise ValueError("Unexpected document owners; existing file preserved")
    if models["telemetry"].get("kind") != "document" or models["nav"].get("kind") != "document":
        raise ValueError("Unexpected model type; existing file preserved")
    page = models["nav"].get("value", {}).get("page")
    if type(page) is not int or page not in (0, 1):
        raise ValueError("Invalid navigation state; existing file preserved")
    if "focus" in models:
        model = models["focus"]
        idx = model.get("value",{}).get("agent")
        if model.get("kind")!="document" or type(idx) is not int or idx not in range(4):
            raise ValueError("Invalid agent selection; existing file preserved")
    if state.get("runs"):
        raise ValueError("Unexpected workflow state; existing file preserved")


def signature(path):
    try:
        s = path.stat()
        return s.st_ino, s.st_mtime_ns, s.st_size
    except FileNotFoundError:
        return None


def write_document(root, value, before_commit=None):
    """Merge our telemetry only. Preserve host navigation and command receipts.

    Read/recheck/atomic rename avoids torn reads and retries observed host writes.
    Pet Manager has no interprocess transaction API: this is a version-specific
    adapter; either writer can win the final rename race, losing a concurrent
    navigation/receipt update as well as telemetry. Telemetry is
    regenerable and refreshed, and no other component data is touched.
    """
    validate_document(value)
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = root / STATE_NAME
    if target.is_symlink():
        raise ValueError("State must be a regular file")
    for attempt in range(8):
        prior = signature(target)
        if prior and prior[2] > 65536:
            raise ValueError("Oversized state; existing file preserved")
        state = json.loads(target.read_bytes()) if prior else new_state()
        validate_state(state)
        previous = state["models"]["telemetry"].get("value", {})
        age = value["at"] - previous.get("at", 0)
        if prior and "focus" not in state["models"] and 0 <= age < HEARTBEAT_MS and all(previous.get(k) == v for k,v in value.items() if k != "at") and previous.keys() == value.keys():
            # The composition context hashes the persisted document. A timestamp
            # write alone must not needlessly invalidate a physical key press.
            return {"written": False, "reason": "unchanged", "page": state["models"]["nav"]["value"]["page"]}
        state["models"].pop("focus", None)  # Retire the 3.0 source selector during upgrade.
        state["models"]["telemetry"]["value"] = copy.deepcopy(value)
        data = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > 65536:
            raise ValueError("State budget exceeded; existing file preserved")
        fd, tmp = tempfile.mkstemp(prefix=".telemetry-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if before_commit:
                before_commit(attempt)
            if signature(target) != prior:
                continue
            os.replace(tmp, target)
            return {"written": True, "page": state["models"]["nav"]["value"]["page"], "bytes": len(data)}
        finally:
            Path(tmp).unlink(missing_ok=True)
    raise RuntimeError("Host state is changing; deferred until next sample")


def windows_manager_version(authoring):
    # Read the running client's PE product version even on installations that
    # have never opened the authoring tools. All PowerShell input is static.
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    query = """
        $session = (Get-Process -Id $PID).SessionId
        $versions = @(Get-Process -Name 'Hachimodock','HachimiDock','pet-manager-tauri' -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $session } |
            ForEach-Object { $_.MainModule.FileVersionInfo } |
            Where-Object { $_ -and ($_.ProductMajorPart -or $_.ProductMinorPart -or $_.ProductBuildPart) } |
            ForEach-Object { '{0}.{1}.{2}' -f $_.ProductMajorPart,$_.ProductMinorPart,$_.ProductBuildPart } |
            Sort-Object -Unique)
        ConvertTo-Json -InputObject $versions -Compress
    """
    if shell.is_file():
        try:
            result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", query],
                                    capture_output=True, timeout=10, check=True)
            versions = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            versions = []
        if not isinstance(versions, list):
            versions = []
        if len(versions) > 1:
            raise RuntimeError("检测到多个版本的 Hachimodock，请只保留当前连接 Pico 的管理端。")
        if versions:
            return versions[0]
    # Fall back to the same executable path used by the official PetUI launcher.
    pointer = authoring / "desktop-path"
    if pointer.is_file():
        desktop = Path(pointer.read_text(encoding="utf-8-sig").strip())
        if desktop.is_absolute() and desktop.suffix.lower() == ".exe" and desktop.is_file():
            result = subprocess.run([str(desktop), "--petui", "tool-info"], capture_output=True, timeout=10, check=True)
            return json.loads(result.stdout).get("version")
    raise RuntimeError("请先打开 Hachimodock。若已打开，请进入一次「组件中心 → 创建组件」，再启动采集器。")


def verify_version():
    import manager
    authoring = Path.home() / ".claw-pet/authoring"
    configured = os.environ.get("HACHIMODOCK_MANAGER")
    if configured and Path(configured).is_file():
        version = manager.version(manager.cli(configured))
    elif sys.platform == "win32":
        version = windows_manager_version(authoring)
    else:
        version = manager.version(manager.cli())
    supported = WINDOWS_MANAGERS if sys.platform == "win32" else SUPPORTED_MANAGERS
    if version not in supported:
        raise RuntimeError(f"当前管理端版本 {version}，此采集器适配 {' / '.join(supported)}。")
    return version


class CollectorAlreadyRunning(RuntimeError):
    pass


def lock_collector(guard):
    try:
        if sys.platform == "win32":
            import msvcrt
            # Windows can lock a byte beyond EOF. Every instance locks byte zero.
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise CollectorAlreadyRunning("采集器已经在运行，请查看先前打开的窗口。") from error
        raise


def write_health(path, phase, detail=None):
    if path is None:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.health-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            health = {'pid':os.getpid(), 'at':int(time.time()*1000), 'phase':phase}
            if detail:
                health["detail"] = str(detail)[:200]
            json.dump(health, f)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main(argv=None, *, console=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=STATE_ROOT)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--check", action="store_true")
    default_codex = os.environ.get("CODEX_HOME") if sys.platform == "win32" else None
    parser.add_argument("--codex-dir", type=Path, default=default_codex or Path.home()/'.codex')
    parser.add_argument("--health-file", type=Path)
    args = parser.parse_args(argv)
    version=verify_version()
    if args.check:
        path = args.state_dir / STATE_NAME
        contract = selected_contract(args.state_dir, SELECTED)
        if contract:
            # The cache host owns navigation. Before the first button press it
            # can still be at its declared initial page without a state file.
            state = json.loads(path.read_bytes()) if path.exists() else {"models": {}}
            page = state["models"].get("nav", {}).get("value", {}).get("page", 0)
            packet = json.loads((args.state_dir / FEED).read_bytes())
            if packet.get("contract") != contract:
                raise ValueError("页面快照尚未更新到当前组件版本")
            value = packet["documents"]["telemetry"]
        else:
            state = json.loads(path.read_bytes())
            validate_state(state)
            page = state["models"]["nav"]["value"]["page"]
            value = state["models"]["telemetry"]["value"]
        print(json.dumps({"componentId": COMPONENT_ID, "stateFile": str(path), "ageMs": int(time.time()*1000)-value["at"],
                          "page": page, "deviceDeliveryVerified": False}))
        return
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.state_dir / "collector.lock").open("a+b") as guard:
        lock_collector(guard)
        collect_until_stopped(args, version, console)


def collect_until_stopped(args, version, console):
    collector = Collector(args.codex_dir)
    weather = Weather(APP_ROOT / "shared-location.json")
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    write_health(args.health_file, 'starting')
    last_message = None
    if console:
        print(f"Hachimodock {version} · 正在读取 {collector.codex_dir}", flush=True)
        print("首次扫描可能需要几分钟。使用仪表盘时请保留此窗口，按 Ctrl+C 可停止。", flush=True)
    else:
        print(json.dumps({"bridge": "started", "componentId": COMPONENT_ID, "version": version, "stateDir": str(args.state_dir)}), flush=True)
    while running:
        started = time.monotonic()
        try:
            weather.snapshot()
            snapshot = collector.collect(include_status=False)
            snapshot["weather"] = weather.snapshot()
            result = publish_snapshot(args.state_dir, snapshot)
            write_health(args.health_file, 'ready' if snapshot.get('available') else 'unavailable')
            if snapshot.get("error"):
                message = "尚未读到本机 Codex 历史索引。请确认上方目录，并在 Windows 的 Codex 完成一次对话。采集器会自动重试。"
            elif not snapshot.get("available"):
                message = "已更新可读数据，部分会话记录缺失或暂时无法读取。"
            else:
                message = "采集器已就绪。请保持 Hachimodock 连接 Pico，在设备上打开「Codex仪表盘」。"
            if args.once:
                print(json.dumps(result), flush=True)
        except Exception as error:
            write_health(args.health_file, 'error', error)
            message = f"采集失败，将自动重试。{error}"
            if not console:
                print(json.dumps({"bridge": "error", "type": type(error).__name__, "message": str(error)[:160]}), flush=True)
            if args.once:
                raise
        if console and message != last_message:
            print(message, flush=True)
            last_message = message
        if args.once:
            break
        while running and time.monotonic() - started < max(2, args.interval):
            time.sleep(0.2)


if __name__ == "__main__":
    main()
