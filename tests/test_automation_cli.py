from contextlib import redirect_stderr
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from switch_hdmi.automation import AutomationControlError, AutomationRunner
from switch_hdmi.automation_control import main
from switch_hdmi.detector import PS5State
from switch_hdmi.displays import DisplayController, DisplayError
from switch_hdmi.multimonitor_tool import parse_inventory
from tests.samples import MODERN_CONFIG


FIXTURE = Path(__file__).parent / "fixtures" / "monitors.csv"
MONITOR_ID = parse_inventory(FIXTURE.read_bytes())[1].monitor_id


class AutomationCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "config.json"
        self.log_path = self.config_path.parent / "logs" / "ps5_detector.log"
        self.config = copy.deepcopy(MODERN_CONFIG)
        self.config["simulator"]["states"] = ["ON", "ON", "REST", "REST"]
        self.write_config()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def invoke(self, *extra):
        with redirect_stderr(io.StringIO()):
            return main([
                "--config", str(self.config_path), "--monitor-id", MONITOR_ID,
                "--inventory-csv", str(FIXTURE), "--max-polls", "4", *extra,
            ])

    def test_simulated_end_to_end_runs_without_network_or_external_process(self):
        with patch('socket.socket', side_effect=AssertionError('Rede física')):
            with patch('subprocess.Popen', side_effect=AssertionError('Processo de hardware')):
                self.assertEqual(self.invoke(), 0)
        log = self.log_path.read_text(encoding="utf-8")
        self.assertIn("DRY_RUN", log)
        self.assertIn("ON confirmado", log)
        self.assertIn("REST confirmado", log)
        self.assertIn("dry_run=True", log)
        self.assertNotIn("EXECUTE:", log)
        self.log_path.rename(self.log_path.with_suffix(".closed"))

    def test_execute_with_simulator_is_refused_before_tool_or_detector(self):
        with patch('switch_hdmi.automation_control.MultiMonitorToolBackend') as backend:
            with patch('switch_hdmi.automation_control.create_detector') as detector:
                with redirect_stderr(io.StringIO()):
                    result = main([
                        "--config", str(self.config_path), "--monitor-id", MONITOR_ID,
                        "--tool", "untrusted.exe", "--execute", "--max-polls", "2",
                    ])
        self.assertEqual(result, 2)
        backend.assert_not_called()
        detector.assert_not_called()
        self.assertFalse(self.log_path.exists())

    def test_execute_with_offline_inventory_is_refused_even_for_real_detector(self):
        self.config.update(simulation_mode=False, ps5_ip="192.168.1.10")
        self.write_config()
        with patch('socket.socket', side_effect=AssertionError('Rede física')):
            self.assertEqual(self.invoke("--execute"), 2)
        self.assertFalse(self.log_path.exists())

    def test_real_mode_is_still_dry_run_without_explicit_execute(self):
        self.config.update(simulation_mode=False, ps5_ip="192.168.1.10")
        self.write_config()
        detector = Mock()
        detector.read_state.side_effect = [PS5State.ON, PS5State.ON, PS5State.REST, PS5State.REST]
        with patch('switch_hdmi.automation_control.create_detector', return_value=detector):
            with patch.object(DisplayController, 'set_enabled') as set_enabled:
                self.assertEqual(self.invoke(), 0)
        self.assertEqual([entry.kwargs['dry_run'] for entry in set_enabled.call_args_list], [True, True])

    def test_explicit_authorization_is_forwarded_with_mocked_live_dependencies(self):
        self.config.update(simulation_mode=False, ps5_ip="192.168.1.10")
        self.write_config()
        detector = Mock()
        detector.read_state.side_effect = [PS5State.ON, PS5State.ON]
        with patch('switch_hdmi.automation_control.create_detector', return_value=detector):
            with patch('switch_hdmi.automation_control.MultiMonitorToolBackend'):
                with patch.object(DisplayController, 'set_enabled') as request:
                    with patch('subprocess.Popen', side_effect=AssertionError('Hardware físico')):
                        with redirect_stderr(io.StringIO()):
                            result = main([
                                "--config", str(self.config_path), "--monitor-id", MONITOR_ID,
                                "--tool", "mock.exe", "--execute", "--max-polls", "2",
                            ])
        self.assertEqual(result, 0)
        request.assert_called_once_with(MONITOR_ID, False, dry_run=False)

    def test_keyboard_interrupt_closes_log_and_returns_130(self):
        with patch.object(AutomationRunner, 'run', side_effect=KeyboardInterrupt()):
            self.assertEqual(self.invoke(), 130)
        self.assertIn("interrompida", self.log_path.read_text(encoding="utf-8"))
        self.log_path.rename(self.log_path.with_suffix(".closed"))

    def test_control_failure_returns_2_and_is_logged(self):
        with patch.object(AutomationRunner, 'run', side_effect=AutomationControlError('resultado incerto')):
            self.assertEqual(self.invoke(), 2)
        self.assertIn("resultado incerto", self.log_path.read_text(encoding="utf-8"))

    def test_incomplete_config_returns_error_without_monitor_calls(self):
        self.config_path.write_text('{}', encoding='utf-8')
        with patch.object(DisplayController, 'set_enabled') as request:
            self.assertEqual(self.invoke(), 2)
        request.assert_not_called()

    def test_legacy_config_remains_supported(self):
        self.config_path.write_text(json.dumps({
            'mode': 'simulator', 'poll_interval_seconds': 0.001,
            'simulator': {'states': ['ON', 'ON', 'REST', 'REST'], 'repeat': False},
            'logging': {'file': 'logs/ps5_detector.log'},
        }), encoding='utf-8')
        self.assertEqual(self.invoke(), 0)

    def test_off_policy_is_opt_in_and_never_applies_to_unknown(self):
        self.config['simulator']['states'] = ['OFF', 'OFF']
        self.write_config()
        with patch.object(DisplayController, 'set_enabled') as request:
            self.assertEqual(self.invoke('--max-polls', '2'), 0)
            request.assert_not_called()
            self.assertEqual(self.invoke('--max-polls', '2', '--allow-off-recovery'), 0)
            request.assert_called_once_with(MONITOR_ID, True, dry_run=True)

    def test_argument_validation_rejects_less_than_two_reads_and_bad_id(self):
        for arguments in (('--confirmations', '1'), ('--confirmations', 'bad'),
                          ('--max-polls', '0'), ('--monitor-id', '1')):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                self.invoke(*arguments)
            self.assertEqual(error.exception.code, 2)

    def test_simulation_example_uses_existing_schema_and_two_read_transitions(self):
        from switch_hdmi.config import load_config
        path = Path(__file__).resolve().parents[1] / 'examples' / 'automation-simulation.json'
        config = load_config(path)
        self.assertTrue(config.simulation_mode)
        self.assertIsNone(config.ps5_ip)
        self.assertEqual(config.states.count(PS5State.ON), 3)
        self.assertEqual(config.states.count(PS5State.REST), 3)
