"""CLI manual independente: python -m switch_hdmi.display_control."""

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys

from .displays import DisplayController, DisplayError
from .multimonitor_tool import CSVInventoryBackend, MultiMonitorToolBackend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controle manual de monitores; dry_run por padrão.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tool", type=Path, help="Caminho absoluto de MultiMonitorTool.exe.")
    source.add_argument("--inventory-csv", type=Path, help="CSV offline; permite somente prévias.")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    execution.add_argument("--execute", dest="dry_run", action="store_false", help="Executa uma ação manual explícita.")
    parser.add_argument("--log-file", type=Path, default=Path("logs/monitor_control.log"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Lista o inventário sem alterar dispositivos.")
    for action in ("enable", "disable"):
        command = commands.add_parser(action)
        command.add_argument("--monitor-id", required=True)
    args = parser.parse_args(argv)
    logger = logging.Logger("switch_hdmi.display_control", level=logging.INFO)
    try:
        if args.inventory_csv is not None and not args.dry_run:
            raise DisplayError("--execute não é permitido com inventário offline.")
        backend = (CSVInventoryBackend(args.inventory_csv) if args.inventory_csv is not None
                   else MultiMonitorToolBackend(args.tool))
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.addHandler(logging.StreamHandler())
        logger.addHandler(logging.FileHandler(args.log_file, encoding="utf-8"))
        for handler in logger.handlers:
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        controller = DisplayController(backend, logger)
        if args.command == "list":
            inventory = controller.list_monitors()
            print(json.dumps([asdict(display) for display in inventory], ensure_ascii=True, indent=2))
            logger.info("Inventário listado (%s monitores); origem=%s; sem alterações.",
                        len(inventory), "offline" if args.inventory_csv else "MultiMonitorTool")
        else:
            result = controller.set_enabled(args.monitor_id, args.command == "enable", dry_run=args.dry_run)
            print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (DisplayError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.warning("Consulta/prévia de monitores interrompida pelo usuário.")
        return 130
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
