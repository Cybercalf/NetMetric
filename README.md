<p align="center">
  <img src="assets/logo.svg" alt="NetMetric logo" width="88" height="88">
</p>

<h1 align="center">NetMetric</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Windows-10%20%2F%2011-0078D4" alt="Windows 10 / 11">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB" alt="Python 3.11+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22A675" alt="MIT License"></a>
</p>

<p align="center">
  <a href="#功能">功能</a> · <a href="#快速开始">快速开始</a> · <a href="docs/usage.md">使用手册</a> · <a href="docs/development.md">开发指南</a>
</p>

NetMetric 是一个轻量的 Windows IPv4 网络接口度量工具，帮助你查看接口状态和路由优先级，并在多网卡环境中调整接口度量。

> A lightweight Windows tool to monitor IPv4 route metrics and manage network interface priorities.

## 功能

- **实时监控**：每秒查看连接状态、接口度量、路由度量和有效度量。
- **调整优先级**：修改接口度量、设为首选、恢复自动跃点，并支持变更记录和撤销。
- **查看选路**：按状态筛选、搜索接口、查看完整路由，并查询指定 IPv4 目标的系统选路。
- **灵活表格**：支持数值排序、列宽自动适配和布局保存。
- **免安装运行**：可打包为自带 Python 运行时的单文件程序。

## 快速开始

支持 **Windows 10 1809+ / Windows 11 x64**，当前仅处理 IPv4。

在本项目 GitHub 仓库的 **Releases** 页面找到最新版本，下载 Windows x64 的 exe 文件，双击即可运行，无需安装 Python：

| 程序 | 使用方式 |
| --- | --- |
| `NetMetric.exe` | 启动时请求管理员权限，可监控并修改设置 |
| `NetMetric-ReadOnly.exe` | 普通权限下监控，需要修改时可从界面以管理员重启 |

选择已连接的接口，双击「接口度量」输入 **1–9999**，按 Enter 确认。数值越小，接口优先级越高。

> 修改需要管理员权限，主要影响新建连接。VPN、代理、应用绑定和 IPv6 流量可能采用不同出口。详见[使用手册](docs/usage.md)。

## 从源码运行

先在 Windows 上准备以下环境：

| 前期准备 | 官方来源与要求 |
| --- | --- |
| Python | 从 [Python 官方下载页](https://www.python.org/downloads/windows/)安装 **64 位 CPython 3.11+**（推荐 3.12），保留 **Tcl/Tk、IDLE 和 pip** 组件，并将 `python` 加入 PATH |
| uv（推荐，可用 pip 替代） | 按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装，或从 [uv 官方 Releases](https://github.com/astral-sh/uv/releases/latest) 下载 Windows x64 程序并加入 PATH |
| Windows 系统组件 | 保留系统自带的 **Windows PowerShell 5.1、NetTCPIP 模块和 WMI**，无需另行安装；[微软官方说明](https://learn.microsoft.com/en-us/powershell/scripting/what-is-windows-powershell)。PowerShell 7 不能替代代码调用的 Windows PowerShell 路径 |

运行 `python -m tkinter`，确认能打开测试窗口后将其关闭。进入下载或解压后的项目目录，用 uv 一次安装全部运行依赖：

```powershell
uv venv --python python --no-python-downloads .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

<details>
<summary>使用 pip 的等价步骤</summary>

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

</details>

需要同时准备打包和校验环境时，将安装命令中的 `requirements.txt` 换成 `requirements-build.txt`；后者已经包含运行依赖。项目不依赖 Conda、Node.js 等额外第三方应用，详细依赖清单见[依赖说明](docs/dependencies.md)，构建和测试见[开发指南](docs/development.md)。

## 许可证

[MIT](LICENSE) · Copyright © 2026 Cybercalf
