"""Manage this user's visible Task Scheduler entry for the dashboard."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


TASK_API = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules') + ';' + $env:PSModulePath
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$name = 'HachimoDock Codex Observatory ' + $identity.User.Value
$owner = 'HachimoDock Codex Observatory local collector'
$task = Get-ScheduledTask -TaskPath '\' -TaskName $name -ErrorAction SilentlyContinue
if ($task -and $task.Description -ne $owner) { throw 'A different task already uses this name.' }
function Stop-CollectorTask {
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -InputObject $task
        for ($index = 0; $index -lt 50; $index++) {
            if ((Get-ScheduledTask -TaskPath '\' -TaskName $name).State -ne 'Running') { return }
            Start-Sleep -Milliseconds 200
        }
        throw 'Timed out stopping the existing collector task.'
    }
}
switch ($env:HACHI_OBS_OPERATION) {
    'status' {
        $live = $false
        if ($task -and $task.State -eq 'Running' -and $env:HACHI_OBS_PID) {
            $proc = Get-Process -Id ([int]$env:HACHI_OBS_PID) -ErrorAction SilentlyContinue
            $live = [bool]($proc -and $proc.Path -and $proc.Path -eq $task.Actions[0].Execute)
        }
        ConvertTo-Json -Compress -InputObject @{registered=[bool]$task; running=$live; state=[string]$task.State}
    }
    'save' {
        if ($task) {
            $xml = Export-ScheduledTask -TaskPath '\' -TaskName $name
            [IO.File]::WriteAllText($env:HACHI_OBS_BACKUP,$xml,[Text.Encoding]::Unicode)
        }
    }
    'install' {
        Stop-CollectorTask
        $action = New-ScheduledTaskAction -Execute $env:HACHI_OBS_PYTHON -Argument $env:HACHI_OBS_ARGS
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $null = Register-ScheduledTask -TaskPath '\' -TaskName $name -Description $owner -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
        Start-ScheduledTask -TaskPath '\' -TaskName $name
    }
    'remove' {
        if ($task) {
            Stop-CollectorTask
            Unregister-ScheduledTask -InputObject $task -Confirm:$false
        }
    }
    'restore' {
        Stop-CollectorTask
        $xml = [IO.File]::ReadAllText($env:HACHI_OBS_BACKUP)
        $null = Register-ScheduledTask -TaskPath '\' -TaskName $name -Xml $xml -Force
        Start-ScheduledTask -TaskPath '\' -TaskName $name
    }
}
# Get-ScheduledTask reports a non-terminating error for an absent entry on
# first install. Only a terminating error should fail the completed operation.
exit 0
"""


def task(operation, **values):
    env = {**os.environ, "HACHI_OBS_OPERATION": operation}
    env.update({"HACHI_OBS_" + key.upper(): str(value) for key, value in values.items()})
    exe = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(exe), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", TASK_API],
                            capture_output=True, env=env, timeout=45,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        detail = (result.stderr.strip() or result.stdout.strip()).decode("utf-8", errors="replace")
        raise RuntimeError(f"Windows 任务计划程序 {operation} 失败（{result.returncode}）。{detail[:1000]}")
    if operation == "status":
        return json.loads(result.stdout)


def read_health(path):
    try:
        data = json.loads(path.read_bytes())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def status(health):
    entry = read_health(health)
    state = task("status", pid=entry.get("pid") or "")
    at = entry.get("at")
    fresh = bool(state["running"] and type(at) is int and 0 <= int(time.time() * 1000) - at < 20_000)
    return {**state, "collectorPhase": entry.get("phase", "unknown") if state["running"] else "stopped",
            "dataReady": fresh and entry.get("phase") == "ready", "detail": entry.get("detail") if fresh else None}


def install(root, payload, codex, manager):
    health = root / "health.json"
    args = ["-I", "-B", "-X", "utf8", str(payload / "manage.py"), "worker", "--codex-dir", str(codex)]
    if manager:
        args.extend(["--manager", str(manager)])
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        backup = Path(tmp) / "task.xml"
        task("save", backup=backup)
        started = int(time.time() * 1000)
        try:
            task("install", python=payload / "runtime/pythonw.exe", args=subprocess.list2cmdline(args))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                entry = read_health(health)
                if type(entry.get("at")) is int and entry["at"] >= started:
                    if entry.get("phase") == "error":
                        raise RuntimeError(entry.get("detail") or "采集器启动失败。")
                    state = status(health)
                    if state["running"] and state["collectorPhase"] in ("starting", "unavailable", "ready"):
                        return state
                time.sleep(0.35)
            raise RuntimeError("任务未通过启动检查。如果旧版 Start.cmd 仍在采集，请关闭它后重试。")
        except Exception:
            if backup.exists():
                task("restore", backup=backup)
            else:
                task("remove")
            raise
