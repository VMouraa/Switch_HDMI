"""Consulta periódica e registro de transições, sem ações sobre monitores."""

from collections.abc import Callable
import logging
from threading import Event, TIMEOUT_MAX

from .detector import PS5State, StateDetector
from .observations import StateHistory, StateObservation


class StateMonitor:
    def __init__(
        self, detector: StateDetector, interval_seconds: float, logger: logging.Logger,
        *, simulation_mode: bool = True,
        clock: Callable[[], float] | None = None,
    ):
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not 0 < interval_seconds <= TIMEOUT_MAX
        ):
            raise ValueError("Intervalo fora do limite de espera suportado.")
        self.detector = detector
        self.interval_seconds = interval_seconds
        self.logger = logger
        self.current_state: PS5State | None = None
        self._subject = "PS5 (simulado)" if simulation_mode else "PS5 (rede/DDP)"
        self._history = StateHistory(clock=clock)

    @property
    def observation(self) -> StateObservation | None:
        """Última observação imutável; não aplica políticas ou ações."""
        return self._history.latest

    def poll_once(self) -> PS5State:
        state = self.detector.read_state()
        if not isinstance(state, PS5State):
            raise ValueError("O detector retornou um estado inválido.")
        self._history.observe(state)
        if self.current_state is None:
            self.logger.info(f"Estado inicial do {self._subject}: %s", state.value)
        elif state != self.current_state:
            self.logger.info(
                f"Mudança de estado do {self._subject}: %s -> %s",
                self.current_state.value,
                state.value,
            )
        self.current_state = state
        return state

    def run(self, *, max_polls: int | None = None, stop_event: Event | None = None) -> int:
        """Consulta imediatamente e espera entre consultas; retorna a quantidade lida."""
        if max_polls is not None and (
            isinstance(max_polls, bool) or not isinstance(max_polls, int) or max_polls <= 0
        ):
            raise ValueError("max_polls deve ser um inteiro maior que zero.")
        stop = stop_event if stop_event is not None else Event()
        count = 0
        while not stop.is_set():
            self.poll_once()
            count += 1
            if max_polls is not None and count >= max_polls:
                break
            if stop.wait(self.interval_seconds):
                break
        return count

