# HachimoDock · Codex 仪表盘

在 Pico 上查看本机最近三条 Codex 对话的标题与状态、今天及近七天的 Token 用量，以及最近记录的剩余额度和重置时间。短按 SW1 切换额度与历史页。

## 把这句话发给电脑上的 Agent

> 请从 https://github.com/guokaixin137-droid/HachimoDock_codex-observatory 安装 Codex 仪表盘。按仓库里的 AGENTS.md 选择本机安装包，安装登录后自动运行的采集器，把组件导入 Hachimodock，并检查数据是否就绪。如果 Pico 已连接，请通过管理端完成同步并告诉我结果。

请在平时运行 **Codex 和 Hachimodock 的那台电脑**上发给能操作本机的 Agent。支持 Windows x64 和 Apple 芯片 Mac。两个发行包均内置 Python。已核对 Hachimodock 0.1.230、0.1.232、0.1.237、0.1.241 和 0.1.245 的组件数据格式。

## 自己安装

1. 打开 Hachimodock，用 USB 连接 Pico。从 [v3.1.8 下载页](https://github.com/guokaixin137-droid/HachimoDock_codex-observatory/releases/tag/v3.1.8) 选对应系统的包，完整解压。
2. 在解压目录运行下表的安装命令。安装器会把组件导入「我的组件」，同时安装当前用户的后台采集器。之后登录电脑会自动启动。
3. 在 Hachimodock 打开「组件中心 → 我的组件 → Codex仪表盘」，选择「保存并同步」。在 Pico 上打开仪表盘即可。使用时保持管理端与 Pico 连接。

| 操作 | Windows（PowerShell） | Mac（终端） |
| --- | --- | --- |
| 检查安装条件 | `.\install-windows.cmd check` | `./install-macos.command check` |
| 安装或重新安装 | `.\install-windows.cmd install` | `./install-macos.command install` |
| 查看状态 | `.\install-windows.cmd status` | `./install-macos.command status` |
| 停止采集并移除登录启动项 | `.\install-windows.cmd remove` | `./install-macos.command remove` |

## 如何判断安装成功

- `componentImported` 为 `true` 表示电脑上的组件库已选中对应版本。`running` 为 `true` 表示采集器正在运行。`dataReady` 为 `true` 表示最近一次采集已读到本机数据。
- 首次扫描大量历史可能需要几分钟。若提示没有可读的历史，请在这台电脑的 Codex 完成一次对话。自定义历史目录可通过 `CODEX_HOME` 或安装命令的 `--codex-dir` 指定。Windows 发行包面向 Windows 本机保存的记录。
- `deviceSync: not_checked` 表示命令行没有读取 Pico 的同步回执。请以 Hachimodock 的同步结果为准。如果详情提供「准备采集器契约」，可在导入或更新后点一次。旧管理端同时刷新和翻页时可能需要再按一次 SW1。
- 若 Windows 上之前开着便携版的 `Start.cmd`，请先关闭旧窗口。Mac 安装器会更新同名的用户级 LaunchAgent。

## 本机数据与后台服务

采集器只读 Codex 会话索引和日志，生成对话标题、状态、用量及额度。仪表盘数据保存在本机，由 Hachimodock 发给 Pico。天气按管理端所在地向 Open-Meteo 查询，只发送城市级坐标，可在 Hachimodock 设置里关闭定位。额度取自 Codex 最近一次写入的观测。

Windows 的后台项位于「任务计划程序」中，名称以 `HachimoDock Codex Observatory` 开头。Mac 的 LaunchAgent 名为 `local.codex-observatory.collector`。移除后台服务后，如需删除组件，可在 Hachimodock 组件中心移除。运行文件分别保存在 `%LOCALAPPDATA%\HachimoDock\CodexObservatory` 和 `~/.claw-pet/codex-observatory-bridge/`。
