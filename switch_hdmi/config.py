"""Configuração validada dos detectores simulado e de rede."""

from dataclasses import dataclass
from ipaddress import IPv4Address
import json
from pathlib import Path
from threading import TIMEOUT_MAX

from .detector import PS5State


class ConfigError(ValueError):
    """Configuração ausente ou inválida."""


def validate_ipv4(value: object, name: str, *, allow_unspecified: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} deve ser um endereço IPv4 literal.")
    try:
        address = IPv4Address(value)
    except ValueError as exc:
        raise ConfigError(f"{name} deve ser um endereço IPv4 literal.") from exc
    if address.is_multicast or address.is_reserved or (
        address.is_unspecified and not allow_unspecified
    ):
        raise ConfigError(f"{name} deve identificar um endereço unicast válido.")
    return str(address)


@dataclass(frozen=True)
class NetworkConfig:
    discovery_port: int = 9302
    timeout_seconds: float = 1.5
    retries: int = 1
    local_ip: str = "0.0.0.0"
    local_port: int = 9303

    def __post_init__(self):
        for name, minimum in (("discovery_port", 1), ("local_port", 0)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= 65535:
                raise ConfigError(f"network.{name} deve ser um inteiro entre {minimum} e 65535.")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 30
        ):
            raise ConfigError("network.timeout_seconds deve ser um número maior que 0 e até 30.")
        if type(self.retries) is not int or not 0 <= self.retries <= 3:
            raise ConfigError("network.retries deve ser um inteiro entre 0 e 3.")
        validate_ipv4(self.local_ip, "network.local_ip", allow_unspecified=True)


@dataclass(frozen=True)
class AppConfig:
    poll_interval_seconds: float
    states: tuple[PS5State, ...]
    repeat: bool
    log_file: Path
    simulation_mode: bool = True
    ps5_ip: str | None = None
    network: NetworkConfig = NetworkConfig()

    @property
    def check_interval_seconds(self) -> float:
        # Mantém a API da Etapa 2.1 e expõe o nome novo do JSON.
        return self.poll_interval_seconds


def _object(
    value: object, fields: set[str], name: str, *, optional: frozenset[str] = frozenset()
) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} deve ser um objeto JSON.")
    missing = fields - value.keys() - optional
    extra = value.keys() - fields
    if missing or extra:
        details = []
        if missing:
            details.append(f"campos obrigatórios ausentes: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"campos desconhecidos: {', '.join(sorted(extra))}")
        raise ConfigError(f"{name}: {'; '.join(details)}.")
    return value


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    try:
        # utf-8-sig aceita também arquivos salvos com BOM no Windows.
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(f"Não foi possível ler {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("config deve ser um objeto JSON.")
    legacy = "mode" in data or "poll_interval_seconds" in data
    if legacy:
        data = _object(
            data, {"mode", "poll_interval_seconds", "simulator", "logging"}, "config"
        )
        if data["mode"] != "simulator":
            raise ConfigError('O mode legado deve ser "simulator".')
        simulation_mode, ps5_ip, network = True, None, NetworkConfig()
        interval_name = "poll_interval_seconds"
    else:
        data = _object(data, {
            "simulation_mode", "ps5_ip", "check_interval_seconds", "network",
            "simulator", "logging",
        }, "config", optional=frozenset({"simulation_mode"}))
        simulation_mode = data.get("simulation_mode", True)
        if not isinstance(simulation_mode, bool):
            raise ConfigError("simulation_mode deve ser true ou false.")
        ps5_ip = data["ps5_ip"]
        if ps5_ip is not None or not simulation_mode:
            ps5_ip = validate_ipv4(ps5_ip, "ps5_ip")
        network_data = _object(data["network"], {
            "discovery_port", "timeout_seconds", "retries", "local_ip", "local_port",
        }, "network")
        network = NetworkConfig(**network_data)
        interval_name = "check_interval_seconds"

    interval = data[interval_name]
    valid_interval = (
        not isinstance(interval, bool)
        and isinstance(interval, (int, float))
        and 0 < interval <= TIMEOUT_MAX
    )
    if not valid_interval:
        raise ConfigError(
            f"{interval_name} deve ser um número finito maior que zero "
            f"e menor ou igual a {TIMEOUT_MAX} s."
        )

    simulator = _object(data["simulator"], {"states", "repeat"}, "simulator")
    raw_states = simulator["states"]
    if not isinstance(raw_states, list) or not raw_states:
        raise ConfigError("simulator.states deve ser uma lista não vazia de ON, REST ou OFF.")
    try:
        states = tuple(PS5State(state) for state in raw_states)
        if PS5State.UNKNOWN in states:
            raise ValueError("UNKNOWN é reservado ao detector real.")
    except (ValueError, TypeError) as exc:
        raise ConfigError("simulator.states aceita somente ON, REST ou OFF.") from exc
    if not isinstance(simulator["repeat"], bool):
        raise ConfigError("simulator.repeat deve ser true ou false.")

    log_config = _object(data["logging"], {"file"}, "logging")
    log_name = log_config["file"]
    if not isinstance(log_name, str) or not log_name.strip() or "\x00" in log_name:
        raise ConfigError("logging.file deve ser um caminho não vazio e sem caracteres nulos.")
    log_file = Path(log_name)
    if not log_file.is_absolute():
        log_file = config_path.parent / log_file

    return AppConfig(
        float(interval), states, simulator["repeat"], log_file,
        simulation_mode, ps5_ip, network,
    )

