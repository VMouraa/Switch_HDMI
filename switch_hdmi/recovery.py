"""Contrato reservado para uma futura política configurável de recuperação.

Não há implementação, configuração ativa ou executor de recuperação nesta
etapa. A CLI e o monitor não instanciam nem consultam este contrato.
"""

from typing import Protocol

from .observations import StateObservation


class RecoveryPolicy(Protocol):
    def should_recover(self, observation: StateObservation) -> bool:
        """Avalia evidências sem efeitos colaterais e sem reinterpretar UNKNOWN.

        Limites e decisões deverão pertencer a uma implementação futura,
        separada dos detectores e de qualquer executor de ações.
        """
        ...
