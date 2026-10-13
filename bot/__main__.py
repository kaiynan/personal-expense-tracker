"""Titik masuk modul Telegram Bot (dijalankan via python -m bot)."""

from __future__ import annotations

import sys

from main import main

if __name__ == "__main__":
    sys.exit(main(["--bot"]))
