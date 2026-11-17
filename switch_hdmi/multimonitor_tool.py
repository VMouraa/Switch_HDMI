"""Adaptador do MultiMonitorTool; somente exportação CSV e ação sobre um ID.

Documentação: https://www.nirsoft.net/utils/multi_monitor_tool.html
O executável não é distribuído, baixado ou localizado automaticamente no PATH.
"""

import codecs
import csv
import io
from pathlib import Path
import subprocess
import sys
import tempfile

from .displays import DisplayError, DisplayInfo, validate_monitor_id


REQUIRED_COLUMNS = {
    "Name", "Monitor ID", "Monitor Name", "Monitor Serial Number", "Active",
    "Primary", "Disconnected", "Resolution", "Left-Top", "Right-Bottom",
    "Orientation", "Frequency", "Colors",
}


def parse_inventory(data: bytes) -> tuple[DisplayInfo, ...]:
    """CSV completo em inglês; nunca assume que estado vazio significa No."""
    try:
        encoding = "utf-16" if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
        text = data.decode(encoding)
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise DisplayError("CSV sem cabeçalho único.")
        if not REQUIRED_COLUMNS <= set(reader.fieldnames):
            raise DisplayError("CSV incompleto ou traduzido: exporte as colunas esperadas em inglês.")
        monitors = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise DisplayError("Linha de CSV incompleta ou com colunas excedentes.")
            def flag(name: str) -> bool:
                value = row[name].strip().casefold()
                if value not in ("yes", "no"):
                    raise DisplayError(f"CSV com valor inválido em {name}: {row[name]!r}.")
                return value == "yes"
            monitors.append(DisplayInfo(
                monitor_id=row["Monitor ID"].strip() or None,
                device_name=row["Name"].strip(),
                friendly_name=row["Monitor Name"].strip(),
                serial_number=row["Monitor Serial Number"].strip(),
                active=flag("Active"), primary=flag("Primary"),
                connected=not flag("Disconnected"),
                resolution=row["Resolution"].strip(), position=row["Left-Top"].strip(),
                extent=row["Right-Bottom"].strip(), orientation=row["Orientation"].strip(),
                frequency=row["Frequency"].strip(), colors=row["Colors"].strip(),
            ))
        return tuple(monitors)
    except (UnicodeError, csv.Error) as exc:
        raise DisplayError(f"Não foi possível interpretar o inventário CSV: {exc}") from exc


class CSVInventoryBackend:
    """Prévia offline: arquivo salvo/sintético jamais pode controlar hardware."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def list_monitors(self) -> tuple[DisplayInfo, ...]:
        try:
            return parse_inventory(self.path.read_bytes())
        except OSError as exc:
            raise DisplayError(f"Não foi possível ler o CSV: {exc}") from exc

    def set_enabled(self, monitor_id: str, enabled: bool) -> None:
        raise DisplayError("Inventário offline não pode executar alterações de monitores.")


class MultiMonitorToolBackend:
    def __init__(self, executable: str | Path, *, timeout_seconds: float = 10.0):
        path = Path(executable)
        if not path.is_absolute() or not path.is_file() or path.name.casefold() != "multimonitortool.exe":
            raise DisplayError("Informe o caminho absoluto de um MultiMonitorTool.exe existente.")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
            raise DisplayError("Timeout do MultiMonitorTool deve ser maior que zero e até 60 segundos.")
        if sys.platform != "win32":
            raise DisplayError("O controle real de monitores exige Windows.")
        self.executable = path.resolve()
        self.timeout_seconds = float(timeout_seconds)

    def _run(self, *arguments: str) -> None:
        try:
            result = subprocess.run(
                [str(self.executable), *arguments], shell=False, check=False,
                capture_output=True, timeout=self.timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=str(self.executable.parent),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DisplayError(f"Falha/timeout no MultiMonitorTool; resultado não presumido: {exc}") from exc
        if result.returncode != 0:
            raise DisplayError(f"MultiMonitorTool retornou código {result.returncode}.")

    def list_monitors(self) -> tuple[DisplayInfo, ...]:
        try:
            with tempfile.TemporaryDirectory(prefix="ps5-displays-") as folder:
                report = Path(folder) / "monitors.csv"
                self._run("/HideInactiveMonitors", "0", "/scomma", str(report))
                return parse_inventory(report.read_bytes())
        except OSError as exc:
            raise DisplayError(f"Não foi possível obter o inventário do MultiMonitorTool: {exc}") from exc

    def set_enabled(self, monitor_id: str, enabled: bool) -> None:
        validate_monitor_id(monitor_id)
        if type(enabled) is not bool:
            raise DisplayError("enabled deve ser booleano.")
        self._run("/enable" if enabled else "/disable", monitor_id)
