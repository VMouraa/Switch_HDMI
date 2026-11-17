"""Executável de desenvolvimento: python -m switch_hdmi."""

import argparse
import logging
from pathlib import Path
import sys

from .config import ConfigError, load_config
from .factory import create_detector
from .monitor import StateMonitor


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use um inteiro maior que zero.") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("Use um inteiro maior que zero.")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor de estados do PS5 (simulação ou DDP).")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    limits = parser.add_mutually_exclusive_group()
    limits.add_argument("--once", action="store_true", help="Executa uma única consulta.")
    limits.add_argument("--max-polls", type=_positive_int, help="Limita o total de consultas.")
    args = parser.parse_args(argv)

    # Logger independente, sem alterar a configuração de logging de outros módulos.
    logger = logging.Logger("switch_hdmi", level=logging.INFO)
    try:
        config = load_config(args.config)
        detector = create_detector(config, logger)
        monitor = StateMonitor(
            detector, config.check_interval_seconds, logger,
            simulation_mode=config.simulation_mode,
        )
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
        logger.addHandler(file_handler)
        logger.addHandler(logging.StreamHandler())
        for handler in logger.handlers:
            handler.setFormatter(formatter)
        mode = "simulator" if config.simulation_mode else "network/DDP"
        logger.info(
            "Monitor iniciado em modo %s; intervalo entre consultas: %s s.",
            mode, config.check_interval_seconds,
        )
        try:
            count = monitor.run(max_polls=1 if args.once else args.max_polls)
            logger.info("Monitor encerrado após %s consulta(s).", count)
        except KeyboardInterrupt:
            logger.info("Monitor encerrado pelo usuário.")
        return 0
    except (ConfigError, OSError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
