"""Shared entry point for the standalone benchmark harnesses."""

from typing import Any


def run_main(build_parser, run, argv=None) -> Any:
    return run(build_parser().parse_args(argv))
