"""Confirmação de estados e integração explícita com o controlador de telas.

Não contém comandos de Windows, descoberta de hardware ou política por timeout.
"""

from dataclasses import dataclass
from enum import Enum
import logging
from threading import Event

from .detector import PS5State, StateDetector
from .displays import DisplayController, validate_monitor_id
from .monitor import StateMonitor


class AutomationError(ValueError):
    """Configuração inválida ou falha que impede continuar a automação."""


class AutomationControlError(AutomationError):
    """Controle de monitor recusado ou incerto; requer revisão manual."""


class DecisionStatus(str, Enum):
    PENDING = "pending"
    REQUESTED = "requested"
    DUPLICATE = "duplicate"
    IGNORED_UNKNOWN = "ignored_unknown"
    IGNORED_OFF = "ignored_off"
    STOPPED = "stopped"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class AutomationDecision:
    state: PS5State
    status: DecisionStatus
    consecutive_reads: int
    requested_enabled: bool | None = None


class AutomationController:
    """Recebe leituras; só solicita ações confirmadas ao DisplayController.

    UNKNOWN interrompe a confirmação e preserva a última intenção aplicada.
    Uma falha de controle suspende esta instância, sem repetição automática.
    """

    def __init__(
        self, displays: DisplayController, monitor_id: str, logger: logging.Logger,
        *, confirmations: int = 2, dry_run: bool = True,
        physical_control_authorized: bool = False, allow_off_recovery: bool = False,
    ):
        validate_monitor_id(monitor_id)
        if type(confirmations) is not int or confirmations < 2:
            raise AutomationError("confirmations deve ser um inteiro maior ou igual a 2.")
        if any(type(flag) is not bool for flag in (
            dry_run, physical_control_authorized, allow_off_recovery,
        )):
            raise AutomationError("As opções de controle devem ser booleanas.")
        if not dry_run and not physical_control_authorized:
            raise AutomationError("Controle físico exige autorização explícita além de dry_run=False.")
        self._displays = displays
        self._monitor_id = monitor_id
        self.logger = logger
        self._confirmations = confirmations
        self._dry_run = dry_run
        self._allow_off_recovery = allow_off_recovery
        self._candidate: PS5State | None = None
        self._consecutive = 0
        self._last_state: PS5State | None = None
        self._last_requested_enabled: bool | None = None
        self._suspended = False

    @property
    def monitor_id(self) -> str:
        return self._monitor_id

    @property
    def confirmations(self) -> int:
        return self._confirmations

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def suspended(self) -> bool:
        return self._suspended

    @property
    def last_requested_enabled(self) -> bool | None:
        return self._last_requested_enabled

    def reset_confirmation(self) -> None:
        """Descarta leituras pendentes; não limpa falhas nem executa ações."""
        self._candidate = None
        self._consecutive = 0

    def observe(
        self, state: PS5State, *, off_is_trusted: bool = False,
        stop_event: Event | None = None,
    ) -> AutomationDecision:
        if not isinstance(state, PS5State) or type(off_is_trusted) is not bool:
            self.reset_confirmation()
            raise AutomationError("A observação exige PS5State e evidência OFF booleana.")
        if stop_event is not None and stop_event.is_set():
            self.reset_confirmation()
            return AutomationDecision(state, DecisionStatus.STOPPED, 0)
        if self._suspended:
            return AutomationDecision(state, DecisionStatus.SUSPENDED, 0)
        changed = self._last_state is not state
        if changed:
            self.logger.info(
                "AUTOMATION: estado observado %s -> %s.",
                self._last_state.value if self._last_state is not None else "INITIAL", state.value,
            )
        self._last_state = state
        if state is PS5State.UNKNOWN:
            self.reset_confirmation()
            if changed:
                self.logger.info("AUTOMATION: UNKNOWN; manter monitor sem ações e descartar confirmação.")
            return AutomationDecision(state, DecisionStatus.IGNORED_UNKNOWN, 0)
        if state is PS5State.OFF and not (off_is_trusted or self._allow_off_recovery):
            self.reset_confirmation()
            if changed:
                self.logger.info("AUTOMATION: OFF sem fonte confiável ou política explícita; nenhuma ação.")
            return AutomationDecision(state, DecisionStatus.IGNORED_OFF, 0)

        if self._candidate is state:
            self._consecutive = min(self._consecutive + 1, self.confirmations)
        else:
            self._candidate, self._consecutive = state, 1
        if self._consecutive < self.confirmations:
            self.logger.info("AUTOMATION: confirmar %s (%s/%s); aguardar.",
                             state.value, self._consecutive, self.confirmations)
            return AutomationDecision(state, DecisionStatus.PENDING, self._consecutive)
        enabled = state is not PS5State.ON
        if enabled == self._last_requested_enabled:
            self.logger.debug("AUTOMATION: intenção para %s já solicitada; suprimir duplicata.", state.value)
            return AutomationDecision(state, DecisionStatus.DUPLICATE, self._consecutive)
        if stop_event is not None and stop_event.is_set():
            self.reset_confirmation()
            return AutomationDecision(state, DecisionStatus.STOPPED, 0)
        self.logger.info(
            "AUTOMATION: %s confirmado (%s leituras); solicitar %s de %s; dry_run=%s.",
            state.value, self._consecutive, "habilitação" if enabled else "desabilitação",
            self.monitor_id, self._dry_run,
        )
        try:
            self._displays.set_enabled(self.monitor_id, enabled, dry_run=self._dry_run)
        except KeyboardInterrupt:
            self._suspended = True
            self.reset_confirmation()
            self.logger.error("AUTOMATION: controle interrompido; instância suspensa para revisão manual.")
            raise
        except Exception as exc:
            self._suspended = True
            self.reset_confirmation()
            self.logger.error("AUTOMATION: falha de controle; suspender sem repetir ou compensar: %s", exc)
            raise AutomationControlError(f"Controle suspenso; revise manualmente o monitor: {exc}") from exc
        self._last_requested_enabled = enabled
        return AutomationDecision(state, DecisionStatus.REQUESTED, self._consecutive, enabled)


class AutomationRunner:
    """Conecta a interface do detector às decisões e oferece parada cooperativa."""

    def __init__(
        self, detector: StateDetector, controller: AutomationController,
        interval_seconds: float, logger: logging.Logger, *, simulation_mode: bool = True,
        stop_event: Event | None = None,
    ):
        self.controller = controller
        self.logger = logger
        self.monitor = StateMonitor(detector, interval_seconds, logger, simulation_mode=simulation_mode)
        self.stop_event = stop_event if stop_event is not None else Event()

    def stop(self) -> None:
        self.stop_event.set()
        self.controller.reset_confirmation()

    def poll_once(self) -> AutomationDecision | None:
        if self.stop_event.is_set():
            return None
        try:
            state = self.monitor.poll_once()
        except (OSError, ValueError) as exc:
            self.logger.warning("AUTOMATION: falha de leitura tratada como UNKNOWN: %s", exc)
            state = PS5State.UNKNOWN
        except Exception as exc:
            self.stop()
            self.logger.error("AUTOMATION: erro inesperado do detector; encerrar: %s", exc)
            raise AutomationError(f"Erro inesperado do detector: {exc}") from exc
        if self.stop_event.is_set():
            self.controller.reset_confirmation()
            return None
        return self.controller.observe(state, stop_event=self.stop_event)

    def run(self, *, max_polls: int | None = None) -> int:
        if max_polls is not None and (type(max_polls) is not int or max_polls <= 0):
            raise AutomationError("max_polls deve ser um inteiro maior que zero.")
        count = 0
        self.logger.info("AUTOMATION: iniciar; monitor=%s; confirmações=%s; dry_run=%s.",
                         self.controller.monitor_id, self.controller.confirmations, self.controller.dry_run)
        try:
            while not self.stop_event.is_set():
                if self.poll_once() is None:
                    break
                count += 1
                if max_polls is not None and count >= max_polls:
                    break
                if self.stop_event.wait(self.monitor.interval_seconds):
                    break
            return count
        finally:
            self.stop()
            self.logger.info("AUTOMATION: encerrada após %s leitura(s) processada(s); sem ação de saída.", count)
