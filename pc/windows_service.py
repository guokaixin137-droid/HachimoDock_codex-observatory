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
function Get-CollectorProcess([switch]$All) {
    if (-not $task -or $task.Actions.Count -ne 1) { return }
    $action = $task.Actions[0]
    if ($All) { $candidates = @(Get-Process -Name pythonw -ErrorAction SilentlyContinue) }
    elseif ($env:HACHI_OBS_PID -match '^[1-9][0-9]*$') {
        $candidates = @(Get-Process -Id $env:HACHI_OBS_PID -ErrorAction SilentlyContinue)
    }
    else { return }
    foreach ($proc in $candidates) {
        if ($proc.Path -ne $action.Execute) { continue }
        # The heartbeat belongs to this user's installed collector. Basic users
        # can query their process directly even when WMI hides ExecutablePath.
        if ($proc.Id.ToString() -eq $env:HACHI_OBS_PID) { $proc; continue }
        $row = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $proc.Id) -ErrorAction SilentlyContinue
        if ($row.CommandLine -and $row.CommandLine.EndsWith(' ' + $action.Arguments, [StringComparison]::Ordinal)) {
            $proc
        }
    }
}
function Stop-CollectorTask {
    if (-not $task) { return }
    $processes = @(Get-CollectorProcess -All)
    if ($task.State -eq 'Running') { Stop-ScheduledTask -InputObject $task }
    foreach ($proc in $processes) {
        if (-not $proc.HasExited) {
            try { $proc.Kill() } catch [InvalidOperationException] { }
            if (-not $proc.WaitForExit(5000)) { throw 'Timed out stopping the existing collector process.' }
        }
    }
    for ($index = 0; $index -lt 50; $index++) {
        if ((Get-ScheduledTask -TaskPath '\' -TaskName $name).State -ne 'Running') { return }
        Start-Sleep -Milliseconds 200
    }
    throw 'Timed out stopping the existing collector task.'
}
switch ($env:HACHI_OBS_OPERATION) {
    'status' {
        $live = @(Get-CollectorProcess).Count -gt 0
        $result = @{registered=[bool]$task; running=$live; state=[string]$task.State}
        if ($env:HACHI_OBS_PYTHON) {
            $result.sameAction = [bool]($task -and $task.Actions.Count -eq 1 -and
                $task.Actions[0].Execute -eq $env:HACHI_OBS_PYTHON -and $task.Actions[0].Arguments -ceq $env:HACHI_OBS_ARGS)
        }
        if ($task) { $result.lastResult = (Get-ScheduledTaskInfo -TaskPath '\' -TaskName $name).LastTaskResult }
        ConvertTo-Json -Compress -InputObject $result
    }
    'save' {
        if ($task) {
            $xml = Export-ScheduledTask -TaskPath '\' -TaskName $name
            [IO.File]::WriteAllText($env:HACHI_OBS_BACKUP,$xml,[Text.Encoding]::Unicode)
        }
    }
    'install' {
        Stop-CollectorTask
        if ($task) { Unregister-ScheduledTask -InputObject $task -Confirm:$false }
        $action = New-ScheduledTaskAction -Execute $env:HACHI_OBS_PYTHON -Argument $env:HACHI_OBS_ARGS
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $null = Register-ScheduledTask -TaskPath '\' -TaskName $name -Description $owner -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
    }
    'remove' {
        if ($task) {
            Stop-CollectorTask
            Unregister-ScheduledTask -InputObject $task -Confirm:$false
        }
    }
    'restore' {
        Stop-CollectorTask
        if ($task) { Unregister-ScheduledTask -InputObject $task -Confirm:$false }
        $xml = [IO.File]::ReadAllText($env:HACHI_OBS_BACKUP)
        $null = Register-ScheduledTask -TaskPath '\' -TaskName $name -Xml $xml -Force
        $restored = Get-ScheduledTask -TaskPath '\' -TaskName $name
        $launch = [Diagnostics.ProcessStartInfo]::new($restored.Actions[0].Execute, $restored.Actions[0].Arguments)
        $launch.UseShellExecute = $false
        $launch.CreateNoWindow = $true
        [Diagnostics.Process]::Start($launch).Dispose()
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


def status(health, **expected_action):
    entry = read_health(health)
    state = task("status", pid=entry.get("pid") or "", **expected_action)
    at = entry.get("at")
    age = int(time.time() * 1000) - at if type(at) is int else None
    fresh = bool(state["running"] and age is not None and 0 <= age < 20_000)
    return {**state, "collectorPhase": entry.get("phase", "unknown") if state["running"] else "stopped",
            "healthAgeMs": age, "dataReady": fresh and entry.get("phase") == "ready",
            "detail": entry.get("detail") if fresh else None}


def start_worker(executable, arguments):
    # A secondary logon can register an interactive task, but Task Scheduler
    # cannot start it until that user's next desktop login. Run the same action
    # in the installing user's session without inheriting the agent's pipes.
    return subprocess.Popen([str(executable), *arguments], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def install(root, payload, codex, manager):
    health = root / "health.json"
    args = ["-I", "-B", "-X", "utf8", str(payload / "manage.py"), "worker", "--codex-dir", str(codex)]
    if manager:
        args.extend(["--manager", str(manager)])
    action = {"python": payload / "runtime/pythonw.exe", "args": subprocess.list2cmdline(args)}
    old_pid = read_health(health).get("pid") or ""
    active_phases = ("starting", "unavailable", "ready")
    if old_pid:
        existing = status(health, **action)
        age = existing["healthAgeMs"]
        if (existing.get("sameAction") and existing["running"] and existing["collectorPhase"] in active_phases
                and age is not None and 0 <= age < 20_000):
            return existing
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        backup = Path(tmp) / "task.xml"
        task("save", backup=backup)
        started = int(time.time() * 1000)
        try:
            task("install", pid=old_pid, **action)
            process = start_worker(action["python"], args)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                entry = read_health(health)
                if type(entry.get("at")) is int and entry["at"] >= started:
                    if entry.get("phase") == "error":
                        raise RuntimeError(entry.get("detail") or "采集器启动失败。")
                    state = status(health)
                    if state["running"] and state["collectorPhase"] in active_phases:
                        return state
                time.sleep(0.35)
            state = status(health)
            entry = read_health(health)
            raise RuntimeError(f"任务未通过启动检查（{state.get('state')}，结果 {state.get('lastResult')}，"
                               f"进程 {process.pid} 退出码 {process.poll()}，心跳 {entry.get('pid')} / {entry.get('phase')}）。"
                               "如旧版 Start.cmd 仍在采集，请关闭它后重试。")
        except Exception:
            last_pid = read_health(health).get("pid") or old_pid
            if backup.exists():
                task("restore", backup=backup, pid=last_pid)
            else:
                task("remove", pid=last_pid)
            raise
