"""Shared logging setup for the bridge and web GUI."""

from __future__ import annotations

import logging
import os


def setup_logging(verbose: bool = False) -> None:
    # Under systemd the journal already timestamps every line.
    fmt = "%(levelname)s %(name)s: %(message)s"
    if "JOURNAL_STREAM" not in os.environ:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)
