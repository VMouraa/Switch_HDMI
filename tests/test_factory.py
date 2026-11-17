from dataclasses import replace
import logging
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from switch_hdmi.config import AppConfig
from switch_hdmi.detector import PS5State, SimulatedPS5Detector
from switch_hdmi.factory import create_detector
from switch_hdmi.network_detector import NetworkPS5Detector


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(1, (PS5State.ON, PS5State.REST), False, Path("test.log"))
        self.logger = Mock(spec=logging.Logger)

    def test_default_creates_simulator_without_network(self):
        with patch("socket.socket", side_effect=AssertionError("Rede não autorizada em simulação")):
            detector = create_detector(self.config, self.logger)
            self.assertIsInstance(detector, SimulatedPS5Detector)
            self.assertEqual(detector.read_state(), PS5State.ON)

    def test_false_creates_real_detector_without_sending_until_read(self):
        config = replace(self.config, simulation_mode=False, ps5_ip="192.168.1.10")
        with patch("socket.socket") as sock:
            detector = create_detector(config, self.logger)
        self.assertIsInstance(detector, NetworkPS5Detector)
        self.assertEqual(detector.config, config.network)
        sock.assert_not_called()

    def test_missing_ip_fails_instead_of_silently_simulating(self):
        with self.assertRaises(ValueError):
            create_detector(replace(self.config, simulation_mode=False), self.logger)
