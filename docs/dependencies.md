# 依赖与运行环境

[返回首页](../README.md) · [开发指南](development.md)

## Python 第三方依赖

依赖清单同时适用于 pip 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。直接依赖固定到已验证版本，传递依赖由包管理器根据各包的元数据自动安装。

| 使用场景 | 分发包 | 项目中使用的模块或入口 | 依赖文件 |
| --- | --- | --- | --- |
| 应用运行 | `pywin32==312` | `win32com.client.dynamic`、`pythoncom`；COM DLL 使用 `pywintypes` | `requirements.txt` |
| 打包 | `pyinstaller==6.22.3` | `python -m PyInstaller`、`PyInstaller.archive.readers`、`.spec` 中的构建 API | `requirements-build.txt` |
| 打包校验 | `pefile==2024.8.26` | `tools/verify_build.py` 的 PE 文件与 DLL 导入检查 | `requirements-build.txt` |

`requirements-build.txt` 包含 `requirements.txt`；一次安装前者即可准备运行、测试、打包和打包校验所需的全部 Python 第三方库。PyInstaller 使用的 `altgraph`、`packaging`、`pyinstaller-hooks-contrib`、`pywin32-ctypes`、`setuptools` 等由其依赖声明自动安装。

测试使用 Python 自带的 `unittest` 和 `unittest.mock`。其余应用代码使用标准库与项目内部模块；未使用独立的 `wmi`、`tk`、Pillow、PySide 或 pytest 包，不需要为它们另行安装依赖。pywin32 在虚拟环境内使用，无需执行全局 post-install 脚本。参见 [pywin32 官方安装说明](https://pypi.org/project/pywin32/)。

平台标记仅在 Windows 安装这些包；跳过安装不代表应用支持 Linux、macOS 或 WSL。

## Python 与 Tcl/Tk

从源码运行需要 **64 位 CPython 3.11+**，建议使用 3.12。下载 [Python 官方 Windows 完整安装程序](https://www.python.org/downloads/windows/)，保留 Tcl/Tk、IDLE 和 pip 组件，并让 `python` 命令指向该解释器。

`tkinter/ttk`、`ctypes`、`venv` 等是 Python 标准库，不应写为 PyPI 依赖。`tkinter` 使用的 Tcl/Tk 运行时由完整 Python 安装提供；`requirements.txt` 不能补装缺失的 `_tkinter` 或 Tcl/Tk DLL。最小化/embeddable Python 不适合直接作为本项目的源码运行环境。参见 [Python 官方 Windows 安装说明](https://docs.python.org/3.11/using/windows.html)。

可先运行以下命令检查 GUI 组件，看到测试窗口后将其关闭：

```powershell
python -m tkinter
```

使用 uv 时，本文档中的 `uv venv --python python --no-python-downloads` 明确复用已安装且检查通过的 Python，避免自动选择另一个缺少 GUI 组件的解释器。uv 是可选的环境管理工具，本身不属于应用运行依赖。

## 应用调用的 Windows 组件

| 组件 | 使用位置与用途 | 获取方式 |
| --- | --- | --- |
| Windows PowerShell 5.1 | `writer.py` 启动 `powershell.exe`，执行网络写入和两份配置回读 | Windows 10/11 自带，[微软官方说明](https://learn.microsoft.com/en-us/powershell/scripting/what-is-windows-powershell) |
| NetTCPIP 模块 | `Get-NetIPInterface`、`Set-NetIPInterface` | Windows 系统模块，[微软官方文档](https://learn.microsoft.com/en-us/powershell/module/nettcpip/) |
| WMI / COM | `WbemScripting.SWbemLocator`，查询 `root\StandardCimv2` 的接口、地址与网卡信息 | Windows 自带，WMI 服务需可用，[微软官方说明](https://learn.microsoft.com/en-us/windows/win32/wmisdk/wmi-start-page) |
| Windows DLL | `iphlpapi.dll`、`kernel32.dll`、`shell32.dll`、`user32.dll`、`shcore.dll`，提供路由、权限、单实例与 DPI API | Windows 自带 |

代码使用 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`。目录名 `v1.0` 是 Windows PowerShell 的标准安装路径，不表示只支持 PowerShell 1.0；独立安装的 PowerShell 7（`pwsh.exe`）不能代替这个路径。

本项目不依赖开发者机器上的 Conda、Node.js、浏览器、VPN/代理软件、Visual Studio 或 .NET SDK。源码环境需要 Python；发行版 exe 已包含 Python、pywin32、Tcl/Tk 等运行依赖，仍然依赖上述 Windows 系统组件。修改网络配置需要管理员权限，监控可在普通权限下运行。

## 选择安装范围

```powershell
# 仅运行应用或执行单元测试
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt

# 一次准备运行、测试、打包和打包校验环境
uv pip install --python .\.venv\Scripts\python.exe -r requirements-build.txt
```

不使用 uv 时，改为 `.\.venv\Scripts\python.exe -m pip install -r <对应文件>`。依赖清单不会安装 Windows 组件或修改网络设置。
