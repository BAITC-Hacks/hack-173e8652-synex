"""Command-line entrypoint for the complete MoneyGraph AML workflow."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from moneygraph.config import get_settings
from moneygraph.logging_config import configure_logging
from moneygraph.pipeline.service import run_analysis

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="moneygraph")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="run the deterministic full analysis")
    analyze.add_argument("--data", type=Path, default=settings.data_dir)
    analyze.add_argument("--out", type=Path, default=settings.out_dir)
    analyze.add_argument("--artifacts", type=Path, default=settings.artifacts_dir)
    analyze.add_argument("--config", type=Path, default=settings.analysis_config)
    analyze.add_argument("--database-url", default=settings.database_url)
    analyze.add_argument("--log-level", default=settings.log_level)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(args.log_level)
    try:
        result = run_analysis(
            args.data,
            args.out,
            args.artifacts,
            config_path=args.config,
            database_url=args.database_url,
        )
    except Exception as exc:
        LOGGER.error("analysis failed: %s", exc, extra={"status": "failed"})
        return 1

    manifest = result.manifest
    print(f"run_id={result.run_id}")
    for stage, duration in manifest["stage_durations_seconds"].items():
        print(f"{stage}: {duration:.3f}s")
    print(f"total: {manifest['duration_seconds']:.3f}s")
    print(f"outputs: {result.out_dir}")
    print(f"artifacts: {result.artifacts_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
