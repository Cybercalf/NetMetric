"""只读 Windows 网络数据层：线程独享 WMI 连接与 IP Helper API。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import ipaddress
import os
import socket
import time

from models import Iface, Route, Snapshot, assemble_snapshot


class MIB_IPFORWARDROW(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "dwForwardDest", "dwForwardMask", "dwForwardPolicy", "dwForwardNextHop",
        "dwForwardIfIndex", "dwForwardType", "dwForwardProto", "dwForwardAge",
        "dwForwardNextHopAS", "dwForwardMetric1", "dwForwardMetric2",
        "dwForwardMetric3", "dwForwardMetric4", "dwForwardMetric5",
    )]


def _ip(value: int) -> str:
    return socket.inet_ntoa(value.to_bytes(4, "little"))


class IpHelper:
    def __init__(self):
        if os.name != "nt":
            raise OSError("NetMetric 仅支持 Windows 10/11。")
        self.dll = ctypes.WinDLL("iphlpapi.dll")
        self.dll.GetIpForwardTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL]
        self.dll.GetIpForwardTable.restype = wintypes.DWORD
        self.dll.GetBestRoute.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(MIB_IPFORWARDROW)]
        self.dll.GetBestRoute.restype = wintypes.DWORD

    @staticmethod
    def _route(row: MIB_IPFORWARDROW) -> Route:
        return Route(row.dwForwardIfIndex, _ip(row.dwForwardDest), _ip(row.dwForwardMask),
                     _ip(row.dwForwardNextHop), row.dwForwardMetric1)

    def read_routes(self) -> list[Route]:
        size = wintypes.ULONG(0)
        rc = self.dll.GetIpForwardTable(None, ctypes.byref(size), False)
        if rc not in (0, 122):  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(rc)
        # VPN/网卡切换期间表大小可能在两次调用之间增长，重新分配后重试。
        for _ in range(5):
            buffer = ctypes.create_string_buffer(max(size.value, 4))
            rc = self.dll.GetIpForwardTable(buffer, ctypes.byref(size), False)
            if rc == 122:
                continue
            if rc:
                raise ctypes.WinError(rc)
            count = ctypes.c_uint32.from_buffer(buffer).value
            required = 4 + count * ctypes.sizeof(MIB_IPFORWARDROW)
            if required > len(buffer):
                raise OSError("系统返回的路由表长度无效。")
            rows = (MIB_IPFORWARDROW * count).from_buffer(buffer, 4)
            return [self._route(row) for row in rows]
        raise OSError("路由表持续变化，请稍后重试。")

    def best_route(self, destination: str) -> Route:
        address = str(ipaddress.IPv4Address(destination.strip()))
        row = MIB_IPFORWARDROW()
        rc = self.dll.GetBestRoute(int.from_bytes(socket.inet_aton(address), "little"), 0, ctypes.byref(row))
        if rc:
            raise OSError(rc, f"无法查询到 {address} 的路由：{ctypes.FormatError(rc).strip()}")
        return self._route(row)


class SystemReader:
    """只能在创建/进入它的工作线程内使用；不可把 COM 对象传给 GUI。"""

    IFACE_SQL = ("SELECT InterfaceIndex,InterfaceAlias,InterfaceMetric,ConnectionState,"
                 "AutomaticMetric,Dhcp FROM MSFT_NetIPInterface WHERE AddressFamily=2")

    def __init__(self):
        self.wmi = None
        self.pythoncom = None
        self.ip = None
        self._adapter_cache: dict[int, dict] = {}
        self._adapter_signature = None
        self._adapter_refreshed = float("-inf")

    def __enter__(self):
        import pythoncom
        self.pythoncom = pythoncom
        pythoncom.CoInitialize()
        try:
            self.ip = IpHelper()
            self._connect()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        self.wmi = None
        if self.pythoncom:
            self.pythoncom.CoUninitialize()
            self.pythoncom = None

    def _connect(self):
        from win32com.client import dynamic
        self.wmi = dynamic.Dispatch("WbemScripting.SWbemLocator").ConnectServer(".", r"root\StandardCimv2")
        self.wmi.Security_.ImpersonationLevel = 3

    def _query(self, sql: str, fields: tuple[str, ...]) -> list[dict]:
        if self.wmi is None:
            self._connect()
        try:
            rows = self.wmi.ExecQuery(sql, "WQL", 0x30)  # forward-only, return-immediately
            # 直接读动态属性，避免每个字段多次访问 Properties_/Item 的 COM 往返。
            return [{name: getattr(row, name) for name in fields} for row in rows]
        except Exception:
            self.wmi = None  # 服务重启/暂时错误后允许下一轮重新连接。
            raise

    def read_ifaces(self, *, fresh_metadata: bool = False, include_addresses: bool = True) -> tuple[list[Iface], tuple[str, ...]]:
        rows = self._query(self.IFACE_SQL, ("InterfaceIndex", "InterfaceAlias", "InterfaceMetric",
                                          "ConnectionState", "AutomaticMetric", "Dhcp"))
        warnings = []
        adapters = self._adapter_cache
        addresses: dict[int, list[str]] = {}
        signature = tuple(sorted((int(r["InterfaceIndex"]), str(r["InterfaceAlias"])) for r in rows))
        # 网卡描述/GUID 查询比度量查询昂贵。监控时缓存 5 秒，增删/改名立即失效；
        # 写入前始终 fresh_metadata=True，不能用缓存身份授权修改。
        if fresh_metadata or signature != self._adapter_signature or time.monotonic() - self._adapter_refreshed >= 5:
            try:
                adapter_rows = self._query(
                    "SELECT InterfaceIndex,InterfaceDescription,InterfaceGuid FROM MSFT_NetAdapter",
                    ("InterfaceIndex", "InterfaceDescription", "InterfaceGuid"))
                adapters = {int(r["InterfaceIndex"]): r for r in adapter_rows}
                self._adapter_cache = adapters
                self._adapter_signature = signature
                self._adapter_refreshed = time.monotonic()
            except Exception as exc:
                adapters = {}
                warnings.append(f"网卡描述暂不可用：{exc}")
        if include_addresses:
            try:
                for row in self._query(
                    "SELECT InterfaceIndex,IPAddress FROM MSFT_NetIPAddress WHERE AddressFamily=2",
                    ("InterfaceIndex", "IPAddress")):
                    addresses.setdefault(int(row["InterfaceIndex"]), []).append(str(row["IPAddress"]))
            except Exception as exc:
                warnings.append(f"IPv4 地址暂不可用：{exc}")
        ifaces = []
        for row in rows:
            index = int(row["InterfaceIndex"])
            adapter = adapters.get(index, {})
            ifaces.append(Iface(
                index=index, alias=str(row["InterfaceAlias"]),
                description=str(adapter.get("InterfaceDescription") or "Windows 虚拟/系统接口"),
                connected=int(row["ConnectionState"]) == 1,
                if_metric=int(row["InterfaceMetric"]),
                auto_metric=int(row["AutomaticMetric"]) == 1,
                dhcp=int(row["Dhcp"]) == 1,
                guid=str(adapter.get("InterfaceGuid") or ""),
                ipv4=tuple(sorted(addresses.get(index, []))),
            ))
        return ifaces, tuple(warnings)

    def collect(self) -> Snapshot:
        started = time.perf_counter()
        ifaces, warnings = self.read_ifaces()
        routes = self.ip.read_routes()
        return assemble_snapshot(ifaces, routes, warnings, (time.perf_counter() - started) * 1000)

    def best_route(self, destination: str) -> Route:
        return self.ip.best_route(destination)
