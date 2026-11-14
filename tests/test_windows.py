"""无网络写入的 Win32 边界验证。"""
import ctypes
from ctypes import wintypes
import os
import unittest
import uuid

from elevate import SingleInstance
from netcore import IpHelper, MIB_IPFORWARDROW


@unittest.skipUnless(os.name == "nt", "Windows API")
class WindowsBoundaryTests(unittest.TestCase):
    def test_mutex_rejects_duplicate_and_releases(self):
        name = "Local\\NetMetric.Test." + uuid.uuid4().hex
        first = SingleInstance(name)
        second = SingleInstance(name)
        try:
            self.assertFalse(first.already_running)
            self.assertTrue(second.already_running)
        finally:
            first.close()
            second.close()
        third = SingleInstance(name)
        try:
            self.assertFalse(third.already_running)
        finally:
            third.close()

    def test_route_table_reallocates_when_size_changes(self):
        class GrowingTable:
            calls = 0
            def GetIpForwardTable(self, buffer, size_ptr, ordered):
                self.calls += 1
                size = ctypes.cast(size_ptr, ctypes.POINTER(wintypes.ULONG)).contents
                if self.calls == 1:
                    size.value = 4
                    return 122
                if self.calls == 2:
                    size.value = 4 + ctypes.sizeof(MIB_IPFORWARDROW)
                    return 122
                ctypes.c_uint32.from_buffer(buffer).value = 1
                row = MIB_IPFORWARDROW.from_buffer(buffer, 4)
                row.dwForwardIfIndex = 17
                row.dwForwardMetric1 = 70
                return 0
        helper = IpHelper.__new__(IpHelper)
        helper.dll = GrowingTable()
        rows = helper.read_routes()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].effective, 70)
        self.assertEqual(helper.dll.calls, 3)

    def test_bad_route_table_size_is_rejected(self):
        class BadTable:
            def GetIpForwardTable(self, buffer, size_ptr, ordered):
                if buffer is None:
                    ctypes.cast(size_ptr, ctypes.POINTER(wintypes.ULONG)).contents.value = 4
                    return 122
                ctypes.c_uint32.from_buffer(buffer).value = 500
                return 0
        helper = IpHelper.__new__(IpHelper)
        helper.dll = BadTable()
        with self.assertRaises(OSError):
            helper.read_routes()


if __name__ == "__main__":
    unittest.main()
