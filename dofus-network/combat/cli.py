"""Shared entry point for the standalone benchmark harnesses."""

def run_main(build_parser, run, argv=None):
    return run(build_parser().parse_args(argv))
