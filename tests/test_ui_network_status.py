"""INP-03c: read-only and fail-closed coarse network status."""
import subprocess
from types import SimpleNamespace
import unittest

from sonos_ui.network_status import NetworkStatus, parse_device_status, read_network_status


class NetworkStatusTests(unittest.TestCase):
    def test_ethernet_preferred_when_both_connected(self):
        result = parse_device_status(
            "eth0:ethernet:connected\nwlan0:wifi:connected\n")
        self.assertEqual(result.preferred, "ethernet")
        self.assertEqual((result.ethernet, result.wifi),
                         ("connected", "connected"))

    def test_wifi_when_ethernet_disconnected(self):
        result = parse_device_status(
            "eth0:ethernet:disconnected\nwlan0:wifi:connected\n")
        self.assertEqual(result.preferred, "wifi")

    def test_no_auto_selection_without_connected_link(self):
        status = parse_device_status(
            "eth0:ethernet:connecting\nwlan0:wifi:disconnected\n")
        self.assertEqual(status.preferred, "none")
        self.assertEqual(status.ethernet, "connecting")

    def test_external_connection_and_multiple_devices(self):
        status = parse_device_status(
            "eth0:ethernet:connected (externally)\n"
            "eth1:ethernet:disconnected\nwlan0:wifi:unmanaged\n")
        self.assertEqual(status.preferred, "ethernet")
        self.assertEqual(status.wifi, "unavailable")

    def test_escaped_device_colon_and_unsupported_type(self):
        status = parse_device_status(
            r"eth\:backup:ethernet:connected" + "\n"
            "lo:loopback:connected\n")
        self.assertTrue(status.available)
        self.assertEqual(status.preferred, "ethernet")

    def test_never_persists_device_or_profile_details(self):
        result = parse_device_status("sensitive-device:ethernet:connected\n")
        self.assertNotIn("sensitive-device", repr(result))
        self.assertFalse(hasattr(result, "ssid"))

    def test_invalid_or_absent_data_fail_closed(self):
        for payload in ("", "nonsense", "eth0:ethernet", "x" * 8193, None):
            self.assertEqual(parse_device_status(payload), NetworkStatus())

    def test_runner_uses_only_read_only_nmcli_command(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0,
                                   stdout="eth0:ethernet:connected\n")
        self.assertEqual(read_network_status(runner=runner).preferred, "ethernet")
        argv, kwargs = calls[0]
        self.assertEqual(argv[-2:], ("device", "status"))
        self.assertNotIn("connection", argv)
        self.assertEqual(kwargs["timeout"], 2.0)
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        self.assertTrue(kwargs["capture_output"])

    def test_errors_are_unknown_without_retry(self):
        for result in (SimpleNamespace(returncode=1, stdout=""),
                       SimpleNamespace(returncode=12, stdout="")):
            calls = []
            def runner(*args, **kwargs):
                calls.append(1)
                return result
            self.assertEqual(read_network_status(runner=runner),
                             NetworkStatus())
            self.assertEqual(len(calls), 1)
        for exc in (OSError("unavailable"),
                    subprocess.TimeoutExpired(cmd=["nmcli"], timeout=2)):
            calls = []
            def runner(*args, **kwargs):
                calls.append(1)
                raise exc
            self.assertEqual(read_network_status(runner=runner),
                             NetworkStatus())
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
