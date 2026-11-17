from dataclasses import FrozenInstanceError
import logging
import unittest
from unittest.mock import Mock, call

from switch_hdmi.detector import PS5State
from switch_hdmi.monitor import StateMonitor
from switch_hdmi.observations import StateHistory


class ObservationTests(unittest.TestCase):
    def test_no_observation_before_first_read(self):
        history = StateHistory()
        self.assertIsNone(history.latest)

    def test_initial_unknown_at_zero_is_not_confused_with_missing_timestamp(self):
        history = StateHistory(clock=Mock(side_effect=[0.0, 60.0]))
        initial = history.observe(PS5State.UNKNOWN)
        self.assertIsNone(initial.last_known_state)
        self.assertIsNone(initial.last_known_at)
        self.assertEqual(initial.unknown_since, 0.0)
        self.assertEqual(initial.unknown_duration_seconds, 0.0)
        later = history.observe(PS5State.UNKNOWN)
        self.assertEqual(later.unknown_since, 0.0)
        self.assertEqual(later.consecutive_unknown, 2)
        self.assertEqual(later.unknown_duration_seconds, 60.0)

    def test_known_state_starts_without_unknown_streak(self):
        for state in (PS5State.ON, PS5State.REST, PS5State.OFF):
            with self.subTest(state=state):
                history = StateHistory(clock=lambda: 10.0)
                observation = history.observe(state)
                self.assertEqual(observation.state, state)
                self.assertEqual(observation.last_known_state, state)
                self.assertEqual(observation.last_known_at, 10.0)
                self.assertIsNone(observation.unknown_since)
                self.assertEqual(observation.consecutive_unknown, 0)
                self.assertEqual(observation.unknown_duration_seconds, 0.0)

    def test_unknown_keeps_last_known_state_only_as_historical_evidence(self):
        history = StateHistory(clock=Mock(side_effect=[10.0, 20.0, 620.0]))
        history.observe(PS5State.ON)
        history.observe(PS5State.UNKNOWN)
        observation = history.observe(PS5State.UNKNOWN)
        self.assertEqual(observation.state, PS5State.UNKNOWN)
        self.assertEqual(observation.last_known_state, PS5State.ON)
        self.assertEqual(observation.last_known_at, 10.0)
        self.assertEqual(observation.unknown_since, 20.0)
        self.assertEqual(observation.consecutive_unknown, 2)
        self.assertEqual(observation.unknown_duration_seconds, 600.0)

    def test_rest_resets_streak_and_next_unknown_starts_new_period(self):
        history = StateHistory(clock=Mock(side_effect=[10.0, 20.0, 100.0, 120.0]))
        history.observe(PS5State.UNKNOWN)
        history.observe(PS5State.UNKNOWN)
        restored = history.observe(PS5State.REST)
        self.assertEqual(restored.last_known_state, PS5State.REST)
        self.assertEqual(restored.last_known_at, 100.0)
        self.assertIsNone(restored.unknown_since)
        self.assertEqual(restored.consecutive_unknown, 0)
        self.assertEqual(restored.unknown_duration_seconds, 0.0)
        lost_again = history.observe(PS5State.UNKNOWN)
        self.assertEqual(lost_again.unknown_since, 120.0)
        self.assertEqual(lost_again.consecutive_unknown, 1)
        self.assertEqual(lost_again.last_known_state, PS5State.REST)

    def test_stable_known_state_refreshes_last_valid_read_time(self):
        history = StateHistory(clock=Mock(side_effect=[10.0, 20.0]))
        history.observe(PS5State.ON)
        observation = history.observe(PS5State.ON)
        self.assertEqual(observation.last_known_at, 20.0)
        self.assertEqual(observation.consecutive_unknown, 0)

    def test_snapshots_are_immutable_and_remain_unchanged_after_other_reads(self):
        history = StateHistory(clock=Mock(side_effect=[10.0, 20.0]))
        initial = history.observe(PS5State.UNKNOWN)
        latest = history.observe(PS5State.ON)
        self.assertIs(history.latest, latest)
        self.assertEqual(initial.state, PS5State.UNKNOWN)
        self.assertEqual(initial.consecutive_unknown, 1)
        with self.assertRaises(FrozenInstanceError):
            initial.state = PS5State.OFF

    def test_invalid_state_does_not_overwrite_evidence(self):
        clock = Mock(return_value=10.0)
        history = StateHistory(clock=clock)
        initial = history.observe(PS5State.ON)
        for state in ("OFF", None, 200):
            with self.subTest(state=state), self.assertRaises(ValueError):
                history.observe(state)
        self.assertIs(history.latest, initial)
        clock.assert_called_once()


class MonitorObservationTests(unittest.TestCase):
    def test_monitor_collects_stable_and_changing_states_without_duplicate_transitions(self):
        detector = Mock()
        detector.read_state.side_effect = [
            PS5State.ON, PS5State.UNKNOWN, PS5State.UNKNOWN, PS5State.REST,
        ]
        logger = Mock(spec=logging.Logger)
        monitor = StateMonitor(
            detector, 1, logger, simulation_mode=False,
            clock=Mock(side_effect=[0.0, 10.0, 310.0, 320.0]),
        )
        self.assertIsNone(monitor.observation)
        for expected in (PS5State.ON, PS5State.UNKNOWN, PS5State.UNKNOWN):
            self.assertEqual(monitor.poll_once(), expected)
        self.assertEqual(monitor.observation.unknown_duration_seconds, 300.0)
        self.assertEqual(monitor.observation.consecutive_unknown, 2)
        self.assertEqual(monitor.current_state, PS5State.UNKNOWN)
        self.assertEqual(monitor.poll_once(), PS5State.REST)
        self.assertEqual(monitor.observation.consecutive_unknown, 0)
        self.assertEqual(logger.info.call_args_list, [
            call("Estado inicial do PS5 (rede/DDP): %s", "ON"),
            call("Mudança de estado do PS5 (rede/DDP): %s -> %s", "ON", "UNKNOWN"),
            call("Mudança de estado do PS5 (rede/DDP): %s -> %s", "UNKNOWN", "REST"),
        ])

    def test_detector_failure_or_invalid_result_keeps_last_observation(self):
        detector = Mock()
        detector.read_state.side_effect = [PS5State.ON, "OFF", RuntimeError("falha")]
        monitor = StateMonitor(detector, 1, Mock(), clock=lambda: 0.0)
        monitor.poll_once()
        initial = monitor.observation
        with self.assertRaises(ValueError):
            monitor.poll_once()
        self.assertIs(monitor.observation, initial)
        with self.assertRaises(RuntimeError):
            monitor.poll_once()
        self.assertIs(monitor.observation, initial)
        self.assertEqual(monitor.current_state, PS5State.ON)
