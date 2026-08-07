"""Reusable argument types for command-line interfaces."""

from __future__ import annotations

import argparse


def positive_int(value: str) -> int:
    """Parse an integer that must be greater than zero."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number
