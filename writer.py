"""网络设置写入：不拼接用户输入的 shell 命令；每次修改均回读验证。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import time

from elevate import is_admin
from models import Iface, MetricSetting, validate_metric

log = logging.getLogger(__name__)


class WriteError(RuntimeError):
    pass


class MetricWriter:
    def __init__(self, reader, admin_check=is_admin, runner=subprocess.run):
        self.reader = reader
        self.admin_check = admin_check
        self.runner = runner

    def current(self, expected: Iface) -> Iface:
        ifaces, _ = self.reader.read_ifaces(fresh_metadata=True, include_addresses=False)
        iface = next((i for i in ifaces if i.index == expected.index), None)
        if not iface or not expected.same_interface(iface):
            raise WriteError("接口已消失、改名或被替换，请刷新后重试。")
        if not iface.connected:
            raise WriteError(f"{iface.alias} 已断开连接，未继续修改。")
        return iface

    def _run(self, command: list[str]) -> str:
        try:
            result = self.runner(command, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", creationflags=0x08000000, timeout=20, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise WriteError("写入命令超时；将回读状态并尝试恢复原设置。") from exc
        except OSError as exc:
            raise WriteError(f"无法执行系统命令：{exc}") from exc
        if result.returncode:
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            raise WriteError(f"命令返回码 {result.returncode}：{output or '系统拒绝写入'}")
        return (result.stdout or "").strip()

    def _powershell(self, body: str) -> str:
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        script = ("$ErrorActionPreference='Stop'; "
                  "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; "
                  "try { " + body + "; exit 0 } catch { "
                  "[Console]::Error.WriteLine($_.Exception.Message); exit 1 }")
        return self._run([str(system32 / "WindowsPowerShell/v1.0/powershell.exe"),
                          "-NoProfile", "-NonInteractive", "-Command", script])

    def read_stores(self, iface: Iface) -> dict:
        """独立读取活动和持久配置，防止命令成功但仅修改了下次启动配置。"""
        index = int(iface.index)
        body = (
            f"$a = @(Get-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 "
            "-PolicyStore ActiveStore); "
            f"$p = @(Get-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 "
            "-PolicyStore PersistentStore); "
            "if ($a.Count -ne 1 -or $p.Count -ne 1) { throw 'Interface store lookup is not unique' }; "
            "[pscustomobject]@{ "
            "active = @{metric=$a[0].InterfaceMetric; automatic=$a[0].AutomaticMetric}; "
            "persistent = @{metric=$p[0].InterfaceMetric; automatic=$p[0].AutomaticMetric} "
            "} | ConvertTo-Json -Compress -Depth 4"
        )
        output = self._powershell(body)
        try:
            stores = json.loads(output.lstrip("\ufeff"))
            for name in ("active", "persistent"):
                state = stores[name]
                if state["automatic"] not in (0, 1) or (
                        state["metric"] is not None and type(state["metric"]) is not int):
                    raise ValueError("invalid metric state")
            return stores
        except (ValueError, KeyError, TypeError) as exc:
            raise WriteError(f"无法解析接口 {iface.alias} 的活动/持久配置回读结果。") from exc

    def apply(self, iface: Iface, setting: MetricSetting):
        if not self.admin_check():
            raise WriteError("修改接口度量需要管理员权限。")
        self.current(iface)
        # 索引仅用于当前会话，调用前以名称和 GUID 再次核对，避免名称转义和注入。
        # 明确更新两个配置存储；旧版只写 persistent，不能保证活动值已改变。
        if setting.automatic:
            parameters = "-AutomaticMetric Enabled"
        else:
            metric = validate_metric(setting.metric)
            parameters = f"-AutomaticMetric Disabled -InterfaceMetric {metric}"
        log.info("开始写入接口 %s (index=%s)，目标=%s", iface.alias, iface.index, setting.label)
        try:
            command = (f"Set-NetIPInterface -InterfaceIndex {int(iface.index)} "
                       f"-AddressFamily IPv4 {parameters} -Confirm:$false")
            self._powershell(f"{command} -PolicyStore PersistentStore; {command} -PolicyStore ActiveStore")
            self.verify(iface, setting)
        except WriteError as exc:
            log.error("接口 %s (index=%s) 写入/校验失败，目标=%s：%s",
                      iface.alias, iface.index, setting.label, exc)
            raise WriteError(f"{iface.alias}（索引 {iface.index}）：{exc}") from exc
        log.info("接口 %s (index=%s) 活动和持久配置均已校验：%s", iface.alias, iface.index, setting.label)

    def verify(self, iface: Iface, setting: MetricSetting, timeout: float = 4.0):
        deadline = time.monotonic() + timeout
        time.sleep(1.0)
        last = ""
        while True:
            current = self.current(iface)
            if setting.matches(current):
                stores = self.read_stores(iface)
                mismatches = []
                for name, label in (("active", "活动配置"), ("persistent", "持久配置")):
                    state = stores[name]
                    automatic = state["automatic"] == 1
                    if automatic != setting.automatic or (not setting.automatic and state["metric"] != setting.metric):
                        mismatches.append(f"{label}为 {'自动' if automatic else '手动'}（{state['metric']}）")
                if not mismatches:
                    return
                last = "；".join(mismatches)
            else:
                last = f"活动配置为 {MetricSetting.of(current).label}"
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        raise WriteError(f"回读校验失败：期望 {setting.label}，{last}。")
