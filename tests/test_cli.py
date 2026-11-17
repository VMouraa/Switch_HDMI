from contextlib import redirect_stderr
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from switch_hdmi.__main__ import main
from tests.test_config import EXAMPLE_CONFIG
from tests.samples import DDP_ON, MODERN_CONFIG


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "config.json"
        self.log_path = self.config_path.parent / "logs" / "ps5_detector.log"
        self.config_path.write_text(json.dumps(EXAMPLE_CONFIG), encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "switch_hdmi", "--config", str(self.config_path), *args],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def test_complete_sequence_is_written_to_file_and_console(self):
        result = self.run_cli("--max-polls", "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log_path.read_text(encoding="utf-8")
        for output in (log, result.stderr):
            self.assertIn("Estado inicial do PS5 (simulado): OFF", output)
            self.assertIn("OFF -> ON", output)
            self.assertIn("ON -> REST", output)
            self.assertIn("REST -> ON", output)
            self.assertIn("ON -> OFF", output)
            self.assertEqual(output.count("Mudança de estado"), 4)
            self.assertIn("encerrado após 7 consulta(s)", output)
        self.assertRegex(log, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \| INFO \|")

    def test_once_exits_even_when_sequence_repeats(self):
        data = copy.deepcopy(EXAMPLE_CONFIG)
        data["simulator"]["repeat"] = True
        self.config_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("--once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("encerrado após 1 consulta(s)", result.stderr)
        self.assertNotIn("Mudança de estado", result.stderr)

    def test_invalid_config_exits_without_creating_logs(self):
        self.config_path.write_text('{"mode": "network"}', encoding="utf-8")
        result = self.run_cli("--once")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Erro:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.log_path.parent.exists())

    def test_invalid_cli_limits(self):
        for args in (("--max-polls", "0"), ("--max-polls", "abc"), ("--once", "--max-polls", "2")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log_path.exists())

    def test_ctrl_c_is_handled_and_log_file_is_closed(self):
        with patch("switch_hdmi.__main__.StateMonitor.run", side_effect=KeyboardInterrupt):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(self.config_path)]), 0)
        self.assertIn("encerrado pelo usuário", self.log_path.read_text(encoding="utf-8"))
        # No Windows, renomear comprova que o FileHandler liberou o arquivo.
        self.log_path.rename(self.log_path.with_suffix(".closed"))

    def test_log_destination_error_is_reported_cleanly(self):
        self.log_path.mkdir(parents=True)
        result = self.run_cli("--once")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Erro:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_simulation_never_accesses_network_or_launches_processes(self):
        with patch("socket.socket", side_effect=AssertionError("Acesso à rede")):
            with patch("subprocess.Popen", side_effect=AssertionError("Processo externo")):
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--config", str(self.config_path), "--max-polls", "5"]), 0)
        self.assertIn("ON -> OFF", self.log_path.read_text(encoding="utf-8"))

    def test_new_simulation_config_never_accesses_network(self):
        self.config_path.write_text(json.dumps(MODERN_CONFIG), encoding="utf-8")
        with patch("socket.socket", side_effect=AssertionError("Acesso à rede")):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(self.config_path), "--max-polls", "5"]), 0)
        self.assertIn("ON -> OFF", self.log_path.read_text(encoding="utf-8"))

    def test_real_mode_with_mocked_network_logs_state(self):
        data = copy.deepcopy(MODERN_CONFIG)
        data.update(simulation_mode=False, ps5_ip="192.168.1.10")
        self.config_path.write_text(json.dumps(data), encoding="utf-8")
        sock = MagicMock(spec=socket.socket)
        sock.__enter__.return_value = sock
        sock.recvfrom.return_value = (DDP_ON, (data["ps5_ip"], 9302))
        with patch("socket.socket", return_value=sock):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(self.config_path), "--once"]), 0)
        log = self.log_path.read_text(encoding="utf-8")
        self.assertIn("modo network/DDP", log)
        self.assertIn("Estado inicial do PS5 (rede/DDP): ON", log)
        self.assertNotIn("simulado", log)

    def test_real_mode_network_error_is_unknown_and_monitor_keeps_running(self):
        data = copy.deepcopy(MODERN_CONFIG)
        data.update(simulation_mode=False, ps5_ip="192.168.1.10")
        self.config_path.write_text(json.dumps(data), encoding="utf-8")
        with patch("socket.socket", side_effect=OSError("Rede indisponível")):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(self.config_path), "--max-polls", "2"]), 0)
        log = self.log_path.read_text(encoding="utf-8")
        self.assertIn("Estado inicial do PS5 (rede/DDP): UNKNOWN", log)
        self.assertIn("encerrado após 2 consulta(s)", log)
        self.assertNotIn("estado=OFF", log)

    def test_real_mode_missing_ip_is_configuration_error(self):
        data = copy.deepcopy(MODERN_CONFIG)
        data["simulation_mode"] = False
        self.config_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("--once")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ps5_ip", result.stderr)
        self.assertFalse(self.log_path.exists())
