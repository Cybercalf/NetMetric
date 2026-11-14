"""后台串行采样和写入；只有纯 Python 数据通过队列交给 tkinter。"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import queue
import threading
import time

from config import HISTORY_LIMIT, POLL_SECONDS
from models import Change, ChangePlan, HistoryEntry
from netcore import SystemReader
from writer import MetricWriter, WriteError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    kind: str
    payload: object = None


class Controller:
    def __init__(self, reader_factory=SystemReader, writer_factory=MetricWriter,
                 interval: float = POLL_SECONDS):
        self.events: queue.Queue[Event] = queue.Queue()
        self.commands: queue.Queue[tuple[str, object]] = queue.Queue()
        self.reader_factory = reader_factory
        self.writer_factory = writer_factory
        self.interval = interval
        self.history: deque[HistoryEntry] = deque(maxlen=HISTORY_LIMIT)
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.thread = threading.Thread(target=self._run, name="NetMetric-worker", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.commands.put(("stop", None))

    def refresh(self):
        self.commands.put(("refresh", None))

    def set_paused(self, paused: bool):
        self.paused.set() if paused else self.paused.clear()
        self.commands.put(("wake", None))

    def apply(self, plan: ChangePlan):
        self.commands.put(("apply", plan))

    def undo(self):
        self.commands.put(("undo", None))

    def verify_route(self, destination: str):
        self.commands.put(("route", destination))

    def _sample(self, reader):
        try:
            snapshot = reader.collect()
            self.events.put(Event("snapshot", snapshot))
        except Exception as exc:
            log.exception("网络采样失败")
            self.events.put(Event("error", f"采样失败（保留上次数据，将自动重试）：{exc}"))

    def _run(self):
        while not self.stop_event.is_set():
            try:
                with self.reader_factory() as reader:
                    self._session(reader)
            except Exception as exc:
                log.exception("网络数据服务初始化失败")
                self.events.put(Event("error", f"数据服务不可用，正在重试：{exc}"))
                self.stop_event.wait(2.0)

    def _session(self, reader):
        writer = self.writer_factory(reader)
        next_sample = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if not self.paused.is_set() and now >= next_sample:
                next_sample = now + self.interval
                self._sample(reader)
            timeout = 0.25 if self.paused.is_set() else max(0.01, min(0.25, next_sample - time.monotonic()))
            try:
                kind, payload = self.commands.get(timeout=timeout)
            except queue.Empty:
                continue
            if kind == "stop":
                return
            if kind == "refresh":
                self._sample(reader)
                next_sample = time.monotonic() + self.interval
            elif kind in ("apply", "undo", "route"):
                self.events.put(Event("busy", True))
                try:
                    if kind == "apply":
                        self.execute_plan(payload, writer)
                    elif kind == "undo":
                        self._undo(writer)
                    else:
                        route = reader.best_route(payload)
                        snapshot = reader.collect()
                        self.events.put(Event("route", (payload, route, snapshot)))
                except Exception as exc:
                    log.exception("命令执行失败")
                    self.events.put(Event("result", (False, str(exc))))
                finally:
                    if kind in ("apply", "undo"):
                        self._sample(reader)  # 即使暂停，也展示刚刚回读的实际结果。
                    self.events.put(Event("busy", False))
                    next_sample = time.monotonic() + self.interval

    def execute_plan(self, plan: ChangePlan, writer, *, undoing: HistoryEntry | None = None) -> HistoryEntry:
        record = HistoryEntry(plan.title, plan.changes)
        attempted: list[Change] = []
        try:
            if not plan.changes:
                raise WriteError("修改计划为空。")
            # 先核对全部旧值，避免确认框打开期间外部程序更改设置。
            for change in plan.changes:
                if not change.before.matches(writer.current(change.iface)):
                    raise WriteError(f"{change.iface.alias} 的设置已被其它程序修改，请刷新后重试。")
            for change in plan.changes:
                if not change.before.matches(writer.current(change.iface)):
                    raise WriteError(f"{change.iface.alias} 的设置发生变化，已停止后续修改。")
                # 非零退出码/超时也可能已经部分生效，因此先记录再执行。
                attempted.append(change)
                writer.apply(change.iface, change.after)
            record.success = True
            record.message = "写入成功，已回读校验。仅影响新建连接；必要时重开浏览器或代理。"
            if undoing:
                undoing.undone = True
        except Exception as exc:
            log.warning("修改失败，尝试恢复：%s", exc)
            rollback_errors = []
            for change in reversed(attempted):
                try:
                    # 活动值未变也可能已写入持久配置，不能仅比较活动值后跳过恢复。
                    writer.apply(change.iface, change.before)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{change.iface.alias}：{rollback_exc}")
            record.message = str(exc)
            log.warning("操作 %s 失败，涉及接口：%s", plan.title, "；".join(c.label for c in plan.changes))
            if rollback_errors:
                record.message += "\n部分接口未能恢复，请检查当前系统状态：\n" + "\n".join(rollback_errors)
            elif attempted:
                record.message += "\n已回读确认恢复原设置。"
            else:
                record.message += "\n未执行写入。"
        self.history.append(record)
        self.events.put(Event("history", tuple(self.history)))
        self.events.put(Event("result", (record.success, record.message)))
        return record

    def _undo(self, writer):
        previous = next((h for h in reversed(self.history)
                         if h.success and not h.undone and h.title != "撤销上一次"), None)
        if previous is None:
            raise WriteError("没有可以撤销的成功修改。")
        reverse = tuple(Change(c.iface, c.after, c.before) for c in reversed(previous.changes))
        self.execute_plan(ChangePlan("撤销上一次", reverse), writer, undoing=previous)
