import logging
import os

import yaml
from pathlib import Path
from rich.console import Console
from rich.logging import RichHandler

REPO_ROOT = Path(__file__).resolve().parents[2]


def get_logger(name: str) -> logging.Logger:
    """Return the module logger. Call `setup_logging()` once in main() to configure."""
    return logging.getLogger(name)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for a CLI entry point.

    Logs go to stderr so the rich tables on stdout stay pipeable. `verbose`
    (or INVESTING_DEBUG) turns on DEBUG for *our* package only — the root stays
    at WARNING, since yfinance and its peewee/urllib3 dependencies emit a flood
    of DEBUG that buries anything useful. Unexpected errors are logged with
    `exc_info` at their call site, so they carry a traceback at any level.
    """
    verbose = verbose or bool(os.environ.get("INVESTING_DEBUG"))
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True,
                              show_path=False, show_time=False, markup=False)],
        force=True,
    )
    logging.getLogger(__package__).setLevel(logging.DEBUG if verbose else logging.INFO)


def load_sources(ticker: str) -> dict:
    path = REPO_ROOT / ticker / "SOURCES.yml"
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    # CIK may be loaded as an integer if unquoted; zero-pad to 10 digits.
    meta = data.get("_meta", {})
    if "cik" in meta:
        meta["cik"] = str(meta["cik"]).zfill(10)
    return data
