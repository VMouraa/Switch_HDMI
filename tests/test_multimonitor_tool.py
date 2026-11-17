import codecs
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from switch_hdmi.displays import DisplayError
from switch_hdmi.multimonitor_tool import CSVInventoryBackend, MultiMonitorToolBackend, parse_inventory


FIXTURE = Path(__file__).parent / "fixtures" / "monitors.csv"


class InventoryParsingTests(unittest.TestCase):
    def test_reads_identifiers_serials_flags_and_layout(self):
        monitors = parse_inventory(FIXTURE.read_bytes())
        self.assertEqual(len(monitors), 3)
        self.assertTrue(monitors[0].primary)
        self.assertTrue(monitors[1].active)
        self.assertTrue(monitors[2].connected)
        self.assertFalse(monitors[2].active)
        self.assertEqual(monitors[1].serial_number, "SIM-002")
        self.assertEqual(monitors[1].position, "1920, 0")
        self.assertTrue(monitors[1].monitor_id.startswith("MONITOR\\SIM0002\\"))

    def test_supports_utf8_bom_and_utf16_both_byte_orders(self):
        text = FIXTURE.read_text(encoding="utf-8").replace("Secundario", "Secundário")
        for encoded in (text.encode("utf-8-sig"), text.encode("utf-16"),
                        codecs.BOM_UTF16_BE + text.encode("utf-16-be")):
            with self.subTest(encoded=encoded[:10]):
                self.assertIn("Secundário", parse_inventory(encoded)[1].friendly_name)

    def test_empty_translated_missing_or_duplicate_headers_are_rejected(self):
        data = FIXTURE.read_bytes()
        for bad in (b"", b"Primary,Primary\nYes,Yes", data.replace(b"Active,", b"Ativo,", 1),
                    data.replace(b"Monitor ID,", b"", 1)):
            with self.subTest(bad=bad[:50]), self.assertRaises(DisplayError):
                parse_inventory(bad)

    def test_unknown_boolean_is_not_treated_as_inactive(self):
        data = FIXTURE.read_bytes()
        for bad in (data.replace(b",Yes,Yes,No,", b",Maybe,Yes,No,", 1),
                    data.replace(b",Yes,Yes,No,", b",,Yes,No,", 1)):
            with self.subTest(bad=bad[:50]), self.assertRaises(DisplayError):
                parse_inventory(bad)

    def test_malformed_or_non_unicode_csv_is_rejected(self):
        for bad in (b"\xff", FIXTURE.read_bytes() + b"too,few,columns\n",
                    FIXTURE.read_bytes().replace(b"SIM-001,Yes", b"SIM-001,extra,Yes", 1)):
            with self.subTest(bad=bad[:50]), self.assertRaises(DisplayError):
                parse_inventory(bad)

    def test_offline_inventory_cannot_execute_even_if_called_directly(self):
        backend = CSVInventoryBackend(FIXTURE)
        self.assertEqual(len(backend.list_monitors()), 3)
        with self.assertRaisesRegex(DisplayError, "offline"):
            backend.set_enabled(backend.list_monitors()[1].monitor_id, False)

    def test_missing_offline_file_has_clear_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(DisplayError, "ler o CSV"):
                CSVInventoryBackend(Path(folder) / "missing.csv").list_monitors()


class MultiMonitorToolBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "MultiMonitorTool.exe"
        # Apenas simula a existência do arquivo; nenhum executável é gerado.
        with patch.object(Path, "is_file", return_value=True):
            with patch("switch_hdmi.multimonitor_tool.sys.platform", "win32"):
                self.backend = MultiMonitorToolBackend(self.path)
        self.monitors = parse_inventory(FIXTURE.read_bytes())

    def test_requires_explicit_existing_absolute_tool_path(self):
        for path in ("MultiMonitorTool.exe", self.path, self.path.with_name("other.exe")):
            with self.subTest(path=path), self.assertRaises(DisplayError):
                MultiMonitorToolBackend(path)

    def test_rejects_non_windows_platform(self):
        with patch.object(Path, "is_file", return_value=True):
            with patch("switch_hdmi.multimonitor_tool.sys.platform", "linux"):
                with self.assertRaisesRegex(DisplayError, "Windows"):
                    MultiMonitorToolBackend(self.path)

    def test_validates_timeout(self):
        for value in (0, -1, True, "10", 61, float("nan"), float("inf")):
            with self.subTest(value=value), patch.object(Path, "is_file", return_value=True):
                with self.assertRaisesRegex(DisplayError, "Timeout"):
                    MultiMonitorToolBackend(self.path, timeout_seconds=value)

    def test_export_only_uses_read_only_options_and_a_fresh_temporary_file(self):
        def fake_run(arguments, **kwargs):
            self.assertEqual(arguments[1:4], ["/HideInactiveMonitors", "0", "/scomma"])
            report = Path(arguments[4])
            self.assertFalse(report.exists())
            report.write_bytes(FIXTURE.read_bytes())
            return Mock(returncode=0)
        with patch("switch_hdmi.multimonitor_tool.subprocess.run", side_effect=fake_run) as run:
            self.assertEqual(self.backend.list_monitors(), self.monitors)
        self.assertFalse(Path(run.call_args.args[0][-1]).exists())
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["capture_output"], True)

    def test_enable_disable_only_pass_exactly_one_full_id_without_shell(self):
        for enabled, command in ((True, "/enable"), (False, "/disable")):
            with self.subTest(enabled=enabled):
                with patch("switch_hdmi.multimonitor_tool.subprocess.run", return_value=Mock(returncode=0)) as run:
                    self.backend.set_enabled(self.monitors[1].monitor_id, enabled)
                self.assertEqual(run.call_args.args[0], [str(self.path), command, self.monitors[1].monitor_id])
                self.assertFalse(run.call_args.kwargs["shell"])

    def test_alias_or_invalid_enabled_flag_never_runs_a_process(self):
        with patch("switch_hdmi.multimonitor_tool.subprocess.run") as run:
            for monitor_id, enabled in (("All", False), ("1", False), (self.monitors[1].monitor_id, "false")):
                with self.subTest(monitor_id=monitor_id), self.assertRaises(DisplayError):
                    self.backend.set_enabled(monitor_id, enabled)
            run.assert_not_called()

    def test_exit_failure_launch_error_and_timeout_are_reported_without_retry(self):
        for failure in (OSError("falha"), subprocess.TimeoutExpired("MultiMonitorTool", 10)):
            with self.subTest(failure=failure):
                with patch("switch_hdmi.multimonitor_tool.subprocess.run", side_effect=failure) as run:
                    with self.assertRaises(DisplayError):
                        self.backend.set_enabled(self.monitors[1].monitor_id, False)
                    run.assert_called_once()
        with patch("switch_hdmi.multimonitor_tool.subprocess.run", return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(DisplayError, "código 1"):
                self.backend.set_enabled(self.monitors[1].monitor_id, False)

    def test_missing_export_is_not_replaced_with_cached_inventory(self):
        with patch("switch_hdmi.multimonitor_tool.subprocess.run", return_value=Mock(returncode=0)):
            with self.assertRaisesRegex(DisplayError, "inventário"):
                self.backend.list_monitors()
