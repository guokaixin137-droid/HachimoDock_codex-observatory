# 帮用户安装 Codex 仪表盘

本仓库供可在用户电脑上运行命令的 Agent 使用。以当前桌面用户身份操作，需要安装的是运行 Codex 和 Hachimodock 的电脑。以下步骤用于用户已请求安装本组件的场景。

1. 识别操作系统与架构。Windows x64 对应 `codex-observatory-3.1.8-windows-x64.zip`，Apple 芯片 Mac 对应 `codex-observatory-3.1.8-macos-arm64.tar.gz`。确认本机已装 Hachimodock。若命令无法定位客户端，先在管理端打开「组件中心 → 创建组件」，或用 `--manager` 传入实际客户端路径。
2. 从 [固定版本 v3.1.8](https://github.com/guokaixin137-droid/HachimoDock_codex-observatory/releases/tag/v3.1.8) 下载相应附件和 `SHA256SUMS`。直链前缀是 `https://github.com/guokaixin137-droid/HachimoDock_codex-observatory/releases/download/v3.1.8/`。对该附件比对 SHA-256，然后解压到单独的可写目录。Windows 可用 `Get-FileHash` 和 `Expand-Archive`，Mac 可用 `shasum -a 256` 和 `tar -xzf`。运行时随系统附件提供，GitHub 的 Source code 压缩包供开发用。
3. 在解压目录运行 `.\install-windows.cmd check` 或 `./install-macos.command check`。确认兼容后，将末尾参数改为 `install` 执行安装。用户指定本机 Codex 历史目录时，对两条命令传递相同的 `--codex-dir`。该流程使用普通用户权限，会导入组件并为当前用户注册后台采集器。
4. 运行同一入口的 `status` 子命令。分别检查 `componentImported`、`running`、`collectorPhase` 和 `dataReady`。初次扫描可间隔数秒再查，缺少记录时请核对本机 Codex 目录。遇到旧版 Windows 前台采集器仍在运行，先请用户关闭那个窗口，再重试。
5. 如已连接 Pico 且有管理端界面控制能力，在「组件中心 → 我的组件 → Codex仪表盘」选择「保存并同步」，查看管理端的完成回执。已同步本版本的设备可直接打开仪表盘。若没有可用设备或界面控制，请告诉用户电脑端的实测状态，以及还需在管理端点的那一步。

反馈问题时，管理端版本号、上述状态字段和安装错误行即够定位常见问题。卸载后台服务使用同一入口的 `remove`。
