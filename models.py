"""与 Windows API 和界面无关的数据结构、路由汇总及修改计划。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import ipaddress
import time

from config import METRIC_MIN, METRIC_MAX


@dataclass(frozen=True)
class Route:
    ifindex: int
    dest: str
    mask: str
    nexthop: str
    effective: int

    @property
    def is_default(self) -> bool:
        return self.dest == "0.0.0.0" and self.mask == "0.0.0.0"

    @property
    def prefix(self) -> str:
        try:
            return str(ipaddress.IPv4Network(f"{self.dest}/{self.mask}", strict=False))
        except ValueError:
            return f"{self.dest}/{self.mask}"

    def route_metric(self, interface_metric: int) -> int | None:
        # -1/0xffffffff 表示不可用；采样期间接口度量也可能发生变化。
        if self.effective == 0xFFFFFFFF or self.effective < interface_metric:
            return None
        return self.effective - interface_metric


@dataclass(frozen=True)
class Iface:
    index: int
    alias: str
    description: str
    connected: bool
    if_metric: int
    auto_metric: bool
    dhcp: bool
    ipv4: tuple[str, ...] = ()
    guid: str = ""
    gateway: str | None = None
    route_metric: int | None = None
    effective: int | None = None
    route_count: int = 0

    def same_interface(self, other: Iface) -> bool:
        if self.index != other.index or self.alias != other.alias:
            return False
        if self.guid or other.guid:
            return bool(self.guid and other.guid and self.guid.lower() == other.guid.lower())
        return self.description == other.description


@dataclass(frozen=True)
class Snapshot:
    ts: float
    ifaces: tuple[Iface, ...]
    routes: tuple[Route, ...]
    warnings: tuple[str, ...] = ()
    elapsed_ms: float = 0.0

    @property
    def default_exits(self) -> tuple[Iface, ...]:
        candidates = [i for i in self.ifaces if i.connected and i.effective is not None]
        if not candidates:
            return ()
        best = min(i.effective for i in candidates)
        return tuple(i for i in candidates if i.effective == best)

    def find(self, index: int) -> Iface | None:
        return next((i for i in self.ifaces if i.index == index), None)


def assemble_snapshot(ifaces: list[Iface], routes: list[Route],
                      warnings: tuple[str, ...] = (), elapsed_ms: float = 0) -> Snapshot:
    by_index: dict[int, list[Route]] = {}
    for route in routes:
        by_index.setdefault(route.ifindex, []).append(route)
    enriched = []
    for iface in ifaces:
        own = by_index.get(iface.index, [])
        defaults = [r for r in own if r.is_default and r.effective != 0xFFFFFFFF]
        # 同一接口可能有多个网关，使用有效度量最低的一条，详情保留所有路由。
        best = min(defaults, key=lambda r: (r.effective, r.nexthop), default=None)
        enriched.append(replace(
            iface, route_count=len(own), gateway=best.nexthop if best else None,
            effective=best.effective if best else None,
            route_metric=best.route_metric(iface.if_metric) if best else None,
        ))
    return Snapshot(time.time(), tuple(enriched), tuple(routes), warnings, elapsed_ms)


def validate_metric(value: str | int) -> int:
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError("请输入 1–9999 之间的整数。")
    metric = int(text)
    if not METRIC_MIN <= metric <= METRIC_MAX:
        raise ValueError("接口度量必须在 1–9999 之间。")
    return metric


@dataclass(frozen=True)
class MetricSetting:
    metric: int
    automatic: bool

    @classmethod
    def of(cls, iface: Iface) -> MetricSetting:
        return cls(iface.if_metric, iface.auto_metric)

    @property
    def label(self) -> str:
        return f"自动（{self.metric}）" if self.automatic else str(self.metric)

    def matches(self, iface: Iface) -> bool:
        return (iface.auto_metric == self.automatic
                and (self.automatic or iface.if_metric == self.metric))


@dataclass(frozen=True)
class Change:
    iface: Iface
    before: MetricSetting
    after: MetricSetting

    @property
    def label(self) -> str:
        return f"{self.iface.alias}：{self.before.label} → {self.after.label}"


@dataclass(frozen=True)
class ChangePlan:
    title: str
    changes: tuple[Change, ...]


@dataclass
class HistoryEntry:
    title: str
    changes: tuple[Change, ...]
    ts: float = field(default_factory=time.time)
    success: bool = False
    message: str = ""
    undone: bool = False


def metric_plan(iface: Iface, metric: int | None) -> ChangePlan:
    if not iface.connected:
        raise ValueError("只能修改已连接的接口。")
    after = MetricSetting(iface.if_metric, True) if metric is None else MetricSetting(validate_metric(metric), False)
    if after.matches(iface):
        raise ValueError("当前已是该设置，无需修改。")
    title = "恢复自动跃点" if metric is None else "修改接口度量"
    return ChangePlan(title, (Change(iface, MetricSetting.of(iface), after),))


def preferred_plan(snapshot: Snapshot, selected: Iface) -> ChangePlan:
    """调整所有其它默认路由接口，确保所选默认路由的总度量更低。"""
    if not selected.connected or selected.route_metric is None:
        raise ValueError("设为首选需要已连接的接口和可用的 IPv4 默认路由。")
    target_effective = selected.route_metric + 10
    changes: list[Change] = []
    if not MetricSetting(10, False).matches(selected):
        changes.append(Change(selected, MetricSetting.of(selected), MetricSetting(10, False)))
    for iface in snapshot.ifaces:
        if iface.index == selected.index or not iface.connected or iface.effective is None:
            continue
        if iface.route_metric is None:
            raise ValueError(f"{iface.alias} 的路由度量暂不可用，请刷新后重试。")
        needed = max(iface.if_metric, target_effective - iface.route_metric + 10)
        if needed > METRIC_MAX:
            raise ValueError(f"无法在 1–9999 的范围内让 {iface.alias} 的默认路由优先级低于所选接口。")
        after = MetricSetting(needed, False)
        if not after.matches(iface):
            changes.append(Change(iface, MetricSetting.of(iface), after))
    if not changes:
        raise ValueError("该接口已经是首选，且其它默认路由的度量足够高。")
    return ChangePlan("设为首选", tuple(changes))
