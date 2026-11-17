"""Dados sintéticos: não são capturas de um PS5 físico."""

MODERN_CONFIG = {
    "simulation_mode": True,
    "ps5_ip": None,
    "check_interval_seconds": 0.001,
    "network": {
        "discovery_port": 9302,
        "timeout_seconds": 1.5,
        "retries": 1,
        "local_ip": "0.0.0.0",
        "local_port": 9303,
    },
    "simulator": {"states": ["OFF", "ON", "REST", "ON", "OFF"], "repeat": False},
    "logging": {"file": "logs/ps5_detector.log"},
}

DDP_ON = (
    b"HTTP/1.1 200 Ok\n"
    b"host-id:0123456789AB\n"
    b"host-type:PS5\n"
    b"host-name:Console de teste\n"
    b"host-request-port:9295\n"
    b"device-discovery-protocol-version:00030010\n"
    b"system-version:08050001\n"
    b"running-app-name:Game: Subtitle\n"
)
DDP_REST = DDP_ON.replace(b"200 Ok", b"620 Server Standby")
