import logging
import socket
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from switch_hdmi.config import NetworkConfig
from switch_hdmi.detector import PS5State
from switch_hdmi.monitor import StateMonitor
from switch_hdmi.network_detector import (
    MAX_RESPONSE_BYTES, SEARCH_PACKET, NetworkPS5Detector, parse_ddp_response,
)
from tests.samples import DDP_ON, DDP_REST


TARGET = ("192.168.1.10", 9302)


class DDPParsingTests(unittest.TestCase):
    def test_status_codes_distinguish_on_and_rest(self):
        self.assertEqual(parse_ddp_response(DDP_ON), PS5State.ON)
        self.assertEqual(parse_ddp_response(DDP_REST), PS5State.REST)

    def test_supports_crlf_trailing_blank_line_and_optional_null(self):
        for ending in (b"", b"\0", b"\n", b"\n\0"):
            with self.subTest(ending=ending):
                payload = (DDP_REST + ending).replace(b"\n", b"\r\n")
                self.assertEqual(parse_ddp_response(payload), PS5State.REST)

    def test_header_names_are_case_insensitive(self):
        payload = DDP_ON.replace(b"device-discovery-protocol-version:", b"Device-Discovery-Protocol-Version: ")
        self.assertEqual(parse_ddp_response(payload), PS5State.ON)

    def test_protocol_version_alone_can_identify_ps5(self):
        self.assertEqual(parse_ddp_response(DDP_ON.replace(b"host-type:PS5\n", b"")), PS5State.ON)

    def test_unknown_status_never_means_off(self):
        for status in (b"404 Not Found", b"500 Error", b"503 Off", b"000 Off", b"621 Off"):
            with self.subTest(status=status):
                self.assertEqual(parse_ddp_response(DDP_ON.replace(b"200 Ok", status)), PS5State.UNKNOWN)

    def test_rejects_non_ps5_or_missing_version(self):
        for payload in (
            DDP_ON.replace(b"00030010", b"00020020"),
            DDP_ON.replace(b"device-discovery-protocol-version:00030010\n", b""),
            DDP_ON.replace(b"host-type:PS5", b"host-type:PS4"),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(parse_ddp_response(payload), PS5State.UNKNOWN)

    def test_rejects_invalid_control_characters_in_status_or_headers(self):
        for character in (b"\x01", b"\x07", b"\x1b", b"\x7f"):
            for field in (b"200 Ok", b"host-name:Console de teste"):
                with self.subTest(character=character, field=field):
                    payload = DDP_ON.replace(field, field + character)
                    self.assertEqual(parse_ddp_response(payload), PS5State.UNKNOWN)

    def test_rejects_malformed_ambiguous_and_oversized_data(self):
        for payload in (
            b"", b"\xff", b"200 ON", SEARCH_PACKET,
            DDP_ON.replace(b"HTTP/1.1", b"HTTP/2.0"),
            DDP_ON.replace(b"host-type:PS5", b"not a header"),
            DDP_ON + b"host-type:PS4\n",
            DDP_ON + b"DEVICE-DISCOVERY-PROTOCOL-VERSION:00030010\n",
            DDP_ON.replace(b"host-id:", b"host\0-id:"),
            DDP_ON.replace(b"host-id:", b"host\r-id:"),
            DDP_ON + b"\nbody",
            DDP_ON + b"x" * MAX_RESPONSE_BYTES,
        ):
            with self.subTest(payload=payload[:100]):
                self.assertEqual(parse_ddp_response(payload), PS5State.UNKNOWN)


class NetworkDetectorTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock(spec=logging.Logger)
        self.socket_class = socket.socket
        self.sock = MagicMock(spec=self.socket_class)
        self.sock.__enter__.return_value = self.sock
        socket_patch = patch(
            "switch_hdmi.network_detector.socket.socket", return_value=self.sock
        )
        self.socket_factory = socket_patch.start()
        self.addCleanup(socket_patch.stop)
        clock_patch = patch("switch_hdmi.network_detector.time.monotonic", return_value=0)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.detector = NetworkPS5Detector(TARGET[0], NetworkConfig(retries=0), self.logger)

    def test_only_sends_search_to_configured_target(self):
        self.sock.recvfrom.return_value = (DDP_ON, TARGET)
        self.assertEqual(self.detector.read_state(), PS5State.ON)
        self.socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind.assert_called_once_with(("0.0.0.0", 9303))
        self.sock.sendto.assert_called_once_with(
            b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010\n", TARGET
        )
        self.sock.recvfrom.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        self.sock.setsockopt.assert_not_called()
        self.sock.connect.assert_not_called()
        self.sock.__exit__.assert_called_once()

    def test_returns_rest_without_using_timing_to_classify(self):
        self.clock.side_effect = [100, 101.49]
        self.sock.recvfrom.return_value = (DDP_REST, TARGET)
        self.assertEqual(self.detector.read_state(), PS5State.REST)

    def test_uses_configured_source_destination_and_timeout(self):
        config = NetworkConfig(9999, 0.75, 0, "192.168.1.20", 0)
        detector = NetworkPS5Detector(TARGET[0], config, self.logger)
        self.sock.recvfrom.return_value = (DDP_ON, (TARGET[0], 9999))
        self.assertEqual(detector.read_state(), PS5State.ON)
        self.sock.bind.assert_called_once_with(("192.168.1.20", 0))
        self.sock.sendto.assert_called_once_with(SEARCH_PACKET, (TARGET[0], 9999))
        self.sock.settimeout.assert_has_calls([call(0.75), call(0.75)])

    def test_timeout_returns_unknown_and_closes_socket(self):
        self.sock.recvfrom.side_effect = socket.timeout()
        self.assertEqual(self.detector.read_state(), PS5State.UNKNOWN)
        self.sock.__exit__.assert_called_once()
        self.assertTrue(self.logger.warning.called)

    def test_all_network_error_paths_return_unknown(self):
        for operation in ("bind", "sendto", "recvfrom"):
            with self.subTest(operation=operation):
                method = getattr(self.sock, operation)
                method.side_effect = OSError("rede indisponível")
                self.assertEqual(self.detector.read_state(), PS5State.UNKNOWN)
                method.side_effect = None

    def test_socket_creation_error_returns_unknown(self):
        self.socket_factory.side_effect = OSError("Sem sockets disponíveis")
        self.assertEqual(self.detector.read_state(), PS5State.UNKNOWN)

    def test_ignores_wrong_ip_and_port_before_accepting_target(self):
        self.sock.recvfrom.side_effect = [
            (DDP_REST, ("192.168.1.11", 9302)),
            (DDP_REST, (TARGET[0], 987)),
            (DDP_ON, TARGET),
        ]
        self.assertEqual(self.detector.read_state(), PS5State.ON)
        self.assertEqual(self.sock.recvfrom.call_count, 3)

    def test_malformed_or_unsupported_response_can_be_followed_by_valid_one(self):
        self.sock.recvfrom.side_effect = [(b"garbage", TARGET), (DDP_REST, TARGET)]
        self.assertEqual(self.detector.read_state(), PS5State.REST)

    def test_only_invalid_responses_returns_unknown(self):
        self.sock.recvfrom.side_effect = [(b"garbage", TARGET), socket.timeout()]
        self.assertEqual(self.detector.read_state(), PS5State.UNKNOWN)

    def test_deadline_is_not_reset_by_unrelated_or_invalid_packets(self):
        self.clock.side_effect = [0, 0.1, 0.9, 1.6]
        self.sock.recvfrom.side_effect = [
            (DDP_ON, ("192.168.1.11", 9302)), (b"garbage", TARGET),
        ]
        self.assertEqual(self.detector.read_state(), PS5State.UNKNOWN)
        self.assertEqual(self.sock.recvfrom.call_count, 2)
        timeouts = [c.args[0] for c in self.sock.settimeout.call_args_list]
        for actual, expected in zip(timeouts, [1.5, 1.4, 0.6]):
            self.assertAlmostEqual(actual, expected)

    def test_retry_after_timeout_can_recover(self):
        second = MagicMock(spec=self.socket_class)
        second.__enter__.return_value = second
        second.recvfrom.return_value = (DDP_REST, TARGET)
        self.socket_factory.side_effect = [self.sock, second]
        self.sock.recvfrom.side_effect = socket.timeout()
        detector = NetworkPS5Detector(TARGET[0], NetworkConfig(retries=1), self.logger)
        self.assertEqual(detector.read_state(), PS5State.REST)
        self.assertEqual(self.socket_factory.call_count, 2)
        self.sock.__exit__.assert_called_once()
        second.__exit__.assert_called_once()

    def test_exhausted_retries_remain_unknown(self):
        self.sock.recvfrom.side_effect = socket.timeout()
        detector = NetworkPS5Detector(TARGET[0], NetworkConfig(retries=3), self.logger)
        self.assertEqual(detector.read_state(), PS5State.UNKNOWN)
        self.assertEqual(self.socket_factory.call_count, 4)
        self.assertEqual(self.sock.sendto.call_count, 4)

    def test_network_failure_does_not_retain_previous_state_or_infer_off(self):
        self.sock.recvfrom.side_effect = [(DDP_ON, TARGET), socket.timeout(), (DDP_REST, TARGET)]
        monitor = StateMonitor(self.detector, 1, self.logger, simulation_mode=False)
        self.assertEqual(monitor.poll_once(), PS5State.ON)
        self.assertEqual(monitor.poll_once(), PS5State.UNKNOWN)
        self.assertEqual(monitor.current_state, PS5State.UNKNOWN)
        self.assertEqual(monitor.poll_once(), PS5State.REST)
        self.assertIn(
            call("Mudança de estado do PS5 (rede/DDP): %s -> %s", "ON", "UNKNOWN"),
            self.logger.info.call_args_list,
        )

    def test_ctrl_c_propagates_and_closes_socket(self):
        self.sock.recvfrom.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.detector.read_state()
        self.sock.__exit__.assert_called_once()

    def test_prolonged_unknown_only_updates_evidence_and_keeps_searching(self):
        self.sock.recvfrom.side_effect = [
            (DDP_ON, TARGET), socket.timeout(), (b"invalid", TARGET),
            socket.timeout(), socket.timeout(), (DDP_REST, TARGET),
        ]
        monitor = StateMonitor(
            self.detector, 1, self.logger, simulation_mode=False,
            clock=Mock(side_effect=[0.0, 10.0, 610.0, 3610.0, 3620.0]),
        )
        for expected in (PS5State.ON, PS5State.UNKNOWN, PS5State.UNKNOWN, PS5State.UNKNOWN):
            self.assertEqual(monitor.poll_once(), expected)
        self.assertEqual(monitor.observation.unknown_duration_seconds, 3600.0)
        self.assertEqual(monitor.observation.consecutive_unknown, 3)
        self.assertEqual(monitor.observation.last_known_state, PS5State.ON)
        self.assertEqual(monitor.current_state, PS5State.UNKNOWN)
        self.assertEqual(monitor.poll_once(), PS5State.REST)
        self.assertEqual(monitor.observation.consecutive_unknown, 0)
        self.assertEqual(self.sock.sendto.call_args_list, [call(SEARCH_PACKET, TARGET)] * 5)
