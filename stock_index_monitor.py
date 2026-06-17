#!/usr/bin/env python3
"""Compatibility wrapper for the stock monitor command."""

from __future__ import annotations

import sys
from pathlib import Path


SRC_PATH = Path(__file__).resolve().parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from news_reader.stock_monitor import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
