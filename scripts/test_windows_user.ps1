# Provision a plain Users-group account only inside the disposable Windows CI VM.
$ErrorActionPreference = 'Stop'
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules') + ';' + $env:PSModulePath
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$name = 'HachiQA' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$secret = ConvertTo-SecureString ([guid]::NewGuid().ToString('N') + 'aA9!') -AsPlainText -Force
$user = New-LocalUser -Name $name -Password $secret -Description 'Ephemeral installer acceptance test'
try {
    $users = Get-LocalGroup -SID 'S-1-5-32-545'
    if (-not (Get-LocalGroupMember -Group $users | Where-Object SID -eq $user.SID)) {
        Add-LocalGroupMember -Group $users -Member $user
    }
    $public = [Environment]::GetFolderPath('CommonDocuments')
    $stage = Join-Path $public ('HachimoQA-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $stage
    & icacls.exe $stage /grant ('*' + $user.SID.Value + ':(OI)(CI)M') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the temporary test directory.' }
    $null = New-Item -ItemType Directory -Path (Join-Path $stage 'scripts'), (Join-Path $stage 'pc')
    Copy-Item -LiteralPath (Join-Path $env:HACHI_TEST_REPO 'AGENTS.md') -Destination $stage
    Copy-Item -LiteralPath (Join-Path $env:HACHI_TEST_REPO 'scripts/test_release_install.py') -Destination (Join-Path $stage 'scripts')
    Copy-Item -LiteralPath (Join-Path $env:HACHI_TEST_REPO 'pc/windows_service.py') -Destination (Join-Path $stage 'pc')
    $arguments = '-u "' + (Join-Path $stage 'scripts/test_release_install.py') + '" --standard-desktop "' + $stage + '"'
    $out = Join-Path $stage 'result.log'
    $err = Join-Path $stage 'error.log'
    $credential = [pscredential]::new(($env:COMPUTERNAME + '\' + $name), $secret)
    $process = Start-Process -FilePath $env:HACHI_TEST_PYTHON -ArgumentList $arguments -Credential $credential -LoadUserProfile -WorkingDirectory $stage -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    if (-not $process.WaitForExit(360000)) { throw 'The standard-user install exceeded six minutes.' }
    $process.Refresh()
    foreach ($log in $out, $err) {
        if ([IO.File]::Exists($log)) { [Console]::Write([IO.File]::ReadAllText($log)) }
    }
    if ($process.ExitCode -ne 0) { throw "Standard-user acceptance returned $($process.ExitCode)." }
} finally {
    Remove-LocalUser -SID $user.SID
}
