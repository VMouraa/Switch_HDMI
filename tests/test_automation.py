from dataclasses import replace
import logging
import socket
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from switch_hdmi.automation import (
    AutomationController, AutomationControlError, AutomationError, AutomationRunner,
    DecisionStatus,
)
from switch_hdmi.detector import PS5State
from switch_hdmi.config import NetworkConfig
from switch_hdmi.displays import DisplayController, DisplayError, DisplayVerificationError
from switch_hdmi.multimonitor_tool import parse_inventory
from switch_hdmi.network_detector import NetworkPS5Detector, SEARCH_PACKET
from tests.samples import DDP_ON, DDP_REST
from tests.test_displays import FakeDisplayBackend


INVENTORY = Path(__file__).parent / "fixtures" / "monitors.csv"
MONITOR_ID = parse_inventory(INVENTORY.read_bytes())[1].monitor_id


class AutomationControllerTests(unittest.TestCase):
    def setUp(self):
        self.displays = Mock(spec=DisplayController)
        self.logger = Mock(spec=logging.Logger)
        self.controller = AutomationController(self.displays, MONITOR_ID, self.logger)

    def observe(self, *states):
        return [self.controller.observe(state) for state in states]

    def test_two_on_reads_request_disable_only_on_second(self):
        first = self.controller.observe(PS5State.ON)
        self.assertEqual(first.status, DecisionStatus.PENDING)
        self.displays.set_enabled.assert_not_called()
        second = self.controller.observe(PS5State.ON)
        self.assertEqual(second.status, DecisionStatus.REQUESTED)
        self.assertFalse(second.requested_enabled)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=True)

    def test_on_to_rest_requests_disable_then_enable(self):
        decisions = self.observe(PS5State.ON, PS5State.ON, PS5State.REST, PS5State.REST)
        self.assertEqual(decisions[2].status, DecisionStatus.PENDING)
        self.assertEqual(self.displays.set_enabled.call_args_list, [
            call(MONITOR_ID, False, dry_run=True), call(MONITOR_ID, True, dry_run=True),
        ])

    def test_rest_to_on_requests_enable_then_disable(self):
        self.observe(PS5State.REST, PS5State.REST, PS5State.ON, PS5State.ON)
        self.assertEqual(self.displays.set_enabled.call_args_list, [
            call(MONITOR_ID, True, dry_run=True), call(MONITOR_ID, False, dry_run=True),
        ])

    def test_on_to_unknown_does_not_enable_or_forget_last_intention(self):
        self.observe(PS5State.ON, PS5State.ON)
        unknown = self.controller.observe(PS5State.UNKNOWN)
        self.assertEqual(unknown.status, DecisionStatus.IGNORED_UNKNOWN)
        self.assertFalse(self.controller.last_requested_enabled)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=True)

    def test_unknown_to_rest_requires_two_new_reads(self):
        self.observe(PS5State.UNKNOWN, PS5State.REST)
        self.displays.set_enabled.assert_not_called()
        self.controller.observe(PS5State.REST)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, True, dry_run=True)

    def test_unknown_interrupts_confirmation(self):
        self.observe(PS5State.ON, PS5State.UNKNOWN, PS5State.ON)
        self.displays.set_enabled.assert_not_called()
        self.controller.observe(PS5State.ON)
        self.displays.set_enabled.assert_called_once()

    def test_flickering_states_never_reach_confirmation(self):
        self.observe(*([PS5State.ON, PS5State.REST] * 10))
        self.displays.set_enabled.assert_not_called()

    def test_repeated_confirmed_state_does_not_repeat_commands(self):
        decisions = self.observe(*([PS5State.ON] * 10))
        self.assertEqual(decisions[-1].status, DecisionStatus.DUPLICATE)
        self.assertEqual(decisions[-1].consecutive_reads, 2)
        self.displays.set_enabled.assert_called_once()

    def test_unknown_then_same_known_state_does_not_repeat_last_request(self):
        self.observe(PS5State.ON, PS5State.ON, PS5State.UNKNOWN, PS5State.ON, PS5State.ON)
        self.displays.set_enabled.assert_called_once()

    def test_incomplete_opposite_transition_does_not_repeat_last_request(self):
        self.observe(PS5State.ON, PS5State.ON, PS5State.REST, PS5State.ON, PS5State.ON)
        self.displays.set_enabled.assert_called_once()

    def test_off_without_authority_never_reactivates_and_resets_confirmation(self):
        self.observe(PS5State.ON, PS5State.ON, *([PS5State.OFF] * 10), PS5State.REST)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=True)
        self.controller.observe(PS5State.REST)
        self.assertEqual(self.displays.set_enabled.call_count, 2)

    def test_trusted_off_requires_two_trusted_reads(self):
        self.controller.observe(PS5State.OFF)
        self.controller.observe(PS5State.OFF, off_is_trusted=True)
        self.displays.set_enabled.assert_not_called()
        self.controller.observe(PS5State.OFF, off_is_trusted=True)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, True, dry_run=True)

    def test_untrusted_off_between_trusted_reads_breaks_streak(self):
        self.controller.observe(PS5State.OFF, off_is_trusted=True)
        self.controller.observe(PS5State.OFF)
        self.controller.observe(PS5State.OFF, off_is_trusted=True)
        self.displays.set_enabled.assert_not_called()

    def test_explicit_off_policy_enables_after_confirmation_but_not_for_unknown(self):
        controller = AutomationController(self.displays, MONITOR_ID, self.logger, allow_off_recovery=True)
        for _ in range(10):
            controller.observe(PS5State.UNKNOWN)
        self.displays.set_enabled.assert_not_called()
        controller.observe(PS5State.OFF)
        self.displays.set_enabled.assert_not_called()
        controller.observe(PS5State.OFF)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, True, dry_run=True)

    def test_custom_confirmation_count_cannot_be_less_than_two(self):
        for value in (0, 1, -1, True, "2", 2.5):
            with self.subTest(value=value), self.assertRaises(AutomationError):
                AutomationController(self.displays, MONITOR_ID, self.logger, confirmations=value)
        controller = AutomationController(self.displays, MONITOR_ID, self.logger, confirmations=3)
        controller.observe(PS5State.ON)
        controller.observe(PS5State.ON)
        self.displays.set_enabled.assert_not_called()
        controller.observe(PS5State.ON)
        self.displays.set_enabled.assert_called_once()

    def test_physical_control_requires_independent_explicit_authorization(self):
        with self.assertRaisesRegex(AutomationError, "autorização explícita"):
            AutomationController(self.displays, MONITOR_ID, self.logger, dry_run=False)
        controller = AutomationController(
            self.displays, MONITOR_ID, self.logger,
            dry_run=False, physical_control_authorized=True,
        )
        controller.observe(PS5State.ON)
        controller.observe(PS5State.ON)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=False)

    def test_rejects_ambiguous_id_and_non_boolean_options(self):
        for value in ("1", "Primary", "*", "SIM0002"):
            with self.subTest(value=value), self.assertRaises(DisplayError):
                AutomationController(self.displays, value, self.logger)
        for name in ("dry_run", "physical_control_authorized", "allow_off_recovery"):
            with self.subTest(name=name), self.assertRaises(AutomationError):
                AutomationController(self.displays, MONITOR_ID, self.logger, **{name: "false"})

    def test_monitor_and_confirmation_parameters_are_read_only(self):
        for name, value in (("monitor_id", "1"), ("confirmations", 1), ("dry_run", False)):
            with self.subTest(name=name), self.assertRaises(AttributeError):
                setattr(self.controller, name, value)

    def test_invalid_observation_breaks_pending_streak(self):
        self.controller.observe(PS5State.ON)
        with self.assertRaises(AutomationError):
            self.controller.observe("ON")
        self.controller.observe(PS5State.ON)
        self.displays.set_enabled.assert_not_called()

    def test_control_failure_suspends_without_retry_on_any_state(self):
        for error in (DisplayError("bloqueado"), DisplayVerificationError("incerto"), OSError("falha")):
            with self.subTest(error=error):
                displays = Mock(spec=DisplayController)
                displays.set_enabled.side_effect = error
                controller = AutomationController(displays, MONITOR_ID, self.logger)
                controller.observe(PS5State.ON)
                with self.assertRaises(AutomationControlError):
                    controller.observe(PS5State.ON)
                for state in (PS5State.ON, PS5State.REST, PS5State.REST, PS5State.UNKNOWN):
                    self.assertEqual(controller.observe(state).status, DecisionStatus.SUSPENDED)
                self.assertTrue(controller.suspended)
                self.assertIsNone(controller.last_requested_enabled)
                displays.set_enabled.assert_called_once()

    def test_control_interrupt_suspends_before_propagating(self):
        self.displays.set_enabled.side_effect = KeyboardInterrupt()
        self.controller.observe(PS5State.ON)
        with self.assertRaises(KeyboardInterrupt):
            self.controller.observe(PS5State.ON)
        self.assertTrue(self.controller.suspended)
        self.assertEqual(self.controller.observe(PS5State.REST).status, DecisionStatus.SUSPENDED)

    def test_stop_before_action_prevents_request(self):
        stop = Event()
        self.controller.observe(PS5State.ON)
        stop.set()
        decision = self.controller.observe(PS5State.ON, stop_event=stop)
        self.assertEqual(decision.status, DecisionStatus.STOPPED)
        self.displays.set_enabled.assert_not_called()
        stop.clear()
        self.assertEqual(self.controller.observe(PS5State.ON).status, DecisionStatus.PENDING)

    def test_stop_between_confirmation_and_request_is_checked_again(self):
        stop = Mock(spec=Event)
        self.controller.observe(PS5State.ON)
        stop.is_set.side_effect = [False, True]
        self.assertEqual(self.controller.observe(PS5State.ON, stop_event=stop).status, DecisionStatus.STOPPED)
        self.displays.set_enabled.assert_not_called()


class AutomationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.detector = Mock()
        self.displays = Mock(spec=DisplayController)
        self.logger = Mock(spec=logging.Logger)
        self.controller = AutomationController(self.displays, MONITOR_ID, self.logger)
        self.runner = AutomationRunner(self.detector, self.controller, 0.001, self.logger)

    def test_detector_interface_drives_actions_after_two_reads(self):
        self.detector.read_state.side_effect = [PS5State.ON, PS5State.ON, PS5State.REST, PS5State.REST]
        self.assertEqual(self.runner.run(max_polls=4), 4)
        self.assertEqual(self.detector.read_state.call_count, 4)
        self.assertEqual(self.displays.set_enabled.call_args_list, [
            call(MONITOR_ID, False, dry_run=True), call(MONITOR_ID, True, dry_run=True),
        ])
        self.assertTrue(self.runner.stop_event.is_set())

    def test_communication_error_or_invalid_state_is_unknown_not_enable(self):
        for error in (TimeoutError("timeout"), OSError("rede"), "bad state"):
            with self.subTest(error=error):
                detector = Mock()
                detector.read_state.side_effect = [PS5State.ON, PS5State.ON, error]
                displays = Mock(spec=DisplayController)
                controller = AutomationController(displays, MONITOR_ID, self.logger)
                runner = AutomationRunner(detector, controller, 0.001, self.logger)
                runner.run(max_polls=3)
                displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=True)

    def test_communication_error_breaks_confirmation_then_recovers_with_two_rest_reads(self):
        self.detector.read_state.side_effect = [PS5State.REST, OSError("rede"), PS5State.REST, PS5State.REST]
        self.runner.run(max_polls=4)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, True, dry_run=True)

    def test_repeated_failures_never_trigger_recovery(self):
        self.detector.read_state.side_effect = OSError("rede ausente")
        self.runner.run(max_polls=5)
        self.displays.set_enabled.assert_not_called()

    def test_control_failure_halts_run_without_next_read_or_compensation(self):
        self.detector.read_state.side_effect = [PS5State.ON, PS5State.ON, PS5State.REST]
        self.displays.set_enabled.side_effect = DisplayVerificationError("resultado incerto")
        with self.assertRaises(AutomationControlError):
            self.runner.run(max_polls=3)
        self.assertEqual(self.detector.read_state.call_count, 2)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=True)
        self.assertTrue(self.runner.stop_event.is_set())

    def test_unexpected_detector_error_halts_safely(self):
        self.detector.read_state.side_effect = RuntimeError("erro inesperado")
        with self.assertRaises(AutomationError):
            self.runner.run(max_polls=3)
        self.assertTrue(self.runner.stop_event.is_set())
        self.displays.set_enabled.assert_not_called()

    def test_stop_before_start_does_not_read(self):
        self.runner.stop()
        self.assertEqual(self.runner.run(max_polls=2), 0)
        self.detector.read_state.assert_not_called()
        self.displays.set_enabled.assert_not_called()

    def test_stop_during_read_prevents_the_confirmed_action(self):
        self.detector.read_state.return_value = PS5State.ON
        self.runner.poll_once()
        def read_and_stop():
            self.runner.stop()
            return PS5State.ON
        self.detector.read_state.side_effect = read_and_stop
        self.assertIsNone(self.runner.poll_once())
        self.displays.set_enabled.assert_not_called()

    def test_interrupt_propagates_and_never_reactivates_on_exit(self):
        self.detector.read_state.side_effect = [PS5State.ON, PS5State.ON, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.runner.run(max_polls=3)
        self.displays.set_enabled.assert_called_once_with(MONITOR_ID, False, dry_run=True)
        self.assertTrue(self.runner.stop_event.is_set())

    def test_wait_is_interruptible_and_has_no_final_wait(self):
        self.detector.read_state.return_value = PS5State.ON
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = False
        runner = AutomationRunner(self.detector, self.controller, 2.5, self.logger, stop_event=stop)
        self.assertEqual(runner.run(max_polls=2), 2)
        stop.wait.assert_called_once_with(2.5)
        stop.set.assert_called_once()

    def test_stop_signal_during_wait_prevents_next_read(self):
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = True
        self.detector.read_state.return_value = PS5State.ON
        runner = AutomationRunner(self.detector, self.controller, 1, self.logger, stop_event=stop)
        self.assertEqual(runner.run(), 1)
        self.displays.set_enabled.assert_not_called()

    def test_invalid_poll_limit_and_interval_are_rejected(self):
        for value in (0, -1, True, 1.5, "2"):
            with self.subTest(value=value), self.assertRaises(AutomationError):
                self.runner.run(max_polls=value)
        with self.assertRaises(ValueError):
            AutomationRunner(self.detector, self.controller, 0, self.logger)
        self.detector.read_state.assert_not_called()


class AutomationDisplayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.inventory = parse_inventory(INVENTORY.read_bytes())
        self.backend = FakeDisplayBackend(self.inventory)
        self.logger = Mock(spec=logging.Logger)

    def make_controller(self, monitor_id=MONITOR_ID, dry_run=True):
        return AutomationController(
            DisplayController(self.backend, self.logger), monitor_id, self.logger,
            dry_run=dry_run, physical_control_authorized=not dry_run,
        )

    def test_dry_run_with_real_controller_never_reaches_backend_executor(self):
        controller = self.make_controller()
        with patch('subprocess.Popen', side_effect=AssertionError('Hardware/processo externo')):
            for state in (PS5State.ON, PS5State.ON, PS5State.REST, PS5State.REST):
                controller.observe(state)
        self.assertEqual(self.backend.commands, [])
        self.assertEqual(self.backend.inventory, self.inventory)

    def test_authorized_path_changes_only_the_in_memory_selected_monitor(self):
        controller = self.make_controller(dry_run=False)
        with patch('subprocess.Popen', side_effect=AssertionError('Hardware/processo externo')):
            for state in (PS5State.ON, PS5State.ON, PS5State.REST, PS5State.REST):
                controller.observe(state)
        self.assertEqual(self.backend.commands, [(MONITOR_ID, False), (MONITOR_ID, True)])
        self.assertEqual(self.backend.inventory, self.inventory)

    def test_primary_last_active_and_duplicate_ids_keep_original_protections(self):
        cases = [
            (self.inventory, self.inventory[0].monitor_id),
            ((self.inventory[1], self.inventory[2]), MONITOR_ID),
            (self.inventory + (replace(self.inventory[1], monitor_id=MONITOR_ID.lower()),), MONITOR_ID),
        ]
        for inventory, monitor_id in cases:
            with self.subTest(monitor_id=monitor_id, count=len(inventory)):
                self.backend.inventory = inventory
                controller = self.make_controller(monitor_id, dry_run=False)
                controller.observe(PS5State.ON)
                with self.assertRaises(AutomationControlError):
                    controller.observe(PS5State.ON)
                self.assertTrue(controller.suspended)
        self.assertEqual(self.backend.commands, [])

    def test_mocked_ddp_to_memory_display_handles_timeout_between_transitions(self):
        target = ("192.168.1.10", 9302)
        sock = MagicMock(spec=socket.socket)
        sock.__enter__.return_value = sock
        sock.recvfrom.side_effect = [
            (DDP_ON, target), (DDP_ON, target), socket.timeout(),
            (DDP_REST, target), (DDP_REST, target),
        ]
        controller = self.make_controller(dry_run=False)
        detector = NetworkPS5Detector(target[0], NetworkConfig(retries=0), self.logger)
        runner = AutomationRunner(detector, controller, 0.001, self.logger, simulation_mode=False)
        with patch('socket.socket', return_value=sock):
            with patch('subprocess.Popen', side_effect=AssertionError('Hardware físico')):
                self.assertEqual(runner.run(max_polls=5), 5)
        self.assertEqual(self.backend.commands, [(MONITOR_ID, False), (MONITOR_ID, True)])
        self.assertEqual(sock.sendto.call_args_list, [call(SEARCH_PACKET, target)] * 5)
