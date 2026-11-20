#!/usr/bin/env python3
"""Install/check/remove this component's macOS collector, with preflight first."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from page_feed import SUPPORTED_MANAGERS

LABEL = "local.codex-observatory.collector"
COLLECTOR_FILES = ("collect.py", "bridge.py", "manager.py", "heatmap.py", "activity_view.py", "quota_layout.py", "home_view.py",
                   "recent_chats.py", "rollouts.py", "weather.py", "codex_view.py", "page_feed.py")
FILES = (*COLLECTOR_FILES, "install_bridge.py")

RETIRED_FILES = ("agents.py", "multi_view.py", "capture_agent.py", "connect_agent.py")


def locations(home=None):
    home = Path(home or Path.home())
    root = home / ".claw-pet/codex-observatory-bridge"
    return {"root": root, "plist": home / "Library/LaunchAgents" / (LABEL + ".plist"),
            "tool": home / ".claw-pet/authoring/petui", "health": root / "health.json"}


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def preflight(paths, source, python, codex_dir, runner=run, system=None, manager_command=None):
    if (system or platform.system()) != "Darwin":
        raise RuntimeError("Windows 用户请运行发行包中的 install-windows.cmd。")
    missing = [name for name in FILES if not (source / name).is_file()]
    if missing:
        raise RuntimeError("采集器文件不完整，请完整解压源码包：" + ", ".join(missing))
    if not Path(python).is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("Python 不可执行，请用 --python 指定 Python 3.9 或更高版本。")
    probe = runner([python, "-c", "import sys,sqlite3,fcntl; from zoneinfo import ZoneInfo; "
                    "assert sys.version_info >= (3,9); ZoneInfo('Asia/Shanghai')"])
    if probe.returncode:
        raise RuntimeError("需要 Python 3.9+，以及 sqlite3、fcntl、zoneinfo 和时区数据。现有采集器未被停止。")
    imports = runner([python, '-B', '-c',
                      'import sys; sys.path.insert(0,sys.argv[1]); import collect,bridge,heatmap,quota_layout', str(source)])
    if imports.returncode:
        raise RuntimeError('新采集器模块加载失败；现有采集器未被停止，请重新下载完整源码包。')
    if any(next((codex_dir / folder).rglob('*.jsonl.zst'), None) for folder in ('sessions', 'archived_sessions')):
        decoder = runner([python, '-B', '-c',
                          'import sys; sys.path.insert(0,sys.argv[1]); from rollouts import can_read_compressed; '
                          'sys.exit(0 if can_read_compressed() else 1)', str(source)])
        if decoder.returncode:
            raise RuntimeError('发现压缩的 Codex 历史。请安装 zstd 或为选定的 Python 安装 zstandard 后重试。')
    if not manager_command and not paths['tool'].is_file():
        raise RuntimeError("未找到 PetUI，请先在 Hachimodock 打开「组件中心 → 创建组件」。")
    info = runner([*(manager_command or [str(paths['tool'])]), 'tool-info'])
    try:
        version = json.loads(info.stdout)['version'] if info.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        version = None
    if version not in SUPPORTED_MANAGERS:
        raise RuntimeError(f"采集器支持 Pet Manager {' / '.join(SUPPORTED_MANAGERS)}，当前版本 {version or '未知'}。")
    db = codex_dir / 'state_5.sqlite'
    if not db.is_file():
        return {"platform":"macOS","petManager":version,"python":python,"index":"waiting for agent history"}
    try:
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=3) as conn:
            conn.execute('SELECT id, rollout_path, updated_at FROM threads LIMIT 0')
    except sqlite3.Error as error:
        raise RuntimeError("Codex 索引不可读或格式不兼容；未安装或更改采集器。") from error
    return {"platform":"macOS", "petManager":version, "python":python, "index":"readable"}


def job_status(paths, target, runner=run):
    result = runner(['launchctl', 'print', target + '/' + LABEL])
    pid = re.search(r'^\s*pid = (\d+)', result.stdout, re.M)
    running = result.returncode == 0 and re.search(r'^\s*state = running\s*$', result.stdout, re.M) is not None
    health = {}
    try:
        health = json.loads(paths['health'].read_text())
    except (OSError, ValueError):
        pass
    if not isinstance(health, dict):
        health = {}
    matching = bool(pid and health.get('pid') == int(pid.group(1)))
    age = max(0, int(time.time()*1000) - health['at']) if matching and type(health.get('at')) is int else None
    return {"running":running, "pid":int(pid.group(1)) if pid else None,
            "collectorPhase":health.get('phase', 'unknown') if matching else 'unknown',
            "healthAgeMs":age, "dataReady":bool(running and matching and age is not None and age < 20000 and health.get('phase') == 'ready')}


def stop_job(paths, target, runner=run):
    result = runner(['launchctl','bootout',target,str(paths['plist'])])
    if result.returncode and runner(['launchctl','print',target+'/'+LABEL]).returncode == 0:
        raise RuntimeError('未能停止现有采集器，保留原文件和自启动配置。')


def atomic_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def install(paths, source, python, codex_dir, runner=run, wait_seconds=20, manager_path=None):
    """Caller must preflight. Stage all inputs before stopping an existing job."""
    staged = {name:(source / name).read_bytes() for name in FILES}
    target = 'gui/' + str(os.getuid())
    root, plist = paths['root'], paths['plist']
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    old = {name:(root / name).read_bytes() if (root / name).is_file() else None for name in (*FILES, *RETIRED_FILES)}
    old_plist = plist.read_bytes() if plist.is_file() else None
    config = {"Label":LABEL, "ProgramArguments":[python, str(root / 'bridge.py'), '--codex-dir', str(codex_dir),
                '--health-file', str(paths['health'])], "RunAtLoad":True, "KeepAlive":{"SuccessfulExit":False},
              "ThrottleInterval":30, "ProcessType":"Background", "StandardOutPath":str(root / 'collector.log'),
              "StandardErrorPath":str(root / 'collector-error.log'), "EnvironmentVariables":{"PYTHONUNBUFFERED":"1"}}
    if manager_path:
        config["EnvironmentVariables"]["HACHIMODOCK_MANAGER"] = str(manager_path)
    for name in ('collector.log','collector-error.log'):
        (root / name).touch(mode=0o600, exist_ok=True)
        (root / name).chmod(0o600)
    if old_plist:
        stop_job(paths, target, runner)
    try:
        for name, data in staged.items():
            atomic_file(root / name, data)
        for name in RETIRED_FILES:
            (root / name).unlink(missing_ok=True)
        atomic_file(plist, plistlib.dumps(config))
        result = runner(['launchctl','bootstrap',target,str(plist)])
        if result.returncode:
            raise RuntimeError('launchctl 未能启动新采集器。')
        deadline = time.monotonic() + wait_seconds
        while True:
            status = job_status(paths, target, runner)
            if status['running'] and status['collectorPhase'] in ('starting','ready','unavailable') and status['healthAgeMs'] is not None and status['healthAgeMs'] < 20000:
                return {"installed":True, "directory":str(root), **status}
            if time.monotonic() >= deadline:
                raise RuntimeError('后台进程未通过启动检查。')
            time.sleep(0.25)
    except Exception as error:
        runner(['launchctl','bootout',target,str(plist)])
        for name, data in old.items():
            if data is None:
                (root / name).unlink(missing_ok=True)
            else:
                atomic_file(root / name, data)
        if old_plist is None:
            plist.unlink(missing_ok=True)
            restoration = '本次安装已撤回'
        else:
            atomic_file(plist, old_plist)
            restored = runner(['launchctl','bootstrap',target,str(plist)])
            restoration = '已恢复原配置并重新启动' if restored.returncode == 0 else '原文件已恢复，但重新启动失败，请查看采集器状态'
        raise RuntimeError(str(error) + '；' + restoration + '。') from error


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='只检查安装条件，不修改任何文件或服务')
    group.add_argument('--remove', action='store_true')
    group.add_argument('--status', action='store_true')
    p.add_argument('--python', default=sys.executable)
    p.add_argument('--codex-dir', type=Path, default=Path.home()/'.codex')
    args = p.parse_args(); paths = locations()
    try:
        if platform.system() != 'Darwin':
            raise RuntimeError('Windows 用户请运行发行包中的 install-windows.cmd。')
        target = 'gui/' + str(os.getuid())
        if args.status:
            status = job_status(paths, target)
            print(json.dumps(status, ensure_ascii=False));return 0 if status['running'] else 1
        if args.remove:
            if paths['plist'].exists():
                stop_job(paths, target)
                paths['plist'].unlink()
            print('采集器已停用；组件、Codex 原始记录和本地数据均保留。');return 0
        source = Path(__file__).resolve().parent
        python = str(Path(shutil.which(args.python) or args.python).expanduser().resolve())
        codex_dir = args.codex_dir.expanduser().resolve()
        checked = preflight(paths, source, python, codex_dir)
        if args.check:
            print(json.dumps({"readyToInstall":True, **checked}, ensure_ascii=False));return 0
        print(json.dumps(install(paths, source, python, codex_dir), ensure_ascii=False));return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr);return 1


if __name__ == '__main__':
    raise SystemExit(main())
