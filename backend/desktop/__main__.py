"""Run the portable desktop launcher."""

from __future__ import annotations

import sys
from pathlib import Path


def _write_early_self_test_failure(exc: BaseException) -> None:
    """Report failures that happen before the desktop launcher can handle them."""

    try:
        option_index = sys.argv.index("--self-test-report")
        report = Path(sys.argv[option_index + 1])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
    except (IndexError, OSError, ValueError):
        pass


try:
    from backend.desktop.launcher import main

    exit_code = main()
except BaseException as exc:
    _write_early_self_test_failure(exc)
    raise

raise SystemExit(exit_code)
