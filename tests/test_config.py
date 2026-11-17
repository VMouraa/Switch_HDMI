import copy
import json
from pathlib import Path
import tempfile
from threading import TIMEOUT_MAX
import unittest

from switch_hdmi.config import ConfigError, load_config
from switch_hdmi.detector import PS5State


EXAMPLE_CONFIG = {
    "mode": "simulator",
    "poll_interval_seconds": 0.001,
    "simulator": {"states": ["OFF", "ON", "REST", "ON", "OFF"], "repeat": False},
    "logging": {"file": "logs/ps5_detector.log"},
}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def write_config(self, data, encoding="utf-8"):
        self.path.write_text(json.dumps(data), encoding=encoding)

    def test_loads_states_interval_repeat_and_relative_log_path(self):
        self.write_config(EXAMPLE_CONFIG)
        config = load_config(self.path)
        self.assertEqual(config.poll_interval_seconds, 0.001)
        self.assertEqual(config.states, tuple(PS5State(state) for state in ["OFF", "ON", "REST", "ON", "OFF"]))
        self.assertFalse(config.repeat)
        self.assertEqual(config.log_file, self.path.parent / "logs" / "ps5_detector.log")
        self.assertFalse(config.log_file.parent.exists())

    def test_supports_bom_and_repeat(self):
        data = copy.deepcopy(EXAMPLE_CONFIG)
        data["simulator"]["repeat"] = True
        self.write_config(data, encoding="utf-8-sig")
        self.assertTrue(load_config(self.path).repeat)

    def test_preserves_absolute_log_path(self):
        data = copy.deepcopy(EXAMPLE_CONFIG)
        log_file = self.path.parent / "absolute.log"
        data["logging"]["file"] = str(log_file)
        self.write_config(data)
        self.assertEqual(load_config(self.path).log_file, log_file)

    def test_missing_malformed_or_invalid_encoding_file(self):
        with self.assertRaises(ConfigError):
            load_config(self.path)
        for raw in (b"{", b"\xff\xfe\x00"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(ConfigError):
                    load_config(self.path)

    def test_rejects_non_simulator_modes(self):
        for mode in ("network", "real", None):
            with self.subTest(mode=mode):
                data = copy.deepcopy(EXAMPLE_CONFIG)
                data["mode"] = mode
                self.write_config(data)
                with self.assertRaisesRegex(ConfigError, "mode"):
                    load_config(self.path)

    def test_rejects_invalid_intervals(self):
        for interval in (
            0, -1, True, "2", None, float("nan"), float("inf"),
            TIMEOUT_MAX * 2, 10**1000,
        ):
            with self.subTest(interval=interval):
                data = copy.deepcopy(EXAMPLE_CONFIG)
                data["poll_interval_seconds"] = interval
                self.write_config(data)
                with self.assertRaisesRegex(ConfigError, "poll_interval_seconds"):
                    load_config(self.path)

    def test_rejects_invalid_state_lists(self):
        for states in ([], "ON", None, ["on"], ["UNKNOWN"], [1], [None], [{}]):
            with self.subTest(states=states):
                data = copy.deepcopy(EXAMPLE_CONFIG)
                data["simulator"]["states"] = states
                self.write_config(data)
                with self.assertRaisesRegex(ConfigError, "simulator.states"):
                    load_config(self.path)

    def test_rejects_non_boolean_repeat(self):
        for repeat in ("false", 0, None):
            with self.subTest(repeat=repeat):
                data = copy.deepcopy(EXAMPLE_CONFIG)
                data["simulator"]["repeat"] = repeat
                self.write_config(data)
                with self.assertRaisesRegex(ConfigError, "simulator.repeat"):
                    load_config(self.path)

    def test_rejects_invalid_log_paths(self):
        for filename in ("", " ", None, 12, "bad\x00.log"):
            with self.subTest(filename=filename):
                data = copy.deepcopy(EXAMPLE_CONFIG)
                data["logging"]["file"] = filename
                self.write_config(data)
                with self.assertRaisesRegex(ConfigError, "logging.file"):
                    load_config(self.path)

    def test_rejects_missing_unknown_and_non_object_sections(self):
        variants = [[], {}, {**EXAMPLE_CONFIG, "unexpected": True}]
        for section in ("simulator", "logging"):
            for invalid in (None, [], {}, {"unexpected": True}):
                variants.append({**EXAMPLE_CONFIG, section: invalid})
        for data in variants:
            with self.subTest(data=data):
                self.write_config(data)
                with self.assertRaises(ConfigError):
                    load_config(self.path)

    def test_repository_example_is_valid(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.json")
        self.assertEqual(set(config.states), {PS5State.ON, PS5State.REST, PS5State.OFF})
