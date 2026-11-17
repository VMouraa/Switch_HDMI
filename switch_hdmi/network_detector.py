"""Consulta DDP do Remote Play: somente SRCH, sem autenticação ou comandos.

Referências do protocolo (implementação própria, sem dependências externas):
https://github.com/chiaki-ng/chiaki-ng/blob/main/lib/include/chiaki/discovery.h
https://github.com/chiaki-ng/chiaki-ng/blob/main/lib/src/discovery.c
https://github.com/ktnrg45/pyremoteplay/blob/master/pyremoteplay/ddp.py
"""

import logging
import re
import socket
import time

from .config import NetworkConfig, validate_ipv4
from .detector import PS5State


DDP_VERSION = "00030010"
SEARCH_PACKET = (
    f"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:{DDP_VERSION}\n"
).encode("ascii")
MAX_RESPONSE_BYTES = 8192


def parse_ddp_response(payload: bytes) -> PS5State:
    """Exige resposta DDP de PS5; conteúdo ambíguo nunca significa OFF."""
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        return PS5State.UNKNOWN
    try:
        text = payload.removesuffix(b"\0").decode("utf-8")
    except UnicodeDecodeError:
        return PS5State.UNKNOWN
    text = text.replace("\r\n", "\n")
    if any(
        (ord(character) < 32 and character not in "\n\t") or ord(character) == 127
        for character in text
    ):
        return PS5State.UNKNOWN
    lines = text.rstrip("\n").split("\n")
    status = re.fullmatch(r"HTTP/1\.1 ([0-9]{3}) [^\n]+", lines[0])
    if status is None:
        return PS5State.UNKNOWN
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        key = key.strip().lower()
        if not separator or not re.fullmatch(r"[a-z0-9-]+", key) or key in headers:
            return PS5State.UNKNOWN
        headers[key] = value.strip()
    if headers.get("device-discovery-protocol-version") != DDP_VERSION:
        return PS5State.UNKNOWN
    if headers.get("host-type", "PS5") != "PS5":
        return PS5State.UNKNOWN
    # DDP não fornece evidência confiável de OFF; não existe fallback por silêncio.
    return {"200": PS5State.ON, "620": PS5State.REST}.get(
        status.group(1), PS5State.UNKNOWN
    )


class NetworkPS5Detector:
    """Descoberta dirigida ao IPv4 configurado, com prazo total por tentativa."""

    def __init__(self, ps5_ip: str, config: NetworkConfig, logger: logging.Logger):
        self.ps5_ip = validate_ipv4(ps5_ip, "ps5_ip")
        self.config = config
        self.logger = logger

    def read_state(self) -> PS5State:
        target = (self.ps5_ip, self.config.discovery_port)
        attempts = self.config.retries + 1
        for attempt in range(1, attempts + 1):
            deadline = time.monotonic() + self.config.timeout_seconds
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(self.config.timeout_seconds)
                    sock.bind((self.config.local_ip, self.config.local_port))
                    sock.sendto(SEARCH_PACKET, target)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        sock.settimeout(remaining)
                        payload, source = sock.recvfrom(MAX_RESPONSE_BYTES + 1)
                        if source != target:
                            self.logger.warning("DDP: resposta de origem diferente do PS5 ignorada.")
                            continue
                        state = parse_ddp_response(payload)
                        if state is not PS5State.UNKNOWN:
                            self.logger.info(
                                "DDP: resposta válida de %s:%s; estado=%s.",
                                *target, state.value,
                            )
                            return state
                        self.logger.warning(
                            "DDP: resposta inválida, incompatível ou sem estado reconhecido de %s:%s.",
                            *target,
                        )
            except socket.timeout:
                self.logger.warning(
                    "DDP: timeout de %s s em %s:%s (tentativa %s/%s).",
                    self.config.timeout_seconds, *target, attempt, attempts,
                )
            except OSError as exc:
                self.logger.warning(
                    "DDP: falha de rede em %s:%s (tentativa %s/%s): %s",
                    *target, attempt, attempts, exc,
                )
        self.logger.warning(
            "DDP: sem evidência válida do estado de %s após %s tentativa(s); UNKNOWN.",
            self.ps5_ip, attempts,
        )
        return PS5State.UNKNOWN
