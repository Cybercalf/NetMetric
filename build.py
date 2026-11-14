"""构建单文件 GUI exe：python build.py [--read-only]。"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from tools.make_icon import make_icon


def main():
    parser = argparse.ArgumentParser(description="构建 NetMetric Windows x64 单文件程序")
    parser.add_argument("--read-only", action="store_true", help="生成无需 UAC 即可启动的监控版（提权后仍可编辑）")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        parser.error("请在 Windows x64 下用 64 位 Python 构建。")
    root = Path(__file__).resolve().parent
    make_icon(root / "assets/app.ico")
    env = dict(os.environ, NETMETRIC_READONLY="1" if args.read_only else "0")
    result = subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                              "--log-level", "WARN", str(root / "NetMetric.spec")], cwd=root, env=env)
    if result == 0:
        print(root / "dist" / ("NetMetric-ReadOnly.exe" if args.read_only else "NetMetric.exe"))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
