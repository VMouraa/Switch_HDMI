import copy
import json
from pathlib import Path
import tempfile
from threading import TIMEOUT_MAX
import unittest

from switch_hdmi.config import ConfigError, load_config
from switch_hdmi.detector import PS5State
from tests.samples import MODERN_CONFIG


class NetworkConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return load_config(self.path)

    def test_new_config_defaults_to_simulation_without_ip(self):
        config = self.load(MODERN_CONFIG)
        self.assertTrue(config.simulation_mode)
        self.assertIsNone(config.ps5_ip)
        self.assertEqual(config.check_interval_seconds, 0.001)
        self.assertEqual(config.poll_interval_seconds, config.check_interval_seconds)
        self.assertEqual(config.states, tuple(PS5State(s) for s in ["OFF", "ON", "REST", "ON", "OFF"]))

    def test_real_mode_loads_all_network_parameters(self):
        data = copy.deepcopy(MODERN_CONFIG)
        data.update(simulation_mode=False, ps5_ip="192.168.1.10")
        data["network"].update(
            discovery_port=9304, timeout_seconds=0.25, retries=3,
            local_ip="192.168.1.20", local_port=0,
        )
        config = self.load(data)
        self.assertFalse(config.simulation_mode)
        self.assertEqual(config.ps5_ip, "192.168.1.10")
        self.assertEqual(config.network.discovery_port, 9304)
        self.assertEqual(config.network.timeout_seconds, 0.25)
        self.assertEqual(config.network.retries, 3)
        self.assertEqual(config.network.local_ip, "192.168.1.20")
        self.assertEqual(config.network.local_port, 0)

    def test_omitted_mode_flag_defaults_to_simulation(self):
        data = copy.deepcopy(MODERN_CONFIG)
        del data["simulation_mode"]
        self.assertTrue(self.load(data).simulation_mode)

    def test_real_mode_requires_ip(self):
        data = copy.deepcopy(MODERN_CONFIG)
        data["simulation_mode"] = False
        with self.assertRaisesRegex(ConfigError, "ps5_ip"):
            self.load(data)

    def test_rejects_invalid_simulation_flag(self):
        for value in ("false", 0, 1, None):
            with self.subTest(value=value):
                data = copy.deepcopy(MODERN_CONFIG)
                data["simulation_mode"] = value
                with self.assertRaisesRegex(ConfigError, "simulation_mode"):
                    self.load(data)

    def test_ip_must_be_literal_ipv4_unicast_even_in_simulation(self):
        for value in ("ps5.local", "", "::1", "999.1.1.1", "224.0.0.1", "0.0.0.0", "255.255.255.255", 123):
            with self.subTest(value=value):
                data = copy.deepcopy(MODERN_CONFIG)
                data["ps5_ip"] = value
                with self.assertRaisesRegex(ConfigError, "ps5_ip"):
                    self.load(data)

    def test_rejects_invalid_check_interval(self):
        for value in (0, -1, True, "1", None, float("inf"), float("nan"), TIMEOUT_MAX * 2, 10**1000):
            with self.subTest(value=value):
                data = copy.deepcopy(MODERN_CONFIG)
                data["check_interval_seconds"] = value
                with self.assertRaisesRegex(ConfigError, "check_interval_seconds"):
                    self.load(data)

    def test_rejects_invalid_network_fields(self):
        variants = {
            "discovery_port": [0, -1, 65536, True, 1.5, "9302"],
            "local_port": [-1, 65536, False, 1.5, "9303"],
            "timeout_seconds": [0, -1, True, 31, "1", None, float("inf"), float("nan"), 10**1000],
            "retries": [-1, 4, True, 0.5, "1", None],
            "local_ip": ["example.com", "::", "224.0.0.1", "255.255.255.255", None],
        }
        for name, values in variants.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    data = copy.deepcopy(MODERN_CONFIG)
                    data["network"][name] = value
                    with self.assertRaisesRegex(ConfigError, f"network.{name}"):
                        self.load(data)

    def test_rejects_incomplete_or_unknown_network_sections(self):
        for value in (None, [], {}, {**MODERN_CONFIG["network"], "ping": True}):
            with self.subTest(value=value):
                data = copy.deepcopy(MODERN_CONFIG)
                data["network"] = value
                with self.assertRaisesRegex(ConfigError, "network"):
                    self.load(data)

    def test_legacy_and_new_names_cannot_be_mixed(self):
        for field, value in (("mode", "simulator"), ("poll_interval_seconds", 1)):
            data = {**MODERN_CONFIG, field: value}
            with self.subTest(field=field), self.assertRaises(ConfigError):
                self.load(data)

    def test_unknown_is_not_added_to_simulator_config(self):
        data = copy.deepcopy(MODERN_CONFIG)
        data["simulator"]["states"] = ["UNKNOWN"]
        with self.assertRaisesRegex(ConfigError, "simulator.states"):
            self.load(data)

    def test_shipped_config_is_safe_default(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.json")
        self.assertTrue(config.simulation_mode)
        self.assertIsNone(config.ps5_ip)
