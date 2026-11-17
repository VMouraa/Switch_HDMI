"""Histórico passivo para diagnóstico e futura avaliação de recuperação."""

from collections.abc import Callable
from dataclasses import dataclass
import time

from .detector import PS5State


@dataclass(frozen=True)
class StateObservation:
    """Retrato de uma leitura; UNKNOWN continua distinto do último estado válido.

    Os tempos usam relógio monotônico, não data/hora civil. A duração representa
    apenas a sequência de UNKNOWN observada, sem provar ausência física do PS5.
    """

    state: PS5State
    observed_at: float
    last_known_state: PS5State | None
    last_known_at: float | None
    unknown_since: float | None
    consecutive_unknown: int

    @property
    def unknown_duration_seconds(self) -> float:
        if self.unknown_since is None:
            return 0.0
        return self.observed_at - self.unknown_since


class StateHistory:
    """Acumula evidência em memória constante, sem decidir ou executar ações."""

    def __init__(self, *, clock: Callable[[], float] | None = None):
        self._clock = clock if clock is not None else time.monotonic
        self._latest: StateObservation | None = None

    @property
    def latest(self) -> StateObservation | None:
        return self._latest

    def observe(self, state: PS5State) -> StateObservation:
        if not isinstance(state, PS5State):
            raise ValueError("O histórico exige um estado PS5State válido.")
        now = self._clock()
        previous = self._latest
        if state is PS5State.UNKNOWN:
            continuing = previous is not None and previous.state is PS5State.UNKNOWN
            observation = StateObservation(
                state=state,
                observed_at=now,
                last_known_state=previous.last_known_state if previous else None,
                last_known_at=previous.last_known_at if previous else None,
                unknown_since=previous.unknown_since if continuing else now,
                consecutive_unknown=previous.consecutive_unknown + 1 if continuing else 1,
            )
        else:
            observation = StateObservation(
                state=state,
                observed_at=now,
                last_known_state=state,
                last_known_at=now,
                unknown_since=None,
                consecutive_unknown=0,
            )
        self._latest = observation
        return observation
