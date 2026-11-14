"""tkinter/ttk 主界面。全部控件操作均在 Tk 主线程执行。"""
from __future__ import annotations

from datetime import datetime
import ipaddress
import logging
import os
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from config import APP_TITLE, VERSION
from controller import Controller
from elevate import is_admin, relaunch_as_admin
from models import ChangePlan, Iface, Snapshot, metric_plan, preferred_plan, validate_metric
from preferences import Preferences

HEADERS = {
    "dot": "连接", "alias": "接口名称", "description": "网卡描述", "state": "状态",
    "metric": "接口度量", "auto": "自动跃点", "gateway": "默认网关",
    "route_metric": "路由度量", "effective": "有效度量", "routes": "路由数",
}
COLUMNS = tuple(HEADERS)
FILTERS = ("全部接口", "已连接", "未连接", "有默认路由")
COLUMN_WIDTHS = {"dot": 48, "alias": 130, "description": 230, "state": 76, "metric": 86,
                 "auto": 78, "gateway": 130, "route_metric": 86, "effective": 86, "routes": 66}
COLUMN_MINIMUMS = {"dot": 36, "alias": 64, "description": 100, "state": 56, "metric": 64,
                   "auto": 64, "gateway": 80, "route_metric": 64, "effective": 64, "routes": 54}


def number(value):
    return "—" if value is None else str(value)


class App(tk.Tk):
    def __init__(self, controller=None, admin: bool | None = None, *,
                 preferences: Preferences | None = None, remember_layout: bool = True):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x740")
        self.minsize(980, 600)
        self.configure(background="#f3f5f8")
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "app.ico"
        if icon.exists():
            self.iconbitmap(str(icon))
        self.admin = is_admin() if admin is None else admin
        self.controller = controller or Controller()
        self.preferences = preferences or Preferences()
        self.remember_layout = remember_layout
        self.layout = self.preferences.load() if remember_layout else {}
        self.resizing_column = False
        self.column_save_id = None
        self.column_dialog = None
        self.last: Snapshot | None = None
        self.history = ()
        self.busy = False
        self.paused = False
        self.sort_column = "alias"
        self.sort_reverse = False
        self.editor = None
        self.history_window = None
        self.route_window = None
        self.message = tk.StringVar(value="正在读取本机 IPv4 接口…")
        self.sample_status = tk.StringVar(value="等待首次采样")
        self.filter_var = tk.StringVar(value=FILTERS[0])
        self.search_var = tk.StringVar()
        self.show_route_metric = tk.BooleanVar(value=self.layout.get("show_route_metric", True) is not False)
        self.detail_var = tk.StringVar(value="选择一个接口查看 IPv4 地址、DHCP 与默认路由。")
        self.summary_vars = [tk.StringVar(value="—") for _ in range(4)]
        self.exit_var = tk.StringVar(value="默认出口：等待采样")
        self._build_styles()
        self._build_ui()
        self._build_menu()
        self.search_var.trace_add("write", lambda *_: self.render())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<F5>", lambda _: self.refresh())
        self.bind("<Control-f>", lambda _: self.search_entry.focus_set())
        self.bind("<Control-z>", lambda _: self.undo())
        self.drain_id = self.after(100, self.drain)
        self.controller.start()

    def _build_styles(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#f3f5f8")
        style.configure("TLabel", background="#f3f5f8", foreground="#26344b")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"), foreground="#102b4e")
        style.configure("Sub.TLabel", foreground="#69788c", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TFrame", background="white")
        style.configure("Card.TLabel", background="white", foreground="#69788c")
        style.configure("Value.TLabel", background="white", foreground="#102b4e", font=("Segoe UI", 23, "bold"))
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=32,
                        background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", padding=(10, 5))
        style.configure("Invalid.TSpinbox", fieldbackground="#ffe5e5", foreground="#b42318")

    def _build_ui(self):
        header = ttk.Frame(self, padding=(20, 15, 20, 8))
        header.pack(fill="x")
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text="NetMetric", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=f"网络接口度量监控器   /   IPv4   ·   v{VERSION}", style="Sub.TLabel").pack(anchor="w")
        access = ttk.Frame(header)
        access.pack(side="right")
        ttk.Label(access, text="管理员 · 可修改" if self.admin else "只读模式 · 监控正常",
                  foreground="#08795c" if self.admin else "#9a6700").pack(anchor="e")
        if not self.admin:
            ttk.Button(access, text="以管理员重启", command=self.elevate).pack(anchor="e", pady=(5, 0))
        cards = ttk.Frame(self, padding=(20, 6, 20, 8))
        cards.pack(fill="x")
        for index, label in enumerate(("IPv4 接口", "已连接", "未连接", "默认路由")):
            cards.columnconfigure(index, weight=1, uniform="cards")
            card = ttk.Frame(cards, style="Card.TFrame", padding=(16, 8))
            card.grid(row=0, column=index, sticky="ew", padx=(0, 10 if index < 3 else 0))
            ttk.Label(card, textvariable=self.summary_vars[index], style="Value.TLabel").pack(anchor="w")
            ttk.Label(card, text=label, style="Card.TLabel").pack(anchor="w")
        toolbar = ttk.Frame(self, padding=(20, 8))
        toolbar.pack(fill="x")
        self.filter_box = ttk.Combobox(toolbar, textvariable=self.filter_var, values=FILTERS,
                                       state="readonly", width=13)
        self.filter_box.pack(side="left")
        self.filter_box.bind("<<ComboboxSelected>>", lambda _: self.render())
        ttk.Label(toolbar, text="搜索").pack(side="left", padx=(14, 6))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=25)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(toolbar, text="路由度量", variable=self.show_route_metric,
                        command=self.toggle_route_metric).pack(side="left", padx=10)
        column_button = ttk.Menubutton(toolbar, text="列宽 ▾")
        column_button.pack(side="left", padx=(0, 6))
        column_button.configure(menu=self.column_menu(column_button))
        self.refresh_button = ttk.Button(toolbar, text="刷新 F5", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(0, 6))
        self.pause_button = ttk.Button(toolbar, text="暂停", command=self.toggle_pause)
        self.pause_button.pack(side="left")
        exit_bar = ttk.Frame(self, padding=(20, 2, 20, 8))
        exit_bar.pack(fill="x")
        ttk.Label(exit_bar, textvariable=self.exit_var, foreground="#08795c").pack(side="left")
        ttk.Label(exit_bar, text="● 已连接有默认路由   ● 已连接无默认路由   ○ 未连接",
                  style="Sub.TLabel").pack(side="right")

        table = ttk.Frame(self, padding=(20, 0))
        table.pack(fill="both", expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", selectmode="browse", height=10)
        saved = self.layout.get("columns", {})
        if not isinstance(saved, dict):
            saved = {}
        for col in COLUMNS:
            self.tree.heading(col, text=HEADERS[col], command=lambda c=col: self.sort_by(c))
            width = saved.get(col, COLUMN_WIDTHS[col])
            if type(width) is not int or not COLUMN_MINIMUMS[col] <= width <= 1200:
                width = COLUMN_WIDTHS[col]
            self.tree.column(col, width=width, minwidth=COLUMN_MINIMUMS[col], stretch=False,
                             anchor="w" if col in ("alias", "description", "gateway") else "center")
        self.tree.configure(displaycolumns=self.visible_columns())
        self.tree.grid(row=0, column=0, sticky="nsew")
        vscroll = ttk.Scrollbar(table, orient="vertical", command=self._yscroll)
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(table, orient="horizontal", command=self._xscroll)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        self.tree.tag_configure("default", foreground="#08795c")
        self.tree.tag_configure("connected", foreground="#93640c")
        self.tree.tag_configure("off", foreground="#8a93a1")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.update_details())
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<ButtonPress-1>", self.begin_column_resize)
        self.tree.bind("<ButtonRelease-1>", self.end_column_resize)
        self.tree.bind("<Motion>", self.column_cursor)
        self.tree.bind("<Leave>", lambda _: self.tree.configure(cursor=""))
        self.tree.bind("<Button-3>", self.popup_menu)
        self.tree.bind("<Return>", lambda _: self.edit_selected())
        self.tree.bind("<MouseWheel>", lambda _: self.cancel_editor(), add=True)
        self.empty_label = ttk.Label(table, text="正在读取网络接口…", background="white", foreground="#69788c")
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        details = ttk.Frame(self, padding=(20, 10, 20, 4))
        details.pack(fill="x")
        ttk.Label(details, textvariable=self.detail_var, style="Sub.TLabel", wraplength=1140).pack(anchor="w")
        actions = ttk.Frame(self, padding=(20, 7, 20, 7))
        actions.pack(fill="x")
        self.edit_button = ttk.Button(actions, text="修改接口度量", command=self.edit_selected)
        self.edit_button.pack(side="left", padx=(0, 6))
        self.prefer_button = ttk.Button(actions, text="设为首选", command=self.prefer_selected)
        self.prefer_button.pack(side="left", padx=(0, 6))
        self.auto_button = ttk.Button(actions, text="恢复自动跃点", command=self.auto_selected)
        self.auto_button.pack(side="left", padx=(0, 6))
        self.routes_button = ttk.Button(actions, text="路由详情", command=self.show_routes)
        self.routes_button.pack(side="left", padx=(0, 6))
        self.verify_button = ttk.Button(actions, text="立即验证选路", command=self.show_verify)
        self.verify_button.pack(side="left")
        ttk.Button(actions, text="变更记录", command=self.show_history).pack(side="right")
        self.undo_button = ttk.Button(actions, text="撤销上一次", command=self.undo)
        self.undo_button.pack(side="right", padx=(0, 6))
        note = ttk.Frame(self, padding=(20, 0, 20, 8))
        note.pack(fill="x")
        ttk.Label(note, text="有效度量 = 接口度量 + 路由度量；数值越小优先级越高。默认出口按默认路由比较，具体目标以「立即验证选路」为准。",
                  style="Sub.TLabel", wraplength=1160).pack(anchor="w")
        footer = ttk.Frame(self, padding=(20, 7, 20, 8))
        footer.pack(fill="x", side="bottom")
        self.status_label = ttk.Label(footer, textvariable=self.message, style="Sub.TLabel", wraplength=780)
        self.status_label.pack(side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.sample_status, style="Sub.TLabel").pack(side="right")
        self.update_actions()

    def visible_columns(self):
        return COLUMNS if self.show_route_metric.get() else tuple(c for c in COLUMNS if c != "route_metric")

    def column_menu(self, parent, selected=None):
        menu = tk.Menu(parent, tearoff=False)
        if selected:
            menu.add_command(label=f"适配「{HEADERS[selected]}」", command=lambda: self.fit_column(selected))
            menu.add_separator()
        menu.add_command(label="按内容适配列宽", command=self.fit_all_columns)
        menu.add_command(label="适配窗口宽度", command=self.fit_columns_to_window)
        menu.add_command(label="列宽设置…", command=self.show_column_settings)
        menu.add_separator()
        menu.add_command(label="恢复默认列宽", command=self.reset_columns)
        return menu

    def column_cursor(self, event):
        separator = self.tree.identify_region(event.x, event.y) == "separator"
        self.tree.configure(cursor="sb_h_double_arrow" if separator or self.resizing_column else "")

    def begin_column_resize(self, event):
        self.cancel_editor()
        self.resizing_column = self.tree.identify_region(event.x, event.y) == "separator"

    def end_column_resize(self, _):
        if self.resizing_column:
            self.resizing_column = False
            # 在 Treeview 类绑定完成原生拖拽之后保存，不在轮询刷新时重置列宽。
            self.schedule_column_save()

    def schedule_column_save(self):
        if self.column_save_id:
            self.after_cancel(self.column_save_id)
        self.column_save_id = self.after(250, self.save_column_layout)

    def save_column_layout(self):
        if self.column_save_id:
            self.after_cancel(self.column_save_id)
            self.column_save_id = None
        if not self.remember_layout:
            return
        try:
            self.preferences.save({c: int(self.tree.column(c, "width")) for c in COLUMNS},
                                  bool(self.show_route_metric.get()))
        except OSError as exc:
            logging.getLogger(__name__).warning("列宽布局保存失败：%s", exc)
            self.message.set(f"列宽已调整，但无法保存布局：{exc}")

    def content_width(self, column):
        style = ttk.Style(self)
        body = tkfont.Font(self, font=style.lookup("Treeview", "font") or "TkDefaultFont")
        heading = tkfont.Font(self, font=style.lookup("Treeview.Heading", "font") or "TkHeadingFont")
        required = heading.measure(HEADERS[column] + " ↑") + 24
        for iid in self.tree.get_children():
            required = max(required, body.measure(str(self.tree.set(iid, column))) + 26)
        return max(COLUMN_MINIMUMS[column], min(required, 600 if column == "description" else 420))

    def set_column_widths(self, widths):
        self.cancel_editor()
        for column, width in widths.items():
            self.tree.column(column, width=max(COLUMN_MINIMUMS[column], min(1200, int(width))))
        self.schedule_column_save()

    def fit_column(self, column):
        self.set_column_widths({column: self.content_width(column)})

    def fit_all_columns(self):
        self.set_column_widths({c: self.content_width(c) for c in self.visible_columns()})
        self.message.set("已按当前可见内容适配列宽；双击表头分隔线可适配单列。")

    def fit_columns_to_window(self):
        self.update_idletasks()
        columns = self.visible_columns()
        available = max(1, self.tree.winfo_width() - 4)
        desired = {c: self.content_width(c) for c in columns}
        minimum = {c: COLUMN_MINIMUMS[c] for c in columns}
        # 先分配各列最小宽度，再按内容需求分配剩余空间。
        spare = max(0, available - sum(minimum.values()))
        weights = {c: max(1, desired[c] - minimum[c]) for c in columns}
        total = sum(weights.values())
        widths = {c: minimum[c] + spare * weights[c] // total for c in columns}
        if available >= sum(minimum.values()):
            widths[columns[-1]] += available - sum(widths.values())
        self.set_column_widths(widths)
        self.tree.xview_moveto(0)
        self.message.set("已适配窗口宽度。窗口过窄时保留最小可读宽度，可横向滚动。")

    def reset_columns(self):
        self.set_column_widths(COLUMN_WIDTHS)
        self.message.set("已恢复默认列宽。")

    def show_column_settings(self):
        if self.column_dialog and self.column_dialog.winfo_exists():
            self.column_dialog.lift()
            return
        win = tk.Toplevel(self)
        self.column_dialog = win
        win.title("列宽设置")
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="输入各列宽度（像素），最大 1200。列宽会自动保存。").grid(row=0, column=0, columnspan=2, pady=(0, 10))
        values = {}
        for row, column in enumerate(COLUMNS, 1):
            ttk.Label(frame, text=HEADERS[column]).grid(row=row, column=0, sticky="w", padx=(0, 24), pady=3)
            values[column] = tk.StringVar(value=str(self.tree.column(column, "width")))
            ttk.Spinbox(frame, from_=COLUMN_MINIMUMS[column], to=1200, textvariable=values[column],
                        width=10).grid(row=row, column=1, sticky="e")

        def use_content():
            for column in COLUMNS:
                values[column].set(str(self.content_width(column)))

        def apply():
            widths = {}
            for column, value in values.items():
                try:
                    width = int(value.get())
                    if not COLUMN_MINIMUMS[column] <= width <= 1200:
                        raise ValueError
                except ValueError:
                    messagebox.showwarning("列宽无效", f"{HEADERS[column]}：请输入 {COLUMN_MINIMUMS[column]}–1200 的整数。", parent=win)
                    return
                widths[column] = width
            self.set_column_widths(widths)
            win.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(COLUMNS) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="按内容填写", command=use_content).pack(side="left")
        ttk.Button(buttons, text="应用", command=apply).pack(side="right", padx=(10, 0))
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="right")
        win.bind("<Return>", lambda _: apply())
        win.bind("<Escape>", lambda _: win.destroy())

    def _build_menu(self):
        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="修改接口度量…", command=self.edit_selected)
        self.menu.add_command(label="设为首选（接口度量 10）…", command=self.prefer_selected)
        for delta, label in ((-10, "提高优先级（度量 −10）"), (-5, "提高优先级（度量 −5）"),
                             (5, "降低优先级（度量 +5）"), (10, "降低优先级（度量 +10）")):
            self.menu.add_command(label=label, command=lambda d=delta: self.adjust_selected(d))
        self.menu.add_command(label="恢复自动跃点…", command=self.auto_selected)
        self.menu.add_separator()
        self.menu.add_command(label="复制行信息", command=self.copy_selected)
        self.menu.add_command(label="查看接口路由", command=self.show_routes)

    def _xscroll(self, *args):
        self.cancel_editor()
        self.tree.xview(*args)

    def _yscroll(self, *args):
        self.cancel_editor()
        self.tree.yview(*args)

    def selected_iface(self) -> Iface | None:
        selected = self.tree.selection()
        return self.last.find(int(selected[0])) if self.last and selected else None

    def update_actions(self):
        iface = self.selected_iface()
        editable = bool(self.admin and iface and iface.connected and not self.busy)
        for button in (self.edit_button, self.auto_button):
            button.configure(state="normal" if editable else "disabled")
        self.prefer_button.configure(state="normal" if editable and iface.route_metric is not None else "disabled")
        self.routes_button.configure(state="normal" if iface else "disabled")
        self.verify_button.configure(state="normal" if self.last and not self.busy else "disabled")
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        can_undo = self.admin and not self.busy and any(
            h.success and not h.undone and h.title != "撤销上一次" for h in self.history)
        self.undo_button.configure(state="normal" if can_undo else "disabled")

    def set_busy(self, busy: bool):
        self.busy = busy
        if busy:
            self.cancel_editor()
            self.message.set("正在执行并回读验证，请稍候…")
        self.update_actions()

    def drain(self):
        try:
            while True:
                event = self.controller.events.get_nowait()
                if event.kind == "snapshot":
                    self.last = event.payload
                    self.render()
                    stamp = datetime.fromtimestamp(self.last.ts).strftime("%H:%M:%S")
                    self.sample_status.set(f"{stamp}  ·  {self.last.elapsed_ms:.0f} ms  ·  {'已暂停' if self.paused else '1 s 轮询'}")
                    if not self.busy:
                        self.message.set("部分数据不可用，正在重试：" + "；".join(self.last.warnings)
                                         if self.last.warnings else "就绪 · 双击接口度量可编辑" if self.admin else "就绪 · 只读监控，修改设置请以管理员重启")
                elif event.kind == "error":
                    self.message.set(str(event.payload))
                    self.sample_status.set("采样异常 · 数据可能过期")
                    if not self.last:
                        self.empty_label.configure(text="暂时无法读取接口，请查看下方错误信息。")
                elif event.kind == "busy":
                    self.set_busy(event.payload)
                elif event.kind == "history":
                    self.history = event.payload
                    self.render_history()
                    self.update_actions()
                elif event.kind == "result":
                    success, message = event.payload
                    self.message.set(message.splitlines()[0])
                    if success:
                        messagebox.showinfo("修改完成", message, parent=self)
                    else:
                        messagebox.showerror("操作未完成", message, parent=self)
                elif event.kind == "route":
                    destination, route, snapshot = event.payload
                    iface = snapshot.find(route.ifindex)
                    self.last = snapshot
                    self.render()
                    self.route_result.set(
                        f"目标：{destination}\n接口：{iface.alias if iface else '未知接口'}（索引 {route.ifindex}）\n"
                        f"匹配路由：{route.prefix}\n下一跳：{route.nexthop if route.nexthop != '0.0.0.0' else '在链路上'}\n"
                        f"接口度量：{number(iface.if_metric if iface else None)}    "
                        f"路由度量：{number(route.route_metric(iface.if_metric) if iface else None)}    "
                        f"有效度量：{number(None if route.effective == 0xFFFFFFFF else route.effective)}\n\n"
                        "此结果由 Windows 选路 API 返回，不发送探测流量。\n已有连接、应用绑定、系统代理和 IPv6 流量可能使用不同出口。")
                    self.message.set(f"已验证到 {destination} 的 IPv4 系统选路。")
        except queue.Empty:
            pass
        self.drain_id = self.after(100, self.drain)

    def render(self):
        if not self.last:
            return
        ifaces = self.last.ifaces
        connected = sum(i.connected for i in ifaces)
        for var, count in zip(self.summary_vars, (len(ifaces), connected, len(ifaces) - connected,
                                                  sum(r.is_default for r in self.last.routes))):
            var.set(str(count))
        exits = self.last.default_exits
        if len(exits) > 1:
            self.exit_var.set("默认出口：等价候选 " + " / ".join(i.alias for i in exits) + f"（{exits[0].effective}）")
        elif exits:
            self.exit_var.set(f"默认出口：{exits[0].alias}（有效度量 {exits[0].effective}）")
        else:
            self.exit_var.set("默认出口：无已连接的 IPv4 默认路由")
        if self.editor or self.resizing_column:
            return
        filtered = []
        mode = self.filter_var.get()
        search = self.search_var.get().strip().casefold()
        for iface in ifaces:
            if mode == "已连接" and not iface.connected:
                continue
            if mode == "未连接" and iface.connected:
                continue
            if mode == "有默认路由" and iface.effective is None:
                continue
            haystack = " ".join((iface.alias, iface.description, str(iface.index), *iface.ipv4, iface.gateway or "")).casefold()
            if search and search not in haystack:
                continue
            filtered.append(iface)
        col = self.sort_column

        def key(iface):
            value = {"dot": 2 if iface.connected and iface.effective is not None else int(iface.connected),
                     "alias": iface.alias.casefold(), "description": iface.description.casefold(),
                     "state": iface.connected, "metric": iface.if_metric, "auto": iface.auto_metric,
                     "gateway": int(ipaddress.IPv4Address(iface.gateway)) if iface.gateway else None,
                     "route_metric": iface.route_metric, "effective": iface.effective,
                     "routes": iface.route_count}[col]
            return (value, iface.index)

        # 空值始终位于末尾，数字按实际数值排序。
        missing = lambda i: col in ("route_metric", "effective", "gateway") and getattr(
            i, {"gateway": "gateway"}.get(col, col)) is None
        ordered = sorted((i for i in filtered if not missing(i)), key=key, reverse=self.sort_reverse)
        ordered += sorted((i for i in filtered if missing(i)), key=lambda i: i.index)
        visible_ids = {str(i.index) for i in ordered}
        for iid in self.tree.get_children():
            if iid not in visible_ids:
                self.tree.delete(iid)
        for pos, iface in enumerate(ordered):
            tag = "default" if iface.connected and iface.effective is not None else "connected" if iface.connected else "off"
            values = ("●" if iface.connected else "○", iface.alias, iface.description,
                      "已连接" if iface.connected else "未连接", iface.if_metric,
                      "是" if iface.auto_metric else "否", iface.gateway or "—", number(iface.route_metric),
                      number(iface.effective), iface.route_count)
            iid = str(iface.index)
            if self.tree.exists(iid):
                if tuple(str(v) for v in values) != self.tree.item(iid, "values"):
                    self.tree.item(iid, values=values, tags=(tag,))
                else:
                    self.tree.item(iid, tags=(tag,))
                self.tree.move(iid, "", pos)
            else:
                self.tree.insert("", pos, iid=iid, values=values, tags=(tag,))
        if ordered:
            self.empty_label.place_forget()
        else:
            self.empty_label.configure(text="没有符合筛选条件的接口。" if ifaces else "未发现 IPv4 接口。")
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        self.update_details()
        self.render_routes()

    def update_details(self):
        iface = self.selected_iface()
        if iface:
            self.detail_var.set(f"索引 {iface.index}  ·  IPv4：{', '.join(iface.ipv4) or '无地址'}  ·  "
                                f"DHCP：{'启用' if iface.dhcp else '禁用'}  ·  接口度量 {iface.if_metric} + "
                                f"默认路由度量 {number(iface.route_metric)} = 有效度量 {number(iface.effective)}")
        else:
            self.detail_var.set("选择一个接口查看详情。双击接口度量编辑；双击其它单元格查看全部路由。")
        self.update_actions()

    def sort_by(self, column):
        self.cancel_editor()
        self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
        self.sort_column = column
        for col in COLUMNS:
            self.tree.heading(col, text=HEADERS[col] + (" ↓" if self.sort_reverse else " ↑") if col == column else HEADERS[col])
        self.render()

    def toggle_route_metric(self):
        self.cancel_editor()
        self.tree.configure(displaycolumns=self.visible_columns())
        self.schedule_column_save()

    def on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "separator":
            column = self.tree.identify_column(event.x)
            self.fit_column(self.tree.column(column, "id"))
            return "break"
        if region != "cell":
            return
        row = self.tree.identify_row(event.y)
        self.tree.selection_set(row)
        col = self.tree.identify_column(event.x)
        if self.tree.column(col, "id") == "metric":
            self.edit_selected()
        else:
            self.show_routes()

    def edit_selected(self):
        iface = self.selected_iface()
        if not self.can_edit(iface):
            return
        self.cancel_editor()
        self.tree.see(str(iface.index))
        bbox = self.tree.bbox(str(iface.index), "metric")
        if not bbox:
            # 横向滚动后可能看不到度量列，移回起点再定位。
            self.tree.xview_moveto(0)
            self.update_idletasks()
            bbox = self.tree.bbox(str(iface.index), "metric")
        if not bbox:
            return
        x, y, width, height = bbox
        value = tk.StringVar(value=str(iface.if_metric))
        editor = ttk.Spinbox(self.tree, from_=1, to=9999, increment=1, textvariable=value, justify="center")
        self.editor = editor
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.selection_range(0, "end")

        def commit(_=None):
            try:
                metric = validate_metric(value.get())
            except ValueError as exc:
                editor.configure(style="Invalid.TSpinbox")
                self.message.set(str(exc))
                self.bell()
                return "break"
            self.cancel_editor()
            self.request_metric(iface, metric)
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<Escape>", lambda _: self.cancel_editor())
        editor.bind("<FocusOut>", lambda _: self.after_idle(self.cancel_editor))
        editor.bind("<KeyRelease>", lambda _: editor.configure(style="TSpinbox"))

    def cancel_editor(self):
        if self.editor:
            editor, self.editor = self.editor, None
            editor.destroy()

    def can_edit(self, iface):
        return bool(iface and iface.connected and self.admin and not self.busy)

    def request_metric(self, iface, metric):
        try:
            self.confirm_plan(metric_plan(iface, metric))
        except ValueError as exc:
            self.message.set(str(exc))

    def confirm_plan(self, plan: ChangePlan):
        if self.busy or not self.admin:
            return
        details = "\n".join(c.label for c in plan.changes)
        prompt = f"{details}\n\n设置将持久保存。手动设置度量会关闭自动跃点。\n仅影响新建连接；必要时重开浏览器或代理。"
        if len(plan.changes) > 1:
            prompt += "\n此操作会同时修改以上接口，任一步失败将尝试恢复。"
        if messagebox.askokcancel(plan.title, prompt, parent=self):
            self.set_busy(True)
            self.controller.apply(plan)

    def auto_selected(self):
        iface = self.selected_iface()
        if self.can_edit(iface):
            self.request_metric(iface, None)

    def adjust_selected(self, delta):
        iface = self.selected_iface()
        if self.can_edit(iface):
            self.request_metric(iface, max(1, min(9999, iface.if_metric + delta)))

    def prefer_selected(self):
        iface = self.selected_iface()
        if not self.can_edit(iface):
            return
        try:
            self.confirm_plan(preferred_plan(self.last, iface))
        except ValueError as exc:
            messagebox.showinfo("无法设为首选", str(exc), parent=self)

    def popup_menu(self, event):
        if self.tree.identify_region(event.x, event.y) in ("heading", "separator"):
            column = self.tree.identify_column(event.x)
            if column and column != "#0":
                previous = getattr(self, "column_context_menu", None)
                if previous:
                    previous.destroy()
                menu = self.column_menu(self, self.tree.column(column, "id"))
                self.column_context_menu = menu
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.cancel_editor()
        self.tree.selection_set(row)
        iface = self.selected_iface()
        for i in range(7):
            editable = self.can_edit(iface) and (i != 1 or iface.route_metric is not None)
            self.menu.entryconfigure(i, state="normal" if editable else "disabled")
        self.menu.tk_popup(event.x_root, event.y_root)

    def copy_selected(self):
        iface = self.selected_iface()
        if not iface:
            return
        self.clipboard_clear()
        self.clipboard_append(
            f"名称\t{iface.alias}\n描述\t{iface.description}\n索引\t{iface.index}\n"
            f"状态\t{'已连接' if iface.connected else '未连接'}\nIPv4\t{', '.join(iface.ipv4)}\n"
            f"接口度量\t{iface.if_metric}\n自动跃点\t{'是' if iface.auto_metric else '否'}\n"
            f"默认网关\t{iface.gateway or '—'}\n路由度量\t{number(iface.route_metric)}\n"
            f"有效度量\t{number(iface.effective)}\n路由数\t{iface.route_count}")
        self.message.set("已复制接口信息。")

    def refresh(self):
        if not self.busy:
            self.cancel_editor()
            self.message.set("正在刷新…")
            self.controller.refresh()

    def toggle_pause(self):
        self.cancel_editor()
        self.paused = not self.paused
        self.controller.set_paused(self.paused)
        self.pause_button.configure(text="继续监控" if self.paused else "暂停")
        self.message.set("已暂停自动采样；筛选、手动刷新和修改仍可使用。" if self.paused else "已恢复每秒采样。")
        self.sample_status.set(self.sample_status.get().replace("1 s 轮询", "已暂停") if self.paused else
                               self.sample_status.get().replace("已暂停", "1 s 轮询"))

    def show_routes(self):
        iface = self.selected_iface()
        if not iface:
            return
        if self.route_window and self.route_window.winfo_exists():
            self.route_window.destroy()
        win = tk.Toplevel(self)
        self.route_window = win
        self.route_index = iface.index
        self.route_identity = iface
        win.title(f"路由详情 · {iface.alias}")
        win.geometry("850x450")
        win.minsize(650, 300)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        self.route_caption = ttk.Label(frame, text="", wraplength=790)
        self.route_caption.pack(fill="x", pady=(0, 10))
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        cols = ("prefix", "gateway", "route", "interface", "effective")
        self.route_tree = ttk.Treeview(table, columns=cols, show="headings")
        for col, title, width in zip(cols, ("目标网络", "下一跳", "路由度量", "接口度量", "有效度量"), (190, 165, 110, 110, 110)):
            self.route_tree.heading(col, text=title)
            self.route_tree.column(col, width=width, minwidth=90, anchor="w" if col in ("prefix", "gateway") else "center")
        self.route_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.route_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.route_tree.configure(yscrollcommand=scroll.set)
        self.route_tree.tag_configure("default", foreground="#08795c")
        ttk.Label(frame, text="绿色为默认路由。接口或路由正在变化时，无法一致推导的度量显示为 —。", style="Sub.TLabel").pack(anchor="w", pady=(10, 0))
        self.render_routes()

    def render_routes(self):
        if not self.route_window or not self.route_window.winfo_exists() or not self.last:
            return
        iface = self.last.find(self.route_index)
        if not iface or not self.route_identity.same_interface(iface):
            self.route_caption.configure(text="该接口已消失或被替换，请关闭详情后重新选择。")
            self.route_tree.delete(*self.route_tree.get_children())
            return
        self.route_caption.configure(text=f"{iface.alias}  ·  {iface.description}  ·  IPv4：{', '.join(iface.ipv4) or '无地址'}  ·  共 {iface.route_count} 条路由")
        rows = sorted((r for r in self.last.routes if r.ifindex == iface.index),
                      key=lambda r: (not r.is_default, int(ipaddress.IPv4Address(r.dest)), r.mask, r.effective))
        yview = self.route_tree.yview()
        self.route_tree.delete(*self.route_tree.get_children())
        for route in rows:
            self.route_tree.insert("", "end", values=(route.prefix, "在链路上" if route.nexthop == "0.0.0.0" else route.nexthop,
                                                       number(route.route_metric(iface.if_metric)), iface.if_metric,
                                                       number(None if route.effective == 0xFFFFFFFF else route.effective)),
                                   tags=("default",) if route.is_default else ())
        if yview:
            self.route_tree.yview_moveto(yview[0])

    def show_verify(self):
        if self.busy:
            return
        if getattr(self, "verify_window", None) and self.verify_window.winfo_exists():
            self.verify_window.lift()
            return
        win = tk.Toplevel(self)
        self.verify_window = win
        win.title("立即验证 IPv4 选路")
        win.geometry("650x390")
        win.minsize(600, 360)
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="目标 IPv4 地址").pack(anchor="w")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(8, 16))
        target = tk.StringVar(value="1.1.1.1")
        entry = ttk.Entry(bar, textvariable=target)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.route_result = tk.StringVar(value="输入目标地址后查询 Windows 当前选择的路由。")

        def query_route():
            if self.busy:
                return
            try:
                destination = str(ipaddress.IPv4Address(target.get().strip()))
            except ValueError:
                self.route_result.set("请输入有效的 IPv4 地址，例如 1.1.1.1。")
                return
            self.set_busy(True)
            self.route_result.set("正在查询…")
            self.controller.verify_route(destination)

        ttk.Button(bar, text="查询选路", command=query_route).pack(side="right")
        entry.bind("<Return>", lambda _: query_route())
        ttk.Label(frame, textvariable=self.route_result, wraplength=600, justify="left").pack(anchor="w")
        entry.focus_set()

    def show_history(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.lift()
            return
        win = tk.Toplevel(self)
        self.history_window = win
        win.title("变更记录 · 最近 50 条（本次会话）")
        win.geometry("900x430")
        win.minsize(700, 320)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        columns = ("time", "action", "change", "result")
        self.history_tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for col, title, width in zip(columns, ("时间", "操作", "接口及旧值 → 新值", "结果"), (90, 115, 440, 100)):
            self.history_tree.heading(col, text=title)
            self.history_tree.column(col, width=width, minwidth=80, anchor="w")
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<<TreeviewSelect>>", self.history_selected)
        self.history_message = tk.StringVar(value="选择记录查看完整结果；撤销会核对当前值，防止覆盖外部修改。")
        ttk.Label(frame, textvariable=self.history_message, wraplength=840).pack(anchor="w", pady=10)
        ttk.Button(frame, text="撤销上一次成功修改", command=self.undo).pack(anchor="e")
        self.render_history()

    def render_history(self):
        if not self.history_window or not self.history_window.winfo_exists():
            return
        self.history_tree.delete(*self.history_tree.get_children())
        for index in reversed(range(len(self.history))):
            record = self.history[index]
            self.history_tree.insert("", "end", iid=str(index), values=(
                datetime.fromtimestamp(record.ts).strftime("%H:%M:%S"), record.title,
                "；".join(c.label for c in record.changes), "已撤销" if record.undone else "成功" if record.success else "失败"))
        if not self.history:
            self.history_message.set("本次会话还没有变更记录。")

    def history_selected(self, _):
        selected = self.history_tree.selection()
        if selected:
            self.history_message.set(self.history[int(selected[0])].message)

    def undo(self):
        if not self.admin or self.busy:
            return
        previous = next((h for h in reversed(self.history) if h.success and not h.undone and h.title != "撤销上一次"), None)
        if previous is None:
            return
        details = "\n".join(f"{c.iface.alias}：{c.after.label} → {c.before.label}" for c in reversed(previous.changes))
        if messagebox.askokcancel("撤销上一次", details + "\n\n将恢复修改前的手动度量或自动跃点模式。", parent=self):
            self.set_busy(True)
            self.controller.undo()

    def elevate(self):
        if self.busy:
            return
        try:
            relaunch_as_admin(os.getpid())
        except OSError as exc:
            self.message.set(str(exc))
            return
        self.close()

    def close(self):
        if self.busy:
            messagebox.showinfo("操作进行中", "正在执行并验证操作，请完成后再退出。", parent=self)
            return
        self.controller.stop()
        self.destroy()

    def destroy(self):
        if hasattr(self, "tree"):
            self.save_column_layout()
        if getattr(self, "drain_id", None):
            self.after_cancel(self.drain_id)
            self.drain_id = None
        if hasattr(self, "controller"):
            self.controller.stop()
        super().destroy()

    def report_callback_exception(self, exc_type, value, traceback):
        logging.getLogger(__name__).error("GUI 回调异常", exc_info=(exc_type, value, traceback))
        messagebox.showerror("界面操作失败", str(value), parent=self)
