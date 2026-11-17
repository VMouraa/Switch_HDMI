"""Entrada explícita para integração: python -m switch_hdmi.automation_control."""

import argparse
import logging
from pathlib import Path
import sys

from .automation import AutomationController, AutomationError, AutomationRunner
from .config import ConfigError, load_config
from .displays import DisplayController, DisplayError, validate_monitor_id
from .factory import create_detector
from .multimonitor_tool import CSVInventoryBackend, MultiMonitorToolBackend


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Informe um inteiro maior que zero.") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("Informe um inteiro maior que zero.")
    return number


def _confirmations(value: str) -> int:
    number = _positive_int(value)
    if number < 2:
        raise argparse.ArgumentTypeError("São necessárias pelo menos duas confirmações.")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Integração PS5/tela; dry_run por padrão.")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--monitor-id", required=True, type=validate_monitor_id)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--tool", type=Path, help="Caminho absoluto de MultiMonitorTool.exe.")
    sources.add_argument("--inventory-csv", type=Path, help="Inventário offline; só permite dry_run.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--execute", dest="dry_run", action="store_false", help="Autoriza controle físico nesta execução.")
    parser.add_argument("--confirmations", type=_confirmations, default=2)
    parser.add_argument("--max-polls", type=_positive_int)
    parser.add_argument("--allow-off-recovery", action="store_true", help="Política explícita: OFF confirmado pode habilitar a tela; UNKNOWN permanece sem ação.")
    parser.add_argument("--log-file", type=Path, help="Padrão: logging.file do JSON.")
    args = parser.parse_args(argv)
    logger = logging.Logger("switch_hdmi.automation", level=logging.INFO)
    try:
        config = load_config(args.config)
        if not args.dry_run and (args.inventory_csv is not None or config.simulation_mode):
            raise AutomationError("--execute exige detector real e --tool; simulação e CSV offline só permitem dry_run.")
        backend = (CSVInventoryBackend(args.inventory_csv) if args.inventory_csv is not None
                   else MultiMonitorToolBackend(args.tool))
        log_file = args.log_file if args.log_file is not None else config.log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.addHandler(logging.StreamHandler())
        logger.addHandler(logging.FileHandler(log_file, encoding="utf-8"))
        for handler in logger.handlers:
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.info("AUTOMATION: modo=%s; política OFF=%s; autorização física=%s.",
                    "simulator" if config.simulation_mode else "network/DDP",
                    args.allow_off_recovery, not args.dry_run)
        displays = DisplayController(backend, logger)
        controller = AutomationController(
            displays, args.monitor_id, logger, confirmations=args.confirmations,
            dry_run=args.dry_run, physical_control_authorized=not args.dry_run,
            allow_off_recovery=args.allow_off_recovery,
        )
        runner = AutomationRunner(
            create_detector(config, logger), controller, config.check_interval_seconds,
            logger, simulation_mode=config.simulation_mode,
        )
        runner.run(max_polls=args.max_polls)
        return 0
    except (AutomationError, ConfigError, DisplayError, OSError) as exc:
        logger.error("AUTOMATION: encerramento por erro: %s", exc)
        return 2
    except KeyboardInterrupt:
        logger.warning("AUTOMATION: interrompida pelo usuário; nenhuma ação adicional de saída.")
        return 130
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
