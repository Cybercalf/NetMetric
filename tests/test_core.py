from dataclasses import replace
import json
import re
import subprocess
import unittest
from unittest.mock import patch

from controller import Controller
from models import (Change, ChangePlan, Iface, MetricSetting, Route, assemble_snapshot,
                    metric_plan, preferred_plan, validate_metric)
from writer import MetricWriter, WriteError


def iface(index=1, metric=35, auto=False, **kwargs):
    defaults = dict(index=index, alias=f"网卡 {index}", description="测试网卡", connected=True,
                    if_metric=metric, auto_metric=auto, dhcp=True, guid=f"guid-{index}")
    defaults.update(kwargs)
    return Iface(**defaults)


class FakeWriter:
    def __init__(self, ifaces, fail_index=None):
        self.ifaces = {i.index: i for i in ifaces}
        self.calls = []
        self.fail_index = fail_index

    def current(self, original):
        current = self.ifaces[original.index]
        if not current.connected or not original.same_interface(current):
            raise WriteError("接口不可用")
        return current

    def apply(self, original, setting):
        self.calls.append((original.index, setting))
        self.ifaces[original.index] = replace(self.ifaces[original.index],
                                              if_metric=setting.metric, auto_metric=setting.automatic)
        if self.fail_index == original.index:
            self.fail_index = None
            raise WriteError("模拟部分写入后命令失败")


class ModelTests(unittest.TestCase):
    def test_reject_invalid_metrics(self):
        for value in ("", "abc", "1.2", "-1", "0", "10000", "99999", "１２", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_metric(value)
        self.assertEqual(validate_metric(" 9999 "), 9999)

    def test_multiple_gateways_choose_lowest_effective(self):
        routes = [Route(1, "0.0.0.0", "0.0.0.0", "10.0.0.1", 90),
                  Route(1, "0.0.0.0", "0.0.0.0", "10.0.0.2", 70)]
        snapshot = assemble_snapshot([iface()], routes)
        self.assertEqual(snapshot.ifaces[0].gateway, "10.0.0.2")
        self.assertEqual(snapshot.ifaces[0].route_metric, 35)
        self.assertEqual(snapshot.ifaces[0].route_count, 2)

    def test_unknown_metrics_are_not_negative_or_huge(self):
        self.assertIsNone(Route(1, "0.0.0.0", "0.0.0.0", "0.0.0.0", 10).route_metric(35))
        route = Route(1, "0.0.0.0", "0.0.0.0", "0.0.0.0", 0xffffffff)
        self.assertIsNone(route.route_metric(35))
        self.assertIsNone(assemble_snapshot([iface()], [route]).ifaces[0].effective)

    def test_equal_defaults_are_all_candidates(self):
        a, b = iface(), iface(2)
        routes = [Route(i, "0.0.0.0", "0.0.0.0", "10.0.0.1", 70) for i in (1, 2)]
        snapshot = assemble_snapshot([a, b], routes)
        self.assertEqual(len(snapshot.default_exits), 2)
        snapshot = assemble_snapshot([a, replace(b, connected=False)], routes)
        self.assertEqual(len(snapshot.default_exits), 1)

    def test_dhcp_does_not_determine_automatic_metric(self):
        self.assertTrue(iface(auto=True, dhcp=False).auto_metric)

    def test_manual_metric_disables_auto_and_undo_preserves_mode(self):
        change = metric_plan(iface(auto=True), 10).changes[0]
        self.assertTrue(change.before.automatic)
        self.assertFalse(change.after.automatic)

    def test_disconnected_edit_rejected(self):
        with self.assertRaises(ValueError):
            metric_plan(replace(iface(), connected=False), 10)

    def test_preferred_adjusts_competitor_using_total_metric(self):
        # 所选接口的路由度量为 90，仅把它的接口度量改为 10 仍然不够。
        snapshot = assemble_snapshot([iface(metric=35), iface(2, metric=5)], [
            Route(1, "0.0.0.0", "0.0.0.0", "10.0.0.1", 125),
            Route(2, "0.0.0.0", "0.0.0.0", "10.0.0.2", 5)])
        plan = preferred_plan(snapshot, snapshot.ifaces[0])
        self.assertEqual(plan.changes[0].after.metric, 10)
        self.assertGreater(plan.changes[1].after.metric, 100)

    def test_preferred_refuses_unachievable_metric(self):
        snapshot = assemble_snapshot([iface(), iface(2)], [
            Route(1, "0.0.0.0", "0.0.0.0", "10.0.0.1", 10035),
            Route(2, "0.0.0.0", "0.0.0.0", "10.0.0.2", 35)])
        with self.assertRaises(ValueError):
            preferred_plan(snapshot, snapshot.ifaces[0])

    def test_identity_checks_guid_and_alias(self):
        self.assertFalse(iface().same_interface(replace(iface(), guid="other")))
        self.assertFalse(iface().same_interface(replace(iface(), alias="renamed")))


class TransactionTests(unittest.TestCase):
    def test_rollback_restores_persistent_only_change_even_if_active_unchanged(self):
        original = iface(metric=40)
        class PersistentOnlyFailure(FakeWriter):
            persistent = 40
            def apply(self, original, setting):
                self.calls.append((original.index, setting))
                self.persistent = setting.metric
                if setting.metric == 35:
                    raise WriteError("活动配置回读失败")
        writer = PersistentOnlyFailure([original])
        record = Controller().execute_plan(metric_plan(original, 35), writer)
        self.assertFalse(record.success)
        self.assertEqual(writer.ifaces[1].if_metric, 40)
        self.assertEqual(writer.persistent, 40)
        self.assertEqual(len(writer.calls), 2)

    def test_rollback_includes_partially_written_failing_interface(self):
        a, b = iface(auto=True), iface(2)
        writer = FakeWriter([a, b], fail_index=2)
        plan = ChangePlan("批量修改", (metric_plan(a, 10).changes[0], metric_plan(b, 100).changes[0]))
        record = Controller().execute_plan(plan, writer)
        self.assertFalse(record.success)
        self.assertEqual(writer.ifaces[1], a)
        self.assertEqual(writer.ifaces[2], b)
        self.assertEqual([i for i, _ in writer.calls], [1, 2, 2, 1])

    def test_stale_plan_performs_no_write(self):
        original = iface()
        writer = FakeWriter([replace(original, if_metric=99)])
        record = Controller().execute_plan(metric_plan(original, 10), writer)
        self.assertFalse(record.success)
        self.assertEqual(writer.calls, [])

    def test_success_and_undo_restore_automatic_mode(self):
        original = iface(auto=True)
        writer = FakeWriter([original])
        controller = Controller()
        record = controller.execute_plan(metric_plan(original, 10), writer)
        self.assertTrue(record.success)
        controller._undo(writer)
        self.assertTrue(record.undone)
        self.assertTrue(writer.ifaces[1].auto_metric)

    def test_undo_does_not_overwrite_external_changes(self):
        original = iface()
        writer = FakeWriter([original])
        controller = Controller()
        record = controller.execute_plan(metric_plan(original, 10), writer)
        writer.ifaces[1] = replace(writer.ifaces[1], if_metric=25)
        controller._undo(writer)
        self.assertFalse(record.undone)
        self.assertEqual(writer.ifaces[1].if_metric, 25)

    def test_rollback_failure_is_reported(self):
        original = iface()
        writer = FakeWriter([original])
        def fail_apply(original, setting):
            writer.ifaces[original.index] = replace(original, if_metric=10)
            raise WriteError("写入/恢复都失败")
        writer.apply = fail_apply
        record = Controller().execute_plan(metric_plan(original, 10), writer)
        self.assertIn("未能恢复", record.message)


class WriterTests(unittest.TestCase):
    def make_writer(self, original, admin=True, returncode=0):
        class Reader:
            def read_ifaces(self, **kwargs):
                return [original], ()
        commands = []
        def runner(command, **kwargs):
            commands.append((command, kwargs))
            return subprocess.CompletedProcess(command, returncode, "", "failure" if returncode else "")
        writer = MetricWriter(Reader(), admin_check=lambda: admin, runner=runner)
        return writer, commands

    def test_non_admin_never_runs_command(self):
        original = iface()
        writer, commands = self.make_writer(original, admin=False)
        with self.assertRaises(WriteError):
            writer.apply(original, MetricSetting(10, False))
        self.assertFalse(commands)

    def test_manual_command_uses_index_disables_auto_and_updates_both_stores(self):
        original = iface()
        writer, commands = self.make_writer(original)
        with patch.object(writer, "verify") as verify:
            writer.apply(original, MetricSetting(10, False))
        command, kwargs = commands[0]
        script = command[-1]
        self.assertIn("Set-NetIPInterface -InterfaceIndex 1", script)
        self.assertIn("-InterfaceMetric 10", script)
        self.assertIn("-AutomaticMetric Disabled", script)
        self.assertIn("-PolicyStore PersistentStore", script)
        self.assertIn("-PolicyStore ActiveStore", script)
        self.assertEqual(script.count("Set-NetIPInterface"), 2)
        self.assertFalse(kwargs["shell"])
        verify.assert_called_once()

    def test_auto_command_updates_persistent_and_active_store(self):
        original = iface()
        writer, commands = self.make_writer(original)
        with patch.object(writer, "verify"):
            writer.apply(original, MetricSetting(35, True))
        command = commands[0][0][-1]
        self.assertIn("-AutomaticMetric Enabled", command)
        self.assertIn("-PolicyStore PersistentStore", command)
        self.assertIn("-PolicyStore ActiveStore", command)
        self.assertIn("exit 1", command)
        self.assertNotIn(original.alias, command)

    def test_command_failure_keeps_return_code(self):
        original = iface()
        writer, _ = self.make_writer(original, returncode=5)
        with self.assertRaisesRegex(WriteError, "返回码 5"):
            writer.apply(original, MetricSetting(10, False))

    def test_readback_rejects_success_exit_with_wrong_value(self):
        original = iface()
        writer, _ = self.make_writer(original)
        with patch("writer.time.sleep"), self.assertRaisesRegex(WriteError, "回读校验失败"):
            writer.verify(original, MetricSetting(10, False), timeout=0)

    def test_readback_rejects_persistent_value_different_from_active_value(self):
        original = iface(metric=35)
        writer, _ = self.make_writer(original)
        states = {"active": {"metric": 35, "automatic": 0},
                  "persistent": {"metric": 40, "automatic": 0}}
        with patch("writer.time.sleep"), patch.object(writer, "read_stores", return_value=states), \
                self.assertRaisesRegex(WriteError, "持久配置"):
            writer.verify(original, MetricSetting(35, False), timeout=0)

    def test_regression_manual_write_and_undo_update_active_and_persistent(self):
        original = iface(metric=40)
        class Network:
            active = original
            persistent = MetricSetting.of(original)
            def read_ifaces(self, **kwargs):
                return [self.active], ()
            def run(self, command, **kwargs):
                script = command[-1]
                if "Set-NetIPInterface" in script:
                    automatic = "-AutomaticMetric Enabled" in script
                    metric = re.search(r"-InterfaceMetric (\d+)", script)
                    metric = int(metric.group(1)) if metric else 25
                    setting = MetricSetting(metric, automatic)
                    if "-PolicyStore PersistentStore" in script:
                        self.persistent = setting
                    if "-PolicyStore ActiveStore" in script:
                        self.active = replace(self.active, if_metric=metric, auto_metric=automatic)
                    return subprocess.CompletedProcess(command, 0, "", "")
                states = {"active": {"metric": self.active.if_metric, "automatic": int(self.active.auto_metric)},
                          "persistent": {"metric": self.persistent.metric, "automatic": int(self.persistent.automatic)}}
                return subprocess.CompletedProcess(command, 0, json.dumps(states), "")
        network = Network()
        writer = MetricWriter(network, admin_check=lambda: True, runner=network.run)
        controller = Controller()
        with patch("writer.time.sleep"):
            record = controller.execute_plan(metric_plan(original, 35), writer)
            self.assertTrue(record.success, record.message)
            self.assertEqual(network.active.if_metric, 35)
            self.assertEqual(network.persistent.metric, 35)
            controller._undo(writer)
        self.assertTrue(record.undone)
        self.assertEqual(network.active.if_metric, 40)
        self.assertEqual(network.persistent.metric, 40)

    def test_store_query_validates_both_stores_and_preserves_utf8(self):
        original = iface()
        writer, commands = self.make_writer(original)
        state = {"active": {"metric": 35, "automatic": 0},
                 "persistent": {"metric": 35, "automatic": 0}}
        with patch.object(writer, "_run", return_value=json.dumps(state)):
            self.assertEqual(writer.read_stores(original), state)
        with patch.object(writer, "_run", return_value="unexpected output"), self.assertRaises(WriteError):
            writer.read_stores(original)


if __name__ == "__main__":
    unittest.main()
