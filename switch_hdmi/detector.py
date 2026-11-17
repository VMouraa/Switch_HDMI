"""Interface comum, estados e simulador preservado da Etapa 2.1."""

from enum import Enum
from typing import Protocol


class PS5State(str, Enum):
    ON = "ON"
    REST = "REST"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"


class StateDetector(Protocol):
    def read_state(self) -> PS5State:
        """Retorna o estado observado do console."""
        ...


class SimulatedPS5Detector:
    """Avança um estado por leitura; ao terminar, mantém o último ou repete."""

    def __init__(self, states: tuple[PS5State, ...], repeat: bool = False):
        self._states = tuple(states)
        if not self._states or any(
            not isinstance(state, PS5State) for state in self._states
        ):
            raise ValueError("A sequência deve conter estados PS5State válidos.")
        if not isinstance(repeat, bool):
            raise ValueError("repeat deve ser booleano.")
        self._repeat = repeat
        self._index = 0

    def read_state(self) -> PS5State:
        state = self._states[self._index]
        if self._index < len(self._states) - 1:
            self._index += 1
        elif self._repeat:
            self._index = 0
        return state

