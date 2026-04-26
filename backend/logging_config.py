"""Structured logging configuration for the endurance coaching backend.

Call ``configure_logging()`` once at startup (done in api/index.py).
All modules obtain a logger via ``logging.getLogger(__name__)``.

Log levels used across the codebase:
  DEBUG   — verbose per-field details useful during local development.
  INFO    — key lifecycle events (auth grants, profile saves, plan generation).
  WARNING — unexpected but recoverable situations (missing optional config,
             missing DB rows that are tolerated, deprecated paths).
  ERROR   — unhandled or partially-handled exceptions that affect a request.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(debug: bool = False) -> None:
    """Set up root logger with a structured-ish console format.

    In production (Vercel) stdout is captured by the platform. Each log line
    includes level, logger name, and message so filters can be applied.
    """
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if called more than once (e.g. during tests).
    if not root.handlers:
        root.addHandler(handler)

    # Quiet noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
