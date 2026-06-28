"""SQLite faylini Railway volume (/data) da saqlash."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def resolve_db_path(default_filename: str = "navbatchi.db") -> Path:
    data_dir = os.getenv("DATABASE_DIR", "").strip()
    if data_dir:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root / default_filename
    local = BASE_DIR / "data"
    local.mkdir(parents=True, exist_ok=True)
    return local / default_filename
