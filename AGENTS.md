# 在用户电脑上安装 Codex 仪表盘

用户已要求安装时，请在**运行 Codex 的那台电脑**上，用当前桌面用户的本地终端完成下列操作。成功交付包含 Hachimodock「我的组件」中的 Codex仪表盘，以及登录后自动启动的采集器。Pico 稍后连接也可以先完成电脑端安装。

## 选择环境

- Windows 使用**原生 Windows PowerShell 或 PowerShell**。如果当前终端在 WSL，请转到同一桌面用户的 Windows PowerShell。此包读取 Windows 本机的 Codex 历史。
- Mac 使用本地终端。支持 Apple 芯片 Mac 和 Windows x64，与 Hachimodock 官方客户端提供的架构一致。请先确认 Hachimodock 已安装。如果尚未安装，从官方发布页选择对应平台的客户端，安装后继续。若系统弹出需要本人操作的提示，让用户完成该提示即可。[Hachimodock 官方客户端](https://github.com/ai-maker-lab/HachimoDock/releases/tag/v0.1.245)
- 管理端已核对的版本为 0.1.230、0.1.232、0.1.237、0.1.241、0.1.245。自定义安装位置可用 `HACHI_MANAGER_PATH` 传入客户端可执行文件或 Mac 的 .app。自定义 Codex 历史目录可用 `HACHI_CODEX_DIR` 传入，安装器也会读取现有的 `CODEX_HOME`。

## 运行对应系统的整段命令

在工作区之外的新临时目录下载正式发行包，校验 SHA-256，完整解压后运行内置安装器；下列安装器会调用 Hachimodock 导入组件，同时注册、启动并检查后台采集器。发行包自带 Python，无需 Git、GitHub 登录或额外安装 Python。

### Windows x64 · PowerShell

<!-- install-windows -->
```powershell
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($arch -ne 'AMD64') { throw "This release requires Windows x64 (found $arch)." }

$name = 'codex-observatory-3.1.8-windows-x64.zip'
$url = "https://github.com/guokaixin137-droid/HachimoDock_codex-observatory/releases/download/v3.1.8/$name"
$expected = '632168d7067645d87b2483f5e940ba73195114dbe0dc5ecde84df9e93969cd5e'
$work = Join-Path ([IO.Path]::GetTempPath()) ('HachimoDock Codex ' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $work
$zip = Join-Path $work $name
Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $expected) { throw 'Release checksum mismatch.' }
$payload = Join-Path $work 'payload'
Expand-Archive -LiteralPath $zip -DestinationPath $payload
$installer = Join-Path $payload 'install-windows.cmd'
Write-Output "INSTALLER_PATH=$installer"
$extra = @()
if ($env:HACHI_MANAGER_PATH) { $extra += @('--manager', $env:HACHI_MANAGER_PATH) }
if ($env:HACHI_CODEX_DIR) { $extra += @('--codex-dir', $env:HACHI_CODEX_DIR) }
& $installer install @extra
if ($LASTEXITCODE -ne 0) { throw "Installer exited with code $LASTEXITCODE." }
$report = & $installer status
if ($LASTEXITCODE -ne 0) { throw 'Unable to check installation.' }
$report
$s = $report | ConvertFrom-Json
if (($s.componentImported -ne $true) -or ($s.registered -ne $true) -or ($s.running -ne $true)) {
    throw 'Component import or collector startup is incomplete.'
}
```

### Apple 芯片 Mac · 终端

<!-- install-macos -->
```sh
(
  set -eu
  [ "$(uname -s)" = Darwin ] && [ "$(/usr/sbin/sysctl -n hw.optional.arm64)" = 1 ] || {
    echo 'This release requires an Apple silicon Mac.' >&2
    exit 1
  }
  name=codex-observatory-3.1.8-macos-arm64.tar.gz
  url="https://github.com/guokaixin137-droid/HachimoDock_codex-observatory/releases/download/v3.1.8/$name"
  expected=9dcfff6d85ffbc6ffa12304b28c47e96df7b07c5fd89f91dd77b98903a33a0bc
  work=$(mktemp -d "${TMPDIR:-/tmp}/HachimoDock Codex.XXXXXXXX")
  cd "$work"
  curl --fail --location --retry 3 --silent --show-error "$url" -o "$name"
  printf '%s  %s\n' "$expected" "$name" | /usr/bin/shasum -a 256 -c -
  mkdir payload
  /usr/bin/tar -xzf "$name" -C payload
  installer="$work/payload/install-macos.command"
  printf 'INSTALLER_PATH=%s\n' "$installer"
  set --
  if [ -n "${HACHI_MANAGER_PATH:-}" ]; then set -- "$@" --manager "$HACHI_MANAGER_PATH"; fi
  if [ -n "${HACHI_CODEX_DIR:-}" ]; then set -- "$@" --codex-dir "$HACHI_CODEX_DIR"; fi
  "$installer" install "$@"
  report=$("$installer" status)
  printf '%s\n' "$report"
  printf '%s\n' "$report" | "$work/payload/runtime/bin/python3" -I -B -c '
import json, sys
s = json.load(sys.stdin)
sys.exit(0 if all(s.get(k) is True for k in ("componentImported", "registered", "running"))
         else "Component import or collector startup is incomplete.")
'
)
```

## 安装后的验收与反馈

1. 记录 `INSTALLER_PATH`。状态输出中 `componentImported`、`registered` 和 `running` 都为 `true`，才向用户报告「组件和后台采集器都已安装」。
2. 再检查 `dataReady`。如果为 `false`，用同一入口的 `status` 每 5 秒复查，首次读取大量历史可等数分钟。如果电脑上没有可读的对话，报告「电脑端已安装，等首次本机对话」，请用户在本机 Codex 完成一次对话。已有历史时核对目录和 `collectorPhase`，如实说明仍待解决的项。
3. 如 Pico 已通过 USB 连接，且你可以控制管理端，在「组件中心 → 我的组件 → Codex仪表盘」选择「保存并同步」，等待成功回执。若该版本已经同步，直接打开仪表盘查看。命令行的 `deviceSync: not_checked` 只覆盖电脑端，请单独报告设备同步结果。缺少设备或界面控制时，告诉用户上述入口。
4. 如果安装出错，保留实际错误行。找不到管理端时，打开「组件中心 → 创建组件」或指定 `HACHI_MANAGER_PATH` 再执行。Windows 若还开着旧版前台采集窗口 `Start.cmd`，关闭该窗口后重试。两条命令均可重新执行。

停用后台服务时，可在已解压的发行包中运行同一入口并传入 `remove`。本机 Codex 历史由用户本人管理。
