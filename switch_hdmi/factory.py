"""Seleção explícita de detector; simulação nunca abre sockets."""

import logging

from .config import AppConfig
from .detector import SimulatedPS5Detector, StateDetector
from .network_detector import NetworkPS5Detector


def create_detector(config: AppConfig, logger: logging.Logger) -> StateDetector:
    if config.simulation_mode:
        return SimulatedPS5Detector(config.states, repeat=config.repeat)
    if config.ps5_ip is None:
        raise ValueError("ps5_ip é obrigatório quando simulation_mode=false.")
    return NetworkPS5Detector(config.ps5_ip, config.network, logger)
