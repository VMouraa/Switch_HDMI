from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from switch_hdmi.display_control import main
from switch_hdmi.multimonitor_tool import parse_inventory


FIXTURE = Path(__file__).parent / "fixtures" / "monitors.csv"


class DisplayCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "monitor_control.log"
        self.args = ["--inventory-csv", str(FIXTURE), "--log-file", str(self.log)]
        self.monitors = parse_inventory(FIXTURE.read_bytes())
        self.output = io.StringIO()
        self.errors = io.StringIO()

    def invoke(self, *arguments):
        with redirect_stdout(self.output), redirect_stderr(self.errors):
            return main([*self.args, *arguments])

    def test_offline_list_is_json_with_full_ids_and_no_external_calls(self):
        with patch("subprocess.run", side_effect=AssertionError("Processo externo")):
            with patch("socket.socket", side_effect=AssertionError("Rede")):
                self.assertEqual(self.invoke("list"), 0)
        rows = json.loads(self.output.getvalue())
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["monitor_id"], self.monitors[1].monitor_id)
        self.assertIn("origem=offline", self.log.read_text(encoding="utf-8"))

    def test_default_dry_run_logs_planned_action_and_closes_log(self):
        with patch("subprocess.run", side_effect=AssertionError("Processo externo")):
            self.assertEqual(self.invoke("disable", "--monitor-id", self.monitors[1].monitor_id), 0)
        result = json.loads(self.output.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["changed"])
        self.assertIn("DRY_RUN", self.log.read_text(encoding="utf-8"))
        self.log.rename(self.log.with_suffix(".closed"))

    def test_offline_execute_is_blocked_before_any_process_or_log(self):
        with patch("subprocess.run", side_effect=AssertionError("Processo externo")):
            self.assertEqual(self.invoke("--execute", "disable", "--monitor-id", self.monitors[1].monitor_id), 2)
        self.assertIn("offline", self.errors.getvalue())
        self.assertFalse(self.log.exists())

    def test_primary_and_numeric_selection_are_blocked_with_clear_errors(self):
        for monitor_id in (self.monitors[0].monitor_id, "2"):
            with self.subTest(monitor_id=monitor_id):
                self.assertEqual(self.invoke("disable", "--monitor-id", monitor_id), 2)
        self.assertNotIn("Traceback", self.errors.getvalue())

    def test_missing_inventory_is_an_error_without_traceback(self):
        self.args[1] = str(Path(self.temp.name) / "missing.csv")
        self.assertEqual(self.invoke("list"), 2)
        self.assertIn("Erro:", self.errors.getvalue())

    def test_explicit_dry_run_is_supported(self):
        self.assertEqual(self.invoke("--dry-run", "enable", "--monitor-id", self.monitors[2].monitor_id), 0)
        self.assertTrue(json.loads(self.output.getvalue())["dry_run"])

    def test_log_creation_failure_is_handled(self):
        self.log.mkdir()
        self.assertEqual(self.invoke("list"), 2)
        self.assertIn("Erro:", self.errors.getvalue())

    def test_interrupted_inventory_closes_log_without_changing_monitors(self):
        with patch("switch_hdmi.multimonitor_tool.CSVInventoryBackend.list_monitors", side_effect=KeyboardInterrupt()):
            self.assertEqual(self.invoke("list"), 130)
        self.assertIn("interrompida", self.log.read_text(encoding="utf-8"))
        self.log.rename(self.log.with_suffix(".closed"))
