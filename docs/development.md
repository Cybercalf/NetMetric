# 开发指南

[返回首页](../README.md) · [使用手册](usage.md)

## 环境准备

先按 [源码运行的前期准备](../README.md#从源码运行)安装包含 Tcl/Tk 的 64 位 CPython 3.11+（推荐 3.12），并保留 Windows PowerShell 5.1、NetTCPIP 和 WMI 系统组件。使用 uv 时可在项目目录一次安装全部开发依赖：

```powershell
uv venv --python python --no-python-downloads .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements-build.txt
.\.venv\Scripts\python.exe main.py
```

`requirements.txt` 声明应用运行与单元测试所需的 pywin32；`requirements-build.txt` 包含前者，并显式声明 PyInstaller 和打包校验使用的 pefile。第三方库和 Windows 组件的完整对应关系见 [依赖说明](dependencies.md)。

没有 uv 时，使用 `python -m venv .venv`，再执行 `.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt`。Python 的 tkinter、ctypes 和 unittest 属于标准库，不需要另装同名 PyPI 包。

## 构建可执行文件

```powershell
.\.venv\Scripts\python.exe build.py
.\.venv\Scripts\python.exe build.py --read-only
```

输出为 `dist/NetMetric.exe` 和 `dist/NetMetric-ReadOnly.exe`。两者均是无控制台单文件程序，自带 Python、COM 和 Tcl/Tk 运行依赖；前者带 `requireAdministrator` 清单，后者使用 `asInvoker`。

也可以使用 PowerShell 入口自动准备虚拟环境和构建依赖。它优先使用已安装的 uv，也兼容 uv 创建的不带 pip 的环境；没有 uv 时使用 venv/pip，并在需要时通过 ensurepip 补齐 pip：

```powershell
.\build.ps1 -PythonPath 'C:\path\to\python.exe'
.\build.ps1 -ReadOnlyBuild
```

`NetMetric.spec` 是维护中的打包源配置，需要加入版本控制。`assets/version_info.txt` 是 PyInstaller 使用的版本资源定义；修改应用版本时同步更新它和 `config.py`。

## 验证

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
# 构建两个 exe 后，检查清单、依赖与打包后的实际运行
.\.venv\Scripts\python.exe -B -X utf8 tools\verify_build.py
```

单元测试使用内存快照和模拟写入，不修改开发机器的网络设置。打包校验会移除 Python/Conda 路径及相关环境变量，在临时目录运行免提权版的诊断和真实 GUI 自检；报告写入 `build/verification.json`。

上述检查不能代替管理员下真实写入、网卡插拔、不同 DPI 和无 Python 的干净 Windows 机器验收。Conda 环境构建时，PyInstaller 可能提示缺少 `conda-meta`；仍应完成运行依赖检查和打包后的自检。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `main.py` | 入口、日志、诊断与单实例启动 |
| `models.py` | 快照、度量语义、修改计划 |
| `netcore.py` | WMI/COM 读取、ctypes 路由与目标选路 |
| `writer.py` | 写入活动/持久配置并回读校验 |
| `controller.py` | 后台采样、串行命令、失败恢复与撤销 |
| `elevate.py` | DPI、权限、单实例锁与 UAC 重启 |
| `preferences.py` | 列布局读取与原子保存 |
| `ui.py` | 表格、筛选、编辑、详情与记录 |

tkinter 仅在主线程操作，WMI COM 连接由后台线程独享，二者通过队列传递普通数据。接口索引只在当前会话使用；写入前重新核对名称和 GUID，写入后分别验证活动、持久配置。

## 图标

`assets/app.ico` 由 `tools/make_icon.py` 使用标准库生成，未使用第三方图片素材；`assets/logo.svg` 是同一网络节点设计的矢量展示，用于项目首页。二者与项目一同使用 MIT 许可。

重新生成应用图标：

```powershell
.\.venv\Scripts\python.exe -B tools\make_icon.py
```

## 文档与生成文件

公开使用和开发文档放在 `docs/`；个人方案、内部验收记录和调研归档放在 `docs/private/`，后者不进入版本控制。根目录保留项目首页和许可证。

构建目录、发行成品、字节码、测试缓存、日志和本地诊断均由 `.gitignore` 排除。新增工具时，应将中间产物放入已有生成目录，或同步增加忽略规则；源配置与图标继续跟踪。
