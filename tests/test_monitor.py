import logging
from threading import Event, TIMEOUT_MAX
import unittest
from unittest.mock import Mock, call

from switch_hdmi.detector import PS5State, SimulatedPS5Detector
from switch_hdmi.monitor import StateMonitor


class MonitorTests(unittest.TestCase):
    def make_monitor(self, states=(PS5State.OFF, PS5State.ON, PS5State.REST)):
        self.logger = Mock(spec=logging.Logger)
        return StateMonitor(SimulatedPS5Detector(states), 2.5, self.logger)

    def test_logs_initial_state_and_only_real_changes(self):
        monitor = self.make_monitor((PS5State.OFF, PS5State.OFF, PS5State.ON, PS5State.REST, PS5State.OFF))
        for _ in range(7):
            monitor.poll_once()
        self.assertEqual(monitor.current_state, PS5State.OFF)
        self.assertEqual(self.logger.info.call_args_list, [
            call("Estado inicial do PS5 (simulado): %s", "OFF"),
            call("Mudança de estado do PS5 (simulado): %s -> %s", "OFF", "ON"),
            call("Mudança de estado do PS5 (simulado): %s -> %s", "ON", "REST"),
            call("Mudança de estado do PS5 (simulado): %s -> %s", "REST", "OFF"),
        ])

    def test_logs_transition_at_repeat_boundary(self):
        logger = Mock(spec=logging.Logger)
        monitor = StateMonitor(SimulatedPS5Detector((PS5State.ON, PS5State.REST), True), 1, logger)
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = False
        self.assertEqual(monitor.run(max_polls=3, stop_event=stop), 3)
        self.assertEqual(logger.info.call_args_list[-1],
                         call("Mudança de estado do PS5 (simulado): %s -> %s", "REST", "ON"))

    def test_waits_configured_interval_between_polls_without_final_wait(self):
        monitor = self.make_monitor()
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = False
        self.assertEqual(monitor.run(max_polls=3, stop_event=stop), 3)
        self.assertEqual(stop.wait.call_args_list, [call(2.5), call(2.5)])
        self.assertEqual(monitor.current_state, PS5State.REST)

    def test_single_poll_does_not_wait(self):
        monitor = self.make_monitor()
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        self.assertEqual(monitor.run(max_polls=1, stop_event=stop), 1)
        stop.wait.assert_not_called()

    def test_preexisting_stop_does_not_read(self):
        monitor = self.make_monitor()
        stop = Event()
        stop.set()
        self.assertEqual(monitor.run(stop_event=stop), 0)
        self.assertIsNone(monitor.current_state)
        self.logger.info.assert_not_called()

    def test_stop_during_wait_prevents_next_read(self):
        monitor = self.make_monitor()
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = True
        self.assertEqual(monitor.run(stop_event=stop), 1)
        self.assertEqual(monitor.current_state, PS5State.OFF)

    def test_invalid_poll_limits_fail_before_reading(self):
        monitor = self.make_monitor()
        for limit in (0, -1, True, 1.5, "3"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                monitor.run(max_polls=limit)
        self.assertIsNone(monitor.current_state)

    def test_invalid_intervals(self):
        for interval in (
            0, -1, True, "1", float("nan"), float("inf"),
            TIMEOUT_MAX * 2, 10**1000,
        ):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                StateMonitor(Mock(), interval, Mock())

    def test_invalid_detector_output_preserves_previous_state(self):
        detector = Mock()
        detector.read_state.side_effect = [PS5State.ON, "OFF"]
        monitor = StateMonitor(detector, 1, Mock())
        monitor.poll_once()
        with self.assertRaises(ValueError):
            monitor.poll_once()
        self.assertEqual(monitor.current_state, PS5State.ON)

    def test_detector_error_is_not_reported_as_off(self):
        detector = Mock()
        detector.read_state.side_effect = RuntimeError("Falha de leitura")
        monitor = StateMonitor(detector, 1, Mock())
        with self.assertRaisesRegex(RuntimeError, "Falha de leitura"):
            monitor.poll_once()
        self.assertIsNone(monitor.current_state)
