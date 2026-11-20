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
$entry = Join-Path $scratch 'start-standard.cmd'
$line = '"' + (Join-Path $PSHOME 'powershell.exe') + '" -NoProfile -NonInteractive -Command "' + $command + '"'
[IO.File]::WriteAllText($entry, "@echo off`r`n" + $line + "`r`n", [Text.Encoding]::ASCII)
# Hosted images can disable UAC splitting. Request the built-in Basic User
# level explicitly and verify the resulting identity in the child above.
$runAs = Join-Path $env:SystemRoot 'System32/runas.exe'
& $runAs /trustlevel:0x20000 ('cmd.exe /d /c ""' + $entry + '""')
if ($LASTEXITCODE -ne 0) { throw "Windows could not start the standard user test ($LASTEXITCODE)." }
$deadline = (Get-Date).AddMinutes(4)
while (-not [IO.File]::Exists($done) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
if ([IO.File]::Exists($output)) { [Console]::Write([IO.File]::ReadAllText($output)) }
if (-not [IO.File]::Exists($done)) { throw 'The standard-user desktop test did not complete.' }
if ([IO.File]::ReadAllText($done).Trim() -ne '0') { throw 'The standard-user installation failed.' }
