# Exercise the public instructions at the privilege level of a normal desktop app.
param([string]$ContextFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules') + ';' + $env:PSModulePath
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($ContextFile) {
    $scratch = [IO.Path]::GetDirectoryName($ContextFile)
    $log = Join-Path $scratch 'standard-output.txt'
    try {
        if ($isAdmin) { throw 'Windows did not supply a standard user token.' }
        $settings = ConvertFrom-Json ([IO.File]::ReadAllText($ContextFile))
        foreach ($v in $settings.PSObject.Properties) { [Environment]::SetEnvironmentVariable($v.Name, $v.Value, 'Process') }
        'Verified a non-elevated Windows desktop token.' | Out-File -Encoding utf8 $log
        & ([ScriptBlock]::Create([IO.File]::ReadAllText($env:HACHI_TEST_RECIPE))) 2>&1 | Out-File -Append -Encoding utf8 $log
        $code = 0
    } catch {
        ($_ | Out-String) | Out-File -Append -Encoding utf8 $log
        $code = 1
    }
    [IO.File]::WriteAllText((Join-Path $scratch 'standard-exit.txt'), [string]$code)
    return
}

if (-not $isAdmin) {
    Write-Output 'Verified a non-elevated Windows desktop token.'
    & ([ScriptBlock]::Create([IO.File]::ReadAllText($env:HACHI_TEST_RECIPE)))
    return
}
$scratch = [IO.Path]::GetDirectoryName($env:HACHI_TEST_RECIPE)
$output = Join-Path $scratch 'standard-output.txt'
$done = Join-Path $scratch 'standard-exit.txt'
$context = Join-Path $scratch 'standard-environment.json'
$settings = @{}
foreach ($item in Get-ChildItem Env:HACHI_*) { $settings[$item.Name] = $item.Value }
[IO.File]::WriteAllText($context, (ConvertTo-Json $settings))
function Quote-Value([string]$value) { "'" + $value.Replace("'", "''") + "'" }
$command = '& ([ScriptBlock]::Create([IO.File]::ReadAllText(' + (Quote-Value $env:HACHI_TEST_WRAPPER) + '))) ' + (Quote-Value $context)
$action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument ('-NoProfile -NonInteractive -Command "' + $command + '"')
$name = 'HachimoDock installer test ' + [guid]::NewGuid().ToString('N')
$limited = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$null = Register-ScheduledTask -TaskName $name -Action $action -Principal $limited
try {
    Start-ScheduledTask -TaskName $name
    $deadline = (Get-Date).AddMinutes(4)
    while (-not [IO.File]::Exists($done) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    if ([IO.File]::Exists($output)) { [Console]::Write([IO.File]::ReadAllText($output)) }
    if (-not [IO.File]::Exists($done)) { throw "Desktop task did not complete: $((Get-ScheduledTaskInfo -TaskName $name).LastTaskResult)" }
    if ([IO.File]::ReadAllText($done).Trim() -ne '0') { throw 'The standard-user installation failed.' }
} finally {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
