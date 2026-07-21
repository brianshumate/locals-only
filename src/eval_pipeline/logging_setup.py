"""Central logging configuration.

The pipeline's stages are long-running and often launched detached (a judge
run over a reasoning model can take an hour). Logging only to stderr means a
run started in one shell is invisible from every other. `configure_logging`
sends records to both stderr and a rotating file under `logs/`, so progress is
always tailable:

    tail -f logs/eval.log

`eval status` reads the database for a point-in-time snapshot; the log file is
the streaming counterpart.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "eval.log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(verbose: bool = False,
                      log_file: Path | str | None = None) -> Path:
    """Attach a stderr handler and a rotating file handler to the root logger.

    Idempotent: repeated calls (e.g. across subcommands in one process) do not
    stack duplicate handlers. Returns the path the file handler writes to.
    """
    level = logging.DEBUG if verbose else logging.INFO
    path = Path(log_file) if log_file else LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    have_stream = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    have_file = any(
        isinstance(h, logging.FileHandler)
        and Path(getattr(h, "baseFilename", "")) == path.resolve()
        for h in root.handlers
    )

    fmt = logging.Formatter(_FORMAT)
    if not have_stream:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
    if not have_file:
        file_handler = RotatingFileHandler(
            path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    for handler in root.handlers:
        handler.setLevel(level)

    # httpx logs one INFO line per request; the pipeline makes thousands, so
    # keep them out of the progress log unless we're debugging.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(
            logging.DEBUG if verbose else logging.WARNING)
    return path
