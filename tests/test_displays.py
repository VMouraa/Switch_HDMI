from dataclasses import replace
import logging
from pathlib import Path
import unittest
from unittest.mock import Mock

from switch_hdmi.displays import DisplayController, DisplayError, DisplayVerificationError
from switch_hdmi.multimonitor_tool import parse_inventory


FIXTURE = Path(__file__).parent / "fixtures" / "monitors.csv"


class FakeDisplayBackend:
    def __init__(self, inventory):
        self.inventory = inventory
        self.commands = []
        self.reads = 0

    def list_monitors(self):
        self.reads += 1
        return self.inventory

    def set_enabled(self, monitor_id, enabled):
        self.commands.append((monitor_id, enabled))
        self.inventory = tuple(replace(item, active=enabled)
                               if item.monitor_id == monitor_id else item
                               for item in self.inventory)


class DisplayControllerTests(unittest.TestCase):
    def setUp(self):
        self.inventory = parse_inventory(FIXTURE.read_bytes())
        self.primary, self.secondary, self.inactive = self.inventory
        self.backend = FakeDisplayBackend(self.inventory)
        self.logger = Mock(spec=logging.Logger)
        self.controller = DisplayController(self.backend, self.logger)

    def test_list_is_read_only_and_includes_connected_inactive_monitor(self):
        self.assertEqual(self.controller.list_monitors(), self.inventory)
        self.assertFalse(self.inactive.active)
        self.assertTrue(self.inactive.connected)
        self.assertEqual(self.backend.commands, [])

    def test_disable_defaults_to_dry_run_and_logs_without_changes(self):
        result = self.controller.disable(self.secondary.monitor_id)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.changed)
        self.assertFalse(result.verified)
        self.assertEqual(self.backend.commands, [])
        self.assertEqual(self.backend.inventory, self.inventory)
        self.assertIn("DRY_RUN", self.logger.info.call_args.args[0])

    def test_enable_defaults_to_dry_run(self):
        result = self.controller.enable(self.inactive.monitor_id)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.backend.commands, [])

    def test_explicit_execution_only_disables_selected_id_and_verifies_others(self):
        result = self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertTrue(result.changed)
        self.assertTrue(result.verified)
        self.assertFalse(result.dry_run)
        self.assertEqual(self.backend.commands, [(self.secondary.monitor_id, False)])
        self.assertEqual(self.backend.inventory[0], self.primary)
        self.assertEqual(self.backend.inventory[2], self.inactive)
        self.assertFalse(self.backend.inventory[1].active)
        self.assertEqual(self.backend.reads, 3)

    def test_explicit_execution_only_enables_selected_id(self):
        result = self.controller.enable(self.inactive.monitor_id, dry_run=False)
        self.assertTrue(result.verified)
        self.assertEqual(self.backend.commands, [(self.inactive.monitor_id, True)])
        self.assertEqual(self.backend.inventory[:2], self.inventory[:2])

    def test_complete_id_is_case_insensitive_and_does_not_use_display_number(self):
        renumbered = replace(self.secondary, device_name=r"\\.\DISPLAY99")
        self.backend.inventory = (self.primary, renumbered, self.inactive)
        result = self.controller.disable(renumbered.monitor_id.lower(), dry_run=False)
        self.assertEqual(result.monitor_id, self.secondary.monitor_id)
        self.assertEqual(self.backend.commands, [(self.secondary.monitor_id, False)])

    def test_already_enabled_or_disabled_is_noop(self):
        for method, target in ((self.controller.enable, self.secondary),
                               (self.controller.disable, self.inactive)):
            for dry_run in (True, False):
                with self.subTest(method=method.__name__, dry_run=dry_run):
                    result = method(target.monitor_id, dry_run=dry_run)
                    self.assertFalse(result.changed)
                    self.assertFalse(result.verified)
        self.assertEqual(self.backend.commands, [])

    def test_cannot_disable_last_active_even_with_explicit_execution(self):
        for primary in (True, False):
            self.backend.inventory = (replace(self.secondary, primary=primary), self.inactive)
            for dry_run in (True, False):
                with self.subTest(primary=primary, dry_run=dry_run):
                    with self.assertRaisesRegex(DisplayError, "último monitor ativo"):
                        self.controller.disable(self.secondary.monitor_id, dry_run=dry_run)
        self.assertEqual(self.backend.commands, [])

    def test_primary_is_protected_for_both_operations(self):
        for enable in (True, False):
            for dry_run in (True, False):
                with self.subTest(enable=enable, dry_run=dry_run):
                    with self.assertRaisesRegex(DisplayError, "monitor principal"):
                        self.controller.set_enabled(self.primary.monitor_id, enable, dry_run=dry_run)
        self.assertEqual(self.backend.commands, [])

    def test_rejects_aliases_partial_ids_wildcards_and_command_injection(self):
        for value in ("1", "Primary", "All", "*", "SIM0002", r"\\.\DISPLAY2",
                      self.secondary.monitor_id + " /disable 1", "", None):
            with self.subTest(value=value), self.assertRaises(DisplayError):
                self.controller.disable(value, dry_run=False)
        self.assertEqual(self.backend.reads, 0)
        self.assertEqual(self.backend.commands, [])

    def test_rejects_unknown_id(self):
        unknown = self.secondary.monitor_id.replace("SIM0002", "SIM9999")
        with self.assertRaisesRegex(DisplayError, "não encontrado"):
            self.controller.disable(unknown, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_rejects_duplicate_ids_even_with_different_case(self):
        self.backend.inventory += (replace(self.secondary, monitor_id=self.secondary.monitor_id.lower()),)
        with self.assertRaisesRegex(DisplayError, "duplicado"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_rejects_missing_id_on_other_connected_monitor(self):
        self.backend.inventory = (self.primary, self.secondary, replace(self.inactive, monitor_id=None))
        with self.assertRaisesRegex(DisplayError, "identificação"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_disconnected_selection_is_blocked(self):
        self.backend.inventory = (self.primary, self.secondary, replace(self.inactive, connected=False))
        with self.assertRaisesRegex(DisplayError, "desconectado"):
            self.controller.enable(self.inactive.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_missing_or_multiple_primary_is_blocked(self):
        for inventory in (
            (replace(self.primary, primary=False), self.secondary),
            (self.primary, self.secondary, replace(self.inactive, active=True, primary=True)),
        ):
            self.backend.inventory = inventory
            with self.subTest(inventory=inventory), self.assertRaisesRegex(DisplayError, "exatamente um"):
                self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_shared_source_is_blocked(self):
        self.backend.inventory = (self.primary, self.secondary,
                                  replace(self.inactive, device_name=self.secondary.device_name))
        with self.assertRaisesRegex(DisplayError, "compartilhada"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_inconsistent_or_non_boolean_inventory_is_blocked(self):
        for bad in (replace(self.inactive, active=True, connected=False),
                    replace(self.inactive, primary=True), replace(self.inactive, active="No")):
            self.backend.inventory = (self.primary, self.secondary, bad)
            with self.subTest(bad=bad), self.assertRaises(DisplayError):
                self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_non_boolean_execution_flags_are_rejected(self):
        for value in ("false", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaises(DisplayError):
                    self.controller.disable(self.secondary.monitor_id, dry_run=value)
                with self.assertRaises(DisplayError):
                    self.controller.set_enabled(self.secondary.monitor_id, value)
        self.assertEqual(self.backend.commands, [])

    def test_missing_layout_of_other_active_monitor_blocks_action(self):
        self.backend.inventory = (replace(self.primary, position=""), self.secondary, self.inactive)
        with self.assertRaisesRegex(DisplayError, "layout"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_hotplug_before_execution_aborts_without_command(self):
        self.backend.list_monitors = Mock(side_effect=[self.inventory, (self.primary, self.secondary)])
        with self.assertRaisesRegex(DisplayError, "Topologia mudou"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_primary_or_last_active_change_during_revalidation_aborts(self):
        changed = (replace(self.primary, active=False, primary=False),
                   replace(self.secondary, primary=True), self.inactive)
        self.backend.list_monitors = Mock(side_effect=[self.inventory, changed])
        with self.assertRaisesRegex(DisplayError, "último monitor ativo"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])

    def test_inventory_reordering_is_not_a_topology_change(self):
        after = tuple(replace(item, active=False) if item is self.secondary else item for item in self.inventory)
        self.backend.list_monitors = Mock(side_effect=[self.inventory, self.inventory[::-1], after[::-1]])
        self.assertTrue(self.controller.disable(self.secondary.monitor_id, dry_run=False).verified)

    def test_failure_after_command_never_retries_or_rolls_back(self):
        self.backend.set_enabled = Mock(side_effect=DisplayError("timeout"))
        with self.assertRaisesRegex(DisplayVerificationError, "pode ter sido aplicada"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.backend.set_enabled.assert_called_once_with(self.secondary.monitor_id, False)
        self.assertTrue(self.logger.error.called)

    def test_success_exit_without_state_change_is_not_success(self):
        self.backend.set_enabled = Mock()
        with self.assertRaisesRegex(DisplayVerificationError, "não atingiu"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)

    def test_interrupted_execution_is_uncertain_and_never_retried(self):
        self.backend.set_enabled = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaisesRegex(DisplayVerificationError, "pode ter sido aplicada"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.backend.set_enabled.assert_called_once()

    def test_verification_read_error_is_not_reported_as_success(self):
        self.backend.list_monitors = Mock(side_effect=[self.inventory, self.inventory, DisplayError("erro de leitura")])
        self.backend.set_enabled = Mock()
        with self.assertRaisesRegex(DisplayVerificationError, "pode ter sido aplicada"):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.backend.set_enabled.assert_called_once()

    def test_unexpected_other_monitor_or_primary_change_is_reported(self):
        for changed in (replace(self.primary, position="100, 0"),
                        replace(self.primary, frequency="75")):
            after = (changed, replace(self.secondary, active=False), self.inactive)
            self.backend.list_monitors = Mock(side_effect=[self.inventory, self.inventory, after])
            self.backend.set_enabled = Mock()
            with self.subTest(changed=changed), self.assertRaisesRegex(DisplayVerificationError, "Outro monitor"):
                self.controller.disable(self.secondary.monitor_id, dry_run=False)
            self.backend.set_enabled.assert_called_once()

    def test_inventory_error_before_command_never_executes(self):
        self.backend.list_monitors = Mock(side_effect=DisplayError("inventário indisponível"))
        with self.assertRaises(DisplayError):
            self.controller.disable(self.secondary.monitor_id, dry_run=False)
        self.assertEqual(self.backend.commands, [])
