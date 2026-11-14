"""NetMetric GUI 入口；--diagnose 提供不修改网络设置的 JSON 自检。"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from config import APP_TITLE, VERSION


def configure_logging():
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NetMetric"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / "netmetric.log", maxBytes=1_000_000,
                                      backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler],
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
    except OSError:
        logging.basicConfig(handlers=[logging.NullHandler()])


def diagnose(output: str | None):
    from netcore import SystemReader
    from elevate import is_admin
    with SystemReader() as reader:
        snapshot = reader.collect()
        data = {"version": VERSION, "admin": is_admin(), **asdict(snapshot)}
        try:
            data["best_route_1_1_1_1"] = asdict(reader.best_route("1.1.1.1"))
        except OSError as exc:
            data["best_route_1_1_1_1"] = None
            data["route_check_error"] = str(exc)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    elif sys.stdout is not None:
        print(text)
    else:
        raise ValueError("窗口版请使用 --diagnose --output <文件路径>。")


def gui_self_test(output: str | None):
    """用真实采样启动完整 Tk 界面，确认首帧后自动退出；不执行写操作。"""
    import time
    from elevate import enable_dpi_awareness
    from ui import App
    enable_dpi_awareness()
    app = App(remember_layout=False)
    result = {}
    deadline = time.monotonic() + 15

    def check():
        if app.last is not None:
            result.update(version=VERSION, frozen=bool(getattr(sys, "frozen", False)),
                          tk_version=app.tk.call("info", "patchlevel"),
                          admin=app.admin, interfaces=len(app.last.ifaces), routes=len(app.last.routes),
                          table_rows=len(app.tree.get_children()), warnings=app.last.warnings,
                          window=[app.winfo_width(), app.winfo_height()],
                          table=[app.tree.winfo_width(), app.tree.winfo_height()],
                          default_exits=[i.alias for i in app.last.default_exits])
            app.close()
        elif time.monotonic() >= deadline:
            result["error"] = app.message.get()
            app.close()
        else:
            app.after(100, check)

    app.after(100, check)
    app.mainloop()
    if not result or "error" in result:
        raise RuntimeError(result.get("error", "界面自检被中断。"))
    if result["table_rows"] != result["interfaces"] or result["table"][1] < 100:
        raise RuntimeError(f"表格渲染自检未通过：{result}")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    elif sys.stdout is not None:
        print(text)
    else:
        raise ValueError("窗口版请使用 --self-test --output <文件路径>。")


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--diagnose", action="store_true", help="只读采集一次并输出 JSON")
    parser.add_argument("--self-test", action="store_true", help="以真实数据启动界面，首帧自检后自动退出（不修改设置）")
    parser.add_argument("--output", help="诊断 JSON 输出路径")
    parser.add_argument("--wait-parent", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    configure_logging()
    if os.name != "nt":
        if sys.stderr:
            print("NetMetric 仅支持 Windows 10/11 x64。", file=sys.stderr)
        return 1
    if args.diagnose or args.self_test:
        try:
            if args.self_test:
                gui_self_test(args.output)
            else:
                diagnose(args.output)
            return 0
        except Exception:
            logging.exception("只读诊断失败")
            if sys.stderr:
                import traceback
                traceback.print_exc()
            return 1

    from elevate import SingleInstance, enable_dpi_awareness, wait_for_parent
    import tkinter as tk
    from tkinter import messagebox
    enable_dpi_awareness()
    instance = None
    app = None
    try:
        if args.wait_parent:
            wait_for_parent(args.wait_parent)
        instance = SingleInstance()
        if instance.already_running:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_TITLE, "NetMetric 已在运行，请切换到现有窗口。", parent=root)
            root.destroy()
            return 0
        from ui import App
        app = App()
        app.mainloop()
        return 0
    except Exception as exc:
        logging.exception("应用启动失败")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_TITLE, f"启动失败：{exc}\n\n详细日志：%LOCALAPPDATA%\\NetMetric\\netmetric.log", parent=root)
            root.destroy()
        except Exception:
            pass
        return 1
    finally:
        if app:
            app.controller.stop()
        if instance:
            instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
