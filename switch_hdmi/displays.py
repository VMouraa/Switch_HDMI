"""Inventário e controle manual de monitores, isolados do detector do PS5."""

from collections import Counter
from dataclasses import dataclass
import logging
import re
from typing import Protocol


class DisplayError(ValueError):
    """Operação recusada ou inventário indisponível/inseguro."""


class DisplayVerificationError(DisplayError):
    """O comando pode ter sido aplicado; o resultado exige revisão manual."""


@dataclass(frozen=True)
class DisplayInfo:
    monitor_id: str | None
    device_name: str
    friendly_name: str
    serial_number: str
    active: bool
    primary: bool
    connected: bool
    # Mantidos como valores exportados para detectar alterações involuntárias.
    resolution: str
    position: str
    extent: str
    orientation: str
    frequency: str
    colors: str


@dataclass(frozen=True)
class DisplayAction:
    monitor_id: str
    enable: bool
    dry_run: bool
    changed: bool
    verified: bool


class DisplayBackend(Protocol):
    def list_monitors(self) -> tuple[DisplayInfo, ...]:
        ...

    def set_enabled(self, monitor_id: str, enabled: bool) -> None:
        ...


def validate_monitor_id(value: object) -> str:
    """Somente o Monitor ID completo; não aceita índices, apelidos ou curingas."""
    if not isinstance(value, str) or re.fullmatch(
        r"MONITOR\\[A-Z0-9_.&-]+\\"
        r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}"
        r"\\[A-Z0-9_.&-]+", value, flags=re.IGNORECASE,
    ) is None:
        raise DisplayError("Informe o Monitor ID completo exportado; números e apelidos não são aceitos.")
    return value


def _inventory_key(inventory: tuple[DisplayInfo, ...]) -> Counter:
    return Counter(inventory)


def _checked_target(inventory: tuple[DisplayInfo, ...], monitor_id: str, enable: bool) -> DisplayInfo:
    seen_ids: set[str] = set()
    sources: set[str] = set()
    active = [display for display in inventory if display.active]
    for display in inventory:
        if any(type(value) is not bool for value in (
            display.active, display.primary, display.connected,
        )):
            raise DisplayError("Inventário contém flags de estado inválidas.")
        if (display.active and not display.connected) or (display.primary and not display.active):
            raise DisplayError("Inventário contém estados inconsistentes.")
        if display.active and not all((
            display.resolution, display.position, display.extent,
            display.orientation, display.frequency, display.colors,
        )):
            raise DisplayError("Monitor ativo sem dados completos de layout; operação bloqueada.")
        if display.monitor_id:
            key = validate_monitor_id(display.monitor_id).casefold()
            if key in seen_ids:
                raise DisplayError("Inventário contém Monitor ID duplicado/ambíguo.")
            seen_ids.add(key)
        if display.connected:
            if not display.monitor_id or not display.device_name:
                raise DisplayError("Monitor conectado sem identificação suficiente; operação bloqueada.")
            source = display.device_name.casefold()
            if source in sources:
                raise DisplayError("Origem de vídeo compartilhada/duplicada; use área de trabalho estendida.")
            sources.add(source)
    selected = [display for display in inventory
                if display.monitor_id and display.monitor_id.casefold() == monitor_id.casefold()]
    if len(selected) != 1:
        raise DisplayError("Monitor ID não encontrado de forma única no inventário atual.")
    target = selected[0]
    if not target.connected:
        raise DisplayError("O monitor selecionado está desconectado.")
    if not enable and target.active and len(active) <= 1:
        raise DisplayError("Não é permitido desativar o último monitor ativo.")
    if target.primary:
        raise DisplayError("Operações sobre o monitor principal são bloqueadas nesta etapa.")
    if len([display for display in active if display.primary]) != 1:
        raise DisplayError("Não foi possível identificar exatamente um monitor principal ativo.")
    return target


class DisplayController:
    """Valida e registra ações; dry_run é o padrão em todas as operações."""

    def __init__(self, backend: DisplayBackend, logger: logging.Logger):
        self.backend = backend
        self.logger = logger

    def list_monitors(self) -> tuple[DisplayInfo, ...]:
        return self.backend.list_monitors()

    def enable(self, monitor_id: str, *, dry_run: bool = True) -> DisplayAction:
        return self.set_enabled(monitor_id, True, dry_run=dry_run)

    def disable(self, monitor_id: str, *, dry_run: bool = True) -> DisplayAction:
        return self.set_enabled(monitor_id, False, dry_run=dry_run)

    def set_enabled(self, monitor_id: str, enabled: bool, *, dry_run: bool = True) -> DisplayAction:
        if type(dry_run) is not bool or type(enabled) is not bool:
            raise DisplayError("dry_run e enabled devem ser booleanos.")
        validate_monitor_id(monitor_id)
        action = "habilitar" if enabled else "desabilitar"
        try:
            before = self.backend.list_monitors()
            target = _checked_target(before, monitor_id, enabled)
            # Usa a grafia exata do inventário, após correspondência integral.
            selected_id = target.monitor_id
            if target.active == enabled:
                self.logger.info("NOOP: %s %s; estado desejado já presente.", action, selected_id)
                return DisplayAction(selected_id, enabled, dry_run, False, False)
            if dry_run:
                self.logger.info("DRY_RUN: %s exclusivamente %s (%s); nenhuma alteração executada.",
                                 action, selected_id, target.friendly_name)
                return DisplayAction(selected_id, enabled, True, False, False)
            current = self.backend.list_monitors()
            _checked_target(current, selected_id, enabled)
            if _inventory_key(current) != _inventory_key(before):
                raise DisplayError("Topologia mudou antes da execução; liste e selecione novamente.")
        except DisplayError as exc:
            self.logger.warning("Operação de monitores recusada (%s): %s", action, exc)
            raise

        self.logger.warning("EXECUTE: %s exclusivamente %s.", action, selected_id)
        # A partir daqui, um erro pode ocorrer depois de o Windows aplicar a ação.
        try:
            self.backend.set_enabled(selected_id, enabled)
            after = self.backend.list_monitors()
            actual = _checked_target(after, selected_id, enabled)
            if actual.active != enabled:
                raise DisplayError("O monitor selecionado não atingiu o estado solicitado.")
            before_others = tuple(display for display in before if display is not target)
            after_others = tuple(display for display in after
                                 if not display.monitor_id or
                                 display.monitor_id.casefold() != selected_id.casefold())
            if _inventory_key(before_others) != _inventory_key(after_others):
                raise DisplayError("Outro monitor, sua identidade ou seu layout foi alterado.")
        except (DisplayError, OSError, KeyboardInterrupt) as exc:
            message = (
                f"Resultado de {action} {selected_id} não confirmado; a ação pode ter sido aplicada. "
                f"Revise manualmente a configuração. Não haverá repetição ou rollback automático: {exc}"
            )
            self.logger.error(message)
            raise DisplayVerificationError(message) from exc
        self.logger.info("VERIFIED: %s %s; demais monitores preservados no inventário.", action, selected_id)
        return DisplayAction(selected_id, enabled, False, True, True)
